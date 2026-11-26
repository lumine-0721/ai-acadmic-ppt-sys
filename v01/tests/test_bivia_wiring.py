"""Tests for T2 (BiviaJudge wiring into C, E, H, and runner) + integration provider ordering."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from PIL import Image

from pptv01.bivia_judge import BiviaJudge
from pptv01.c_asset_requirements import RequirementRuleError, run_stage_c
from pptv01.c_asset_requirements.stage import build_requirements
from pptv01.llm import LLMOutputError
from pptv01.contracts import (
    AssetChecks,
    AssetRecord,
    AssetRegistry,
    AssetRequirement,
    AssetRequirements,
    AssetStatus,
    AssetTarget,
    AssetType,
    Priority,
    ProductionPlan,
    QAIssueKind,
    QAReport,
    RunOverall,
    SCHEMA_VERSION,
    Severity,
    SlideAssetMapping,
    SlidePlan,
    UserInputRequests,
    load_contract,
)
from pptv01.d_retrieve import services_from_env
from pptv01.e_mapping import run_stage_e
from pptv01.f_layout import run_stage_f
from pptv01.g_pptx import run_stage_g
from pptv01.h_qa import run_stage_h
from pptv01.retrieval import DisabledResearch
from pptv01.retrieval.provider import research_from_env
from pptv01.runner import run_pipeline
from tests.conftest import GOOD_A, GOOD_B, GOOD_C, GOOD_TH
from tests.fakes import FakeClient, FakeResearch
from tests.test_stage_d import Net, rp


def _test_render(pptx_path: Path, out_dir: Path) -> list[Path]:
    from PIL import ImageDraw
    from pptx import Presentation

    prs = Presentation(str(pptx_path))
    out_dir.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    for i in range(1, len(prs.slides) + 1):
        p = out_dir / f"slide_{i:03d}.png"
        im = Image.new("RGB", (320, 180), (245, 248, 252))
        ImageDraw.Draw(im).rectangle([10, 10, 100, 50], fill=(30, 64, 175))
        im.save(p, "PNG")
        out.append(p)
    return out


def _c_inputs():
    pp = ProductionPlan(run_id="t2-c", source_utterance="帮我做一个城市热岛效应的 PPT。", **GOOD_A)
    sp = SlidePlan(
        run_id="t2-c",
        status="provisional",
        narrative=GOOD_B["narrative"],
        pages=[{"page_id": f"S{i:03d}", "status": "provisional", **p} for i, p in enumerate(GOOD_B["pages"], 1)],
    )
    return pp, sp


def _judge_with(payload_or_fn, *, threshold: float = 0.90) -> BiviaJudge:
    if callable(payload_or_fn):
        handler = payload_or_fn
    else:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload_or_fn, request=request)
    return BiviaJudge("a8-secret-token-never-logged", threshold=threshold, transport=httpx.MockTransport(handler))


def _setup_eh_fixture(run_dir: Path, *, with_image: bool = True, with_ref: bool = True):
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "assets").mkdir(exist_ok=True)
    plan, splan = _c_inputs()
    plan = plan.model_copy(update={"run_id": "t2-eh"})
    splan = splan.model_copy(update={"run_id": "t2-eh", "pages": splan.pages[:2]})
    reqs: list[AssetRequirement] = []
    recs: list[AssetRecord] = []
    raw_map: list[dict] = []

    if with_image:
        img_rel = "assets/A001.png"
        img_path = run_dir / img_rel
        Image.new("RGB", (160, 100), color=(20, 120, 60)).save(img_path, format="PNG")
        img_sha = hashlib.sha256(img_path.read_bytes()).hexdigest()
        reqs.append(
            AssetRequirement(
                asset_id="A001",
                page_ids=[splan.pages[0].page_id],
                need_refs=[f"{splan.pages[0].page_id}#1"],
                type=AssetType.image,
                purpose="展示城区表面温度分布特征",
                targets=[AssetTarget(name="城区温度", entity_type="concept")],
                query_hints=["urban heat island surface temperature"],
                priority=Priority.required,
                acceptance=["来自开放许可来源（CC BY 4.0）且直接反映主题，须核实对象一致"],
            )
        )
        recs.append(
            AssetRecord(
                asset_id="A001",
                type=AssetType.image,
                status=AssetStatus.acquired,
                source_name="Wikimedia Commons",
                source_url="https://commons.wikimedia.org/wiki/File:UHI.png",
                license_or_terms="CC BY 4.0",
                author="Test Author",
                attribution="Test Author, CC BY 4.0",
                identifier="Urban heat island thermal map",
                file_path=img_rel,
                sha256=img_sha,
                mime="image/png",
                width=160,
                height=100,
                retrieved_at=datetime.now(timezone.utc),
                checks=AssetChecks(relevance="passed", usability="ok", format="PNG", quality="160x100"),
            )
        )
        raw_map.append({
            "page_id": splan.pages[0].page_id,
            "asset_id": "A001",
            "request_id": None,
            "usage": "置于封面右侧：城市热岛温度分布示例图",
            "accepted": True,
        })

    if with_ref:
        ref_rel = "assets/A002.json"
        (run_dir / ref_rel).write_text(
            json.dumps({"title": "Urban Heat Island Review (2024)", "doi": "10.1000/uhi"}, ensure_ascii=False),
            encoding="utf-8",
        )
        reqs.append(
            AssetRequirement(
                asset_id="A002",
                page_ids=[splan.pages[1].page_id],
                need_refs=[f"{splan.pages[1].page_id}#1"],
                type=AssetType.reference,
                purpose="为热岛观测口径差异提供文献出处",
                targets=[AssetTarget(name="城市热岛文献", entity_type="publication")],
                query_hints=["urban heat island air vs surface temperature"],
                priority=Priority.optional,
                acceptance=["须提供可核验来源链接或 DOI 且与本页论述直接相关，须核实出处"],
            )
        )
        recs.append(
            AssetRecord(
                asset_id="A002",
                type=AssetType.reference,
                status=AssetStatus.acquired,
                source_name="Crossref",
                source_url="https://doi.org/10.1000/uhi",
                license_or_terms="Crossref metadata",
                identifier="Urban Heat Island Review (2024) DOI:10.1000/uhi",
                file_path=ref_rel,
                retrieved_at=datetime.now(timezone.utc),
            )
        )
        raw_map.append({
            "page_id": splan.pages[1].page_id,
            "asset_id": "A002",
            "request_id": None,
            "usage": "置于页脚作为气温与地表温度差异的文献引用",
            "accepted": True,
        })

    req_obj = AssetRequirements(schema_version=SCHEMA_VERSION, run_id="t2-eh", requirements=reqs, dropped_needs=[])
    reg_obj = AssetRegistry(schema_version=SCHEMA_VERSION, run_id="t2-eh", assets=recs)
    uir_obj = UserInputRequests(schema_version=SCHEMA_VERSION, run_id="t2-eh", requests=[])
    e_client = FakeClient([{"mappings": raw_map, "unused_assets": []}])
    return plan, splan, req_obj, reg_obj, uir_obj, e_client


# ---------------------------------------------------------------------------
# Integration branch ordering
# ---------------------------------------------------------------------------
def test_integration_keenable_before_firecrawl_and_default_unchanged():
    assert [b.name for b in research_from_env({}).backends] == ["wikipedia", "crossref"]
    names = [
        b.name
        for b in research_from_env(
            {"PPTV01_KEENABLE": "1", "KEENABLE_API_KEY": "k", "PPTV01_FIRECRAWL": "1"}
        ).backends
    ]
    assert names == ["wikipedia", "crossref", "keenable", "firecrawl"]


# ---------------------------------------------------------------------------
# Rule 1: dual env switch & zero behavior change when disabled
# ---------------------------------------------------------------------------
def test_bivia_disabled_by_default_and_never_calls_http(monkeypatch, tmp_path):
    monkeypatch.delenv("PPTV01_BIVIA_JUDGE", raising=False)
    monkeypatch.delenv("A8_API_KEY", raising=False)
    assert BiviaJudge.from_env() is None
    assert BiviaJudge.from_env({"PPTV01_BIVIA_JUDGE": "1"}) is None
    assert BiviaJudge.from_env({"A8_API_KEY": "secret"}) is None

    pp, sp = _c_inputs()
    reqs_off, _ = run_stage_c(pp, sp, FakeClient([GOOD_C]), tmp_path / "c_off")
    reqs_explicit, _ = run_stage_c(pp, sp, FakeClient([GOOD_C]), tmp_path / "c_exp", bivia=None)
    assert reqs_off.model_dump() == reqs_explicit.model_dump()

    plan, splan, req_obj, reg_obj, uir_obj, e_client = _setup_eh_fixture(tmp_path / "e_off")
    mp, _ = run_stage_e(plan, splan, req_obj, reg_obj, uir_obj, e_client, tmp_path / "e_off")
    assert [m.relevance for m in mp.mappings] == ["not_assessed", "not_assessed"]
    assert all("bivia" not in m.relevance_note.lower() for m in mp.mappings)


def test_bivia_key_never_appears_in_run_artifacts(monkeypatch, tmp_path):
    secret = "a8-super-secret-value-999"
    monkeypatch.setenv("PPTV01_BIVIA_JUDGE", "1")
    monkeypatch.setenv("A8_API_KEY", secret)
    called = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called += 1
        assert request.headers["Authorization"] == f"Bearer {secret}"
        return httpx.Response(200, json={"value": "yes", "probability": 0.96, "status": "answered"}, request=request)

    judge = BiviaJudge.from_env(transport=httpx.MockTransport(handler))
    assert judge is not None
    assert secret not in repr(judge)

    sv = services_from_env(
        {"PPTV01_ASSETS": "auto", "PPTV01_RELEVANCE": "structural", "PPTV01_PIXEL_RULES": "off"},
        transport=httpx.MockTransport(Net()),
        openalex_fetch=lambda q, n: rp("openalex_uhi.json")[:n],
    )
    good_c = json.loads(json.dumps(GOOD_C))
    good_c["requirements"][0]["targets"] = ["帕特农神庙"]
    good_c["requirements"][0]["query_hints"] = ["Parthenon"]
    good_b = json.loads(json.dumps(GOOD_B))
    good_b["pages"][0]["asset_needs"][0]["targets"] = [{"name": "帕特农神庙", "entity_type": "building", "verification": "to_verify"}]
    e_resp = {
        "mappings": [{"page_id": "S001", "asset_id": "A001", "usage": "置于封面右侧作为城市热岛实景参考图", "accepted": True}],
        "unused_assets": [],
    }
    rep, run_dir = run_pipeline(
        "帮我做一个城市热岛效应的 PPT。",
        tmp_path,
        FakeClient([GOOD_A, good_b, good_c, e_resp, GOOD_TH]),
        run_id="key-check",
        research=DisabledResearch("offline"),
        assets=sv,
        bivia=judge,
        render_fn=_test_render,
    )
    assert called > 0
    assert rep.overall in (RunOverall.complete, RunOverall.complete_with_placeholders)
    assert rep.tool_config["bivia_judge"]["enabled"] is True
    assert rep.stages[2].substeps == {"bivia_judge": "done"}
    for json_file in run_dir.rglob("*.json"):
        assert secret not in json_file.read_text(encoding="utf-8", errors="ignore")


# ---------------------------------------------------------------------------
# Rule 2 & 3: C stage pass / veto / uncertain / fail-open
# ---------------------------------------------------------------------------
def test_stage_c_bivia_veto_uncertain_and_fail_open(tmp_path):
    pp, sp = _c_inputs()

    # Pass >= 0.90 leaves acceptance untouched
    j_pass = _judge_with({"value": "yes", "probability": 0.93, "status": "answered"})
    reqs_pass, _ = run_stage_c(pp, sp, FakeClient([GOOD_C]), tmp_path / "pass", bivia=j_pass)
    assert all("Bivia" not in a for r in reqs_pass.requirements for a in r.acceptance)

    # Veto >= 0.90 rejects (build_requirements raises RequirementRuleError; run_stage_c exhausts retry -> LLMOutputError)
    j_veto = _judge_with({"value": "no", "probability": 0.91, "status": "answered"})
    with pytest.raises(RequirementRuleError, match="bivia veto"):
        build_requirements(GOOD_C, pp, sp, bivia=j_veto)
    with pytest.raises(LLMOutputError, match="bivia veto"):
        run_stage_c(pp, sp, FakeClient([GOOD_C, GOOD_C]), tmp_path / "veto", bivia=j_veto)

    # Abstain and low confidence (< 0.90) NEVER count as pass -> annotate 待补充 / needs_review
    for idx, body in enumerate((
        {"value": None, "probability": None, "status": "abstained", "abstain": True},
        {"value": "yes", "probability": 0.89, "status": "answered"},
    )):
        j_unc = _judge_with(body)
        reqs_unc, _ = run_stage_c(pp, sp, FakeClient([GOOD_C]), tmp_path / f"unc_{idx}", bivia=j_unc)
        for r in reqs_unc.requirements:
            assert any("待补充" in a and "needs_review" in a for a in r.acceptance)

    # Network failure -> fail-open (not_run), never raises
    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection reset", request=request)

    stats = {"pass": 0, "not_vetoed": 0, "veto": 0, "uncertain": 0, "not_run": 0}
    reqs_err, _ = run_stage_c(pp, sp, FakeClient([GOOD_C]), tmp_path / "err", bivia=_judge_with(broken), bivia_stats_out=stats)
    assert len(reqs_err.requirements) == len(reqs_pass.requirements)
    assert stats["not_run"] == len(reqs_pass.requirements)


# ---------------------------------------------------------------------------
# Rule 2, 3 & 4: Stage E image veto-only + abstain/low-conf -> 待补充
# ---------------------------------------------------------------------------
def test_stage_e_image_veto_only_yes_is_not_proof(tmp_path):
    plan, splan, req_obj, reg_obj, uir_obj, e_client = _setup_eh_fixture(tmp_path / "yes_not_proof", with_ref=False)
    j_yes = _judge_with({"value": "yes", "probability": 0.99, "status": "answered"})
    mp, _ = run_stage_e(plan, splan, req_obj, reg_obj, uir_obj, e_client, tmp_path / "yes_not_proof", bivia=j_yes)
    assert len(mp.mappings) == 1
    assert mp.mappings[0].accepted is True
    # Must NOT become "relevant" solely from Bivia image yes
    assert mp.mappings[0].relevance == "not_assessed"
    assert "not_vetoed" in mp.mappings[0].relevance_note
    assert "not proof" in mp.mappings[0].relevance_note


def test_stage_e_veto_and_uncertain_trigger_unused_and_placeholder(tmp_path):
    # Veto (no >= 0.90) rejects raster image into unused_assets
    plan, splan, req_obj, reg_obj, uir_obj, e_client = _setup_eh_fixture(tmp_path / "veto", with_ref=False)
    j_veto = _judge_with({"value": "no", "probability": 0.95, "status": "answered"})
    mp_v, _ = run_stage_e(plan, splan, req_obj, reg_obj, uir_obj, e_client, tmp_path / "veto", bivia=j_veto)
    assert mp_v.mappings[0].accepted is False
    assert mp_v.mappings[0].relevance == "irrelevant"
    assert "bivia veto" in mp_v.mappings[0].relevance_note
    assert any(u.asset_id == "A001" for u in mp_v.unused_assets)

    # Low confidence (0.85 < 0.90) & abstain reject into unused_assets with 待补充 (never pass)
    for tag, body in (
        ("lowconf", {"value": "yes", "probability": 0.85, "status": "answered"}),
        ("abstain", {"value": None, "probability": None, "status": "abstained", "abstain": True}),
    ):
        plan, splan, req_obj, reg_obj, uir_obj, e_client = _setup_eh_fixture(tmp_path / tag, with_ref=False)
        j_u = _judge_with(body)
        mp_u, _ = run_stage_e(plan, splan, req_obj, reg_obj, uir_obj, e_client, tmp_path / tag, bivia=j_u)
        assert mp_u.mappings[0].accepted is False
        assert mp_u.mappings[0].relevance == "not_assessed"
        assert "待补充" in mp_u.mappings[0].relevance_note
        assert any(u.asset_id == "A001" and "待补充" in u.reason for u in mp_u.unused_assets)


def test_stage_e_reference_boolean_pass_veto_and_fail_open(tmp_path, monkeypatch):
    monkeypatch.setattr("pptv01.bivia_judge._sleep", lambda _: None)
    plan, splan, req_obj, reg_obj, uir_obj, e_client = _setup_eh_fixture(tmp_path / "ref_ok", with_image=False, with_ref=True)
    mp_ok, _ = run_stage_e(
        plan, splan, req_obj, reg_obj, uir_obj, e_client, tmp_path / "ref_ok",
        bivia=_judge_with({"value": "yes", "probability": 0.94, "status": "answered"}),
    )
    assert mp_ok.mappings[0].accepted is True
    assert mp_ok.mappings[0].relevance == "relevant"

    plan, splan, req_obj, reg_obj, uir_obj, e_client = _setup_eh_fixture(tmp_path / "ref_500", with_image=False, with_ref=True)
    stats = {"pass": 0, "not_vetoed": 0, "veto": 0, "uncertain": 0, "not_run": 0}
    mp_err, _ = run_stage_e(
        plan, splan, req_obj, reg_obj, uir_obj, e_client, tmp_path / "ref_500",
        bivia=BiviaJudge(
            "k",
            transport=httpx.MockTransport(lambda req: httpx.Response(503, json={}, request=req)),
        ),
        bivia_stats_out=stats,
    )
    assert mp_err.mappings[0].accepted is True
    assert "bivia: not_run" in mp_err.mappings[0].relevance_note
    assert stats["not_run"] == 1


# ---------------------------------------------------------------------------
# Rule 2-5: Stage H & full runner fail-open + veto + uncertain
# ---------------------------------------------------------------------------
def test_stage_h_bivia_veto_uncertain_yes_not_proof_and_fail_open(tmp_path, monkeypatch):
    monkeypatch.setattr("pptv01.bivia_judge._sleep", lambda _: None)
    run_dir = tmp_path / "h_run"
    plan, splan, req_obj, reg_obj, uir_obj, e_client = _setup_eh_fixture(run_dir)
    mp, _ = run_stage_e(plan, splan, req_obj, reg_obj, uir_obj, e_client, run_dir)
    design = run_stage_f(plan, splan, reg_obj, uir_obj, mp, run_dir, theme_id="academic-default")
    pptx_path = run_stage_g(splan, design, reg_obj, uir_obj, run_dir)

    # Veto on picture & reference -> emits content_asset_mismatch major issues
    rep_veto, _ = run_stage_h(
        pptx_path, splan, run_dir,
        design=design, reg=reg_obj, uir=uir_obj, mapping=mp,
        render_fn=_test_render,
        bivia=_judge_with({"value": "no", "probability": 0.95, "status": "answered"}),
    )
    bivia_issues = [i for i in rep_veto.issues if "bivia veto" in i.description]
    assert len(bivia_issues) >= 1
    assert all(i.kind is QAIssueKind.content_asset_mismatch and i.severity is Severity.major for i in bivia_issues)

    # Abstain / low confidence -> emits minor 待补充 needs_review issues (never silent pass)
    rep_unc, _ = run_stage_h(
        pptx_path, splan, run_dir,
        design=design, reg=reg_obj, uir=uir_obj, mapping=mp,
        render_fn=_test_render,
        bivia=_judge_with({"value": None, "probability": None, "status": "abstained", "abstain": True}),
    )
    unc_issues = [i for i in rep_unc.issues if "bivia uncertain" in i.description and "待补充" in i.description]
    assert len(unc_issues) >= 1
    assert all(i.severity is Severity.minor for i in unc_issues)

    # High-confidence yes never suppresses deterministic mismatch issues
    bad_mp = mp.model_copy(
        update={
            "mappings": [
                m.model_copy(update={"relevance": "irrelevant"}) if m.asset_id == "A001" else m
                for m in mp.mappings
            ]
        }
    )
    rep_det, _ = run_stage_h(
        pptx_path, splan, run_dir,
        design=design, reg=reg_obj, uir=uir_obj, mapping=bad_mp,
        render_fn=_test_render,
        bivia=_judge_with({"value": "yes", "probability": 0.99, "status": "answered"}),
    )
    assert any("irrelevant" in i.description for i in rep_det.issues)

    # Fail-open on network errors -> H still succeeds and records bivia_judge: not_run
    rep_err, _ = run_stage_h(
        pptx_path, splan, run_dir,
        design=design, reg=reg_obj, uir=uir_obj, mapping=mp,
        render_fn=_test_render,
        bivia=_judge_with(lambda req: (_ for _ in ()).throw(httpx.ConnectError("offline", request=req))),
    )
    assert "bivia_judge: not_run" in rep_err.diagnostics


def test_runner_fail_open_end_to_end_when_bivia_returns_500(monkeypatch, tmp_path):
    monkeypatch.setattr("pptv01.bivia_judge._sleep", lambda _: None)
    monkeypatch.setenv("PPTV01_BIVIA_JUDGE", "1")
    monkeypatch.setenv("A8_API_KEY", "test-k")

    judge = BiviaJudge(
        "test-k",
        transport=httpx.MockTransport(lambda req: httpx.Response(500, json={"error": "boom"}, request=req)),
    )
    sv = services_from_env(
        {"PPTV01_ASSETS": "auto", "PPTV01_RELEVANCE": "structural", "PPTV01_PIXEL_RULES": "off"},
        transport=httpx.MockTransport(Net()),
        openalex_fetch=lambda q, n: rp("openalex_uhi.json")[:n],
    )
    good_c = json.loads(json.dumps(GOOD_C))
    good_c["requirements"][0]["targets"] = ["帕特农神庙"]
    good_c["requirements"][0]["query_hints"] = ["Parthenon"]
    good_b = json.loads(json.dumps(GOOD_B))
    good_b["pages"][0]["asset_needs"][0]["targets"] = [{"name": "帕特农神庙", "entity_type": "building", "verification": "to_verify"}]
    mapping_payload = {
        "mappings": [
            {
                "page_id": "S001",
                "asset_id": "A001",
                "request_id": None,
                "usage": "置于封面右侧作为城市热岛实景背景参考图",
                "accepted": True,
            }
        ],
        "unused_assets": [],
    }
    client = FakeClient([GOOD_A, good_b, good_c, mapping_payload, GOOD_TH])
    rep, run_dir = run_pipeline(
        "帮我做一个城市热岛效应的 PPT。",
        tmp_path,
        client,
        run_id="bivia-fail-open",
        research=FakeResearch([("城市热岛", "城市热岛效应表现为城区夜间升温高于郊区（3℃）。")]),
        assets=sv,
        bivia=judge,
        render_fn=_test_render,
    )
    assert rep.overall in (RunOverall.complete, RunOverall.complete_with_placeholders)
    assert rep.tool_config["bivia_judge"]["enabled"] is True
    assert rep.tool_config["bivia_judge"]["status"] == "not_run"
    for stage_code in ("C", "E", "H"):
        st = next(s for s in rep.stages if s.stage == stage_code)
        assert st.substeps == {"bivia_judge": "not_run"}
    qa = load_contract(QAReport, run_dir / "qa_report.json")
    assert "bivia_judge: not_run" in qa.diagnostics
