"""Regression tests for HANDOFF v2 R0 bugfixes: B1 (stale --resume), B2 (content_summary leak), B3 (self-draw wording)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from PIL import Image, ImageDraw
from pptx import Presentation

from pptv01 import cli
from pptv01.c_asset_requirements.validate import validate_requirements
from pptv01.contracts import (
    AssetRegistry,
    AssetRequirements,
    AssetStatus,
    AssetType,
    ElementKind,
    PageDesign,
    ProductionPlan,
    RunReport,
    SlideAssetMapping,
    SlidePlan,
    StageStatus,
    UserInputRequests,
    UserRequestStatus,
    load_contract,
)
from pptv01.d_retrieve import run_stage_d
from pptv01.f_layout import run_stage_f
from pptv01.f_layout.validate import validate_page_design
from pptv01.g_pptx import run_stage_g
from pptv01.runner import run_pipeline
from tests.conftest import GOOD_A, GOOD_B, GOOD_C, GOOD_TH
from tests.fakes import FakeClient
from tests.test_stage_d import Net, make, req

U = "帮我做一个城市热岛效应的 PPT。"
SELF_DRAW_PAT = re.compile(r"自绘|自行绘制|drawn\s+natively|draw\s+natively", re.I)


def _test_render(pptx_path: Path, out_dir: Path) -> list[Path]:
    prs = Presentation(str(pptx_path))
    out_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for i in range(1, len(prs.slides) + 1):
        p = out_dir / f"slide_{i:03d}.png"
        im = Image.new("RGB", (320, 180), (245, 248, 252))
        ImageDraw.Draw(im).rectangle([10, 10, 100, 50], fill=(30, 64, 175))
        im.save(p, "PNG")
        out.append(p)
    return out


# ------------------------------------------------------------------ B1: provide -> --resume must invalidate E-H
def test_b1_provide_then_resume_replaces_placeholder_with_user_input(tmp_path):
    # Initial run completes A->H with placeholders (PPTV01_ASSETS=none from conftest)
    rep1, run_dir = run_pipeline(
        U, tmp_path, FakeClient([GOOD_A, GOOD_B, GOOD_C, GOOD_TH]), run_id="b1-run", render_fn=_test_render
    )
    assert rep1.overall.value == "complete_with_placeholders"
    uir1 = load_contract(UserInputRequests, run_dir / "user_input_requests.json")
    diag_req = next(r for r in uir1.requests if r.kind.value == "diagram")
    assert diag_req.status is UserRequestStatus.placeholder_rendered

    prs1 = Presentation(str(run_dir / "deck.pptx"))
    names1 = [sh.name for sl in prs1.slides for sh in sl.shapes]
    assert any(n.startswith(f"PLACEHOLDER_{diag_req.request_id}") for n in names1)

    # User provides a structured diagram JSON for that request
    flow = tmp_path / "flow.json"
    flow.write_text(
        json.dumps(
            {
                "nodes": [
                    {"id": "n1", "label": "城市硬质铺装", "source": "R001"},
                    {"id": "n2", "label": "热容升高", "source": "R001"},
                    {"id": "n3", "label": "夜间升温", "source": "R001"},
                ],
                "edges": [
                    {"from": "n1", "to": "n2", "label": "蓄热", "source": "R001"},
                    {"from": "n2", "to": "n3", "label": "释放", "source": "R001"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    rc = cli.main(
        [
            "provide",
            str(run_dir),
            diag_req.request_id,
            "--file",
            str(flow),
            "--source",
            "Oke 1982",
            "--license",
            "CC BY 4.0",
        ]
    )
    assert rc == 0

    # run_report.json must mark E-H as stale right after provide
    rep_after_provide = load_contract(RunReport, run_dir / "run_report.json")
    st_after = {s.stage: s for s in rep_after_provide.stages}
    for code in ("E", "F", "G", "H"):
        assert st_after[code].stale is True, f"stage {code} must be marked stale after provide"
    for code in ("A", "B", "C", "D"):
        assert st_after[code].stale is False

    # Resume with an E response that maps the newly provided request
    e_resp = {
        "mappings": [
            {
                "page_id": diag_req.page_ids[0],
                "asset_id": diag_req.asset_id,
                "request_id": diag_req.request_id,
                "usage": "在成因页用用户提供的结构化流程图展示城市热岛形成机制",
                "accepted": True,
            }
        ],
        "unused_assets": [],
    }
    client2 = FakeClient([e_resp, GOOD_TH])
    rep2, _ = run_pipeline(U, tmp_path, client2, run_id="b1-run", resume=True, render_fn=_test_render)
    assert rep2.overall.value in ("complete", "complete_with_placeholders")
    assert len(client2.calls) == 2  # E + TH re-ran after provide invalidated E-H

    # Deck must now contain the provided diagram element and NOT the old placeholder
    prs2 = Presentation(str(run_dir / "deck.pptx"))
    names2 = [sh.name for sl in prs2.slides for sh in sl.shapes]
    assert not any(n.startswith(f"PLACEHOLDER_{diag_req.request_id}") for n in names2), names2
    assert any(diag_req.request_id in n and not n.startswith("PLACEHOLDER_") for n in names2), names2

    # A clean --resume with no changes must reuse E-H without calling the LLM again
    client3 = FakeClient([])
    rep3, _ = run_pipeline(U, tmp_path, client3, run_id="b1-run", resume=True, render_fn=_test_render)
    assert len(client3.calls) == 0
    assert all(s.status is StageStatus.done and not s.stale for s in rep3.stages)


def test_b1_upstream_file_hash_mismatch_invalidates_downstream(tmp_path):
    rep1, run_dir = run_pipeline(
        U, tmp_path, FakeClient([GOOD_A, GOOD_B, GOOD_C, GOOD_TH]), run_id="b1-hash", render_fn=_test_render
    )
    st1 = {s.stage: s for s in rep1.stages}
    assert "slide_plan.json" in st1["F"].input_hashes

    # Modify page_design.json directly -> G and H must re-run on resume
    design = load_contract(PageDesign, run_dir / "page_design.json")
    title_el = next(e for e in design.pages[0].elements if e.kind is ElementKind.title)
    title_el.text = (title_el.text or "") + "（手动修订）"
    design.pages[0].notes = (design.pages[0].notes + "; manual tweak").strip("; ")
    (run_dir / "page_design.json").write_text(design.model_dump_json(indent=2) + "\n", encoding="utf-8")

    old_deck_mtime = (run_dir / "deck.pptx").stat().st_mtime_ns
    # Ensure G rewrites deck.pptx and rendered slide text reflects the edit
    client2 = FakeClient([])
    rep2, _ = run_pipeline(U, tmp_path, client2, run_id="b1-hash", resume=True, render_fn=_test_render)
    assert len(client2.calls) == 0
    prs = Presentation(str(run_dir / "deck.pptx"))
    slide0_text = "\n".join(sh.text_frame.text for sh in prs.slides[0].shapes if sh.has_text_frame)
    assert "（手动修订）" in slide0_text
    assert rep2.stages[6].input_hashes["page_design.json"] != st1["G"].input_hashes["page_design.json"]
    assert (run_dir / "deck.pptx").stat().st_mtime_ns >= old_deck_mtime


# ------------------------------------------------------------------ B2: content_summary must not leak onto slide body
def test_b2_content_summary_never_rendered_on_slide_only_in_notes(tmp_path):
    director_prompt_1 = "用一个简洁的图示说明概念，文字只保留最核心的一句"
    director_prompt_2 = "用开场提出观看建筑的几个角度，并交代后面怎么组织"
    sp = SlidePlan.model_validate(
        {
            "run_id": "b2-run",
            "status": "provisional",
            "narrative": "先定义再讲分析角度。",
            "pages": [
                {
                    "page_id": "S001",
                    "title": "什么是城市热岛效应",
                    "page_role": "content",
                    "headline": "城区气温长期高于周边郊区，是城市热岛效应的核心特征。",
                    "goal": "给出核心定义",
                    "key_points": ["城区气温高于周边郊区", "地表与人类活动共同作用"],
                    "content_summary": director_prompt_1,
                    "speaker_notes": "本页我们先给出城市热岛的基本判断。与临时性的天气热浪不同，这种城郊温差由地表特征与人工排放长期造成。",
                    "visual_intent": "对比图",
                    "asset_needs": [],
                    "status": "provisional",
                },
                {
                    "page_id": "S002",
                    "title": "观看建筑的几个角度",
                    "page_role": "content",
                    "headline": "材料构建与空间光影决定了人类使用与感知建筑的基本方式。",
                    "goal": "介绍分析框架",
                    "key_points": ["空间结构与材料", "光影与使用方式"],
                    "content_summary": director_prompt_2,
                    "speaker_notes": "从热环境进一步延伸到建筑观察维度时，建议结合物质材料与实际使用体验两条主线来看待问题。",
                    "visual_intent": "架构图",
                    "asset_needs": [],
                    "status": "provisional",
                },
            ],
        }
    )
    prod = ProductionPlan.model_validate({"run_id": "b2-run", "source_utterance": U, **GOOD_A})
    reg = AssetRegistry(run_id="b2-run", assets=[], sources=[], summary="0/0")
    mapping = SlideAssetMapping(run_id="b2-run", mappings=[], unused_assets=[])
    uir = UserInputRequests(run_id="b2-run", requests=[], summary="0 open (0 blocking)")

    design = run_stage_f(prod, sp, reg, uir, mapping, tmp_path)
    deck_path = run_stage_g(sp, design, reg, uir, tmp_path)
    prs = Presentation(str(deck_path))

    for sp_page, d_page, slide in zip(sp.pages, design.pages, prs.slides):
        allowed_lines = {sp_page.title.strip()} | {kp.strip() for kp in sp_page.key_points}
        if getattr(sp_page, "headline", None):
            allowed_lines.add(sp_page.headline.strip())
        visible_texts = [
            el.text.strip()
            for el in d_page.elements
            if el.kind is not ElementKind.placeholder and el.text and el.text.strip()
        ]
        # No visible text may contain content_summary
        for txt in visible_texts:
            assert sp_page.content_summary not in txt
            for line in txt.splitlines():
                clean = line.lstrip("•").strip()
                if not clean:
                    continue
                # Every visible line must come from title/headline, key_points, or attribution/source footer/caption
                is_attr = clean.startswith(("图源：", "数据来源：", "[A", "[U", "来源"))
                assert clean in allowed_lines or is_attr, f"unexpected visible text on {d_page.page_id}: {clean!r}"

        # Also verify the PPTX shapes themselves do not contain content_summary
        slide_text = "\n".join(sh.text_frame.text for sh in slide.shapes if sh.has_text_frame)
        assert sp_page.content_summary not in slide_text

        # PPTX notes preserve spoken speaker_notes, NOT planning content_summary
        speaker_notes = slide.notes_slide.notes_text_frame.text
        assert sp_page.speaker_notes in speaker_notes
        assert sp_page.content_summary not in speaker_notes

    # Validator must catch content_summary if manually injected as a visible body element
    bad_design = PageDesign.model_validate(design.model_dump())
    bad_design.pages[0].elements.append(
        design.pages[0].elements[2].model_copy(
            update={"element_id": "summary", "kind": ElementKind.body, "text": director_prompt_1, "y_in": 5.0, "h_in": 1.0}
        )
    )
    problems = validate_page_design(bad_design, sp, prod, mapping, reg, uir, tmp_path)
    assert any("content_summary" in p for p in problems), problems


# ------------------------------------------------------------------ B3: no "自绘" / "drawn natively" in D or C
def test_b3_stage_d_and_uir_contain_no_self_draw_wording(tmp_path):
    reqs = [
        req("A001", "diagram", hints=("d",)),
        req("A002", "icon", hints=("i",), priority="optional"),
        req("A003", "chart_data", [("热岛数据", "dataset", "to_verify")], hints=("c",)),
        req("A004", "statistic", [("温差数值", "dataset", "to_verify")], hints=("s",)),
        req("A005", "table", [("对比表", "concept", "to_verify")], hints=("t",)),
    ]
    net = Net(wd_search=None, commons_search={"query": {"pages": []}})
    plan, sp, rq, sv, _ = make(tmp_path, reqs, net=net, oa_fetch=lambda q, n: [])
    reg = run_stage_d(plan, sp, rq, tmp_path, sv)
    uir = load_contract(UserInputRequests, tmp_path / "user_input_requests.json")

    for a in reg.assets:
        assert not SELF_DRAW_PAT.search(a.notes), f"asset {a.asset_id} notes still mention self-drawing: {a.notes!r}"
    for r in uir.requests:
        assert not SELF_DRAW_PAT.search(r.why), f"request {r.request_id} why mentions self-drawing: {r.why!r}"
        for acc in r.acceptance:
            assert not SELF_DRAW_PAT.search(acc), f"request {r.request_id} acceptance mentions self-drawing: {acc!r}"

    diag_uir = next(r for r in uir.requests if r.kind.value == "diagram")
    assert "不允许凭空绘制" in diag_uir.why


def test_b3_stage_c_validator_rejects_self_draw_acceptance():
    prod = ProductionPlan.model_validate({"run_id": "b3-c", "source_utterance": U, **GOOD_A})
    sp = SlidePlan.model_validate(
        {
            "run_id": "b3-c",
            "status": "provisional",
            "narrative": "讲清成因。",
            "pages": [
                {
                    "page_id": "S001",
                    "title": "热岛成因",
                    "goal": "说明成因",
                    "key_points": ["地表吸热"],
                    "content_summary": "讲清成因",
                    "visual_intent": "流程图",
                    "asset_needs": [{"type": "diagram", "description": "成因机制图", "targets": []}],
                    "status": "provisional",
                }
            ],
        }
    )
    bad_reqs = AssetRequirements.model_validate(
        {
            "run_id": "b3-c",
            "requirements": [
                {
                    "asset_id": "A001",
                    "page_ids": ["S001"],
                    "need_refs": ["S001#1"],
                    "type": "diagram",
                    "purpose": "展示三类成因如何抬高城区气温",
                    "targets": [],
                    "query_hints": ["urban heat island diagram"],
                    "priority": "required",
                    "acceptance": ["与三类成因直接对应", "若取用现成图须许可明确；否则自绘并说明"],
                }
            ],
            "dropped_needs": [],
        }
    )
    problems = validate_requirements(bad_reqs, sp, prod)
    assert any("自绘" in p or "self-draw" in p.lower() or "fabricat" in p.lower() for p in problems), problems
