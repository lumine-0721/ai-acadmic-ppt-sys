"""Tests for user_input_requests.json, requests/provide CLI, --on-missing wait|placeholder,
RunReport overall rules, and no_fabricated_visuals / placeholders_marked hard gates."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image
from pptx import Presentation
from pptx.util import Inches
from pydantic import ValidationError

from pptv01 import cli
from pptv01.contracts import (
    AssetRegistry,
    AssetRequirements,
    ProductionPlan,
    RunOverall,
    RunReport,
    SlidePlan,
    UserInputRequests,
    UserRequestStatus,
    load_contract,
    write_contract,
)
from pptv01.d_retrieve import run_stage_d
from pptv01.grading.gates import check_gates
from pptv01.runner import run_pipeline
from tests.conftest import GOOD_A, GOOD_B, GOOD_C
from tests.fakes import FakeClient
from tests.test_stage_d import Net, make, req

RID = "20260101-000000-abcd"
U = "帮我做一个城市热岛效应的 PPT。"


def _png_bytes(w=32, h=32) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (w, h), (10, 120, 200)).save(b, "PNG")
    return b.getvalue()


def _make_run_with_all_missing_kinds(tmp_path: Path, *, on_missing="placeholder"):
    reqs = [
        req("A001", "diagram", hints=("d",)),
        req("A002", "chart_data", [("热岛数据", "dataset", "to_verify")], hints=("c",)),
        req("A003", "statistic", [("温差数值", "dataset", "to_verify")], hints=("s",)),
        req("A004", "table", [("对比表", "concept", "to_verify")], hints=("t",)),
        req("A005", "icon", hints=("i",), priority="optional"),
        req("A006", "image", [("不存在的建筑", "building", "to_verify")], hints=("zzz",)),
        req("A007", "reference", [("不存在的论文", "publication", "to_verify")], hints=("zzz",)),
    ]
    net = Net(wd_search=None, commons_search={"query": {"pages": []}})
    plan, sp, rq, sv, _ = make(tmp_path, reqs, net=net, oa_fetch=lambda q, n: [])
    reg = run_stage_d(plan, sp, rq, tmp_path, sv, on_missing=on_missing)
    return reg, load_contract(UserInputRequests, tmp_path / "user_input_requests.json")


# ---------------------------------------------------------------- ① contract + ② deterministic D output
def test_stage_d_deterministically_emits_user_input_requests_for_every_missing_kind(tmp_path):
    reg, uir = _make_run_with_all_missing_kinds(tmp_path)
    assert all(a.status.value == "missing" for a in reg.assets)
    kinds = [(r.asset_id, r.kind.value, r.blocking) for r in uir.requests]
    assert kinds == [
        ("A001", "diagram", True),
        ("A002", "chart", True),
        ("A003", "statistic", True),
        ("A004", "table", True),
        ("A005", "icon", False),
        ("A006", "image", True),
        ("A007", "reference", True),
    ]
    assert uir.requests[0].request_id == "U001" and uir.requests[-1].request_id == "U007"
    assert "7 open (6 blocking)" in uir.summary


def test_user_input_requests_contract_rules(fx):
    d = fx("user_input_requests")
    uir = UserInputRequests.model_validate(d)
    assert uir.requests[0].request_id == "U001"
    # status=provided requires provided payload
    bad = json.loads(json.dumps(d))
    bad["requests"][0]["status"] = "provided"
    with pytest.raises(ValidationError, match="requires provided"):
        UserInputRequests.model_validate(bad)
    # status=open cannot carry provided payload
    bad2 = json.loads(json.dumps(d))
    bad2["requests"][0]["provided"] = {
        "file": "inputs/U001.json",
        "sha256": "a" * 64,
        "source": "IPCC",
        "license": "CC BY 4.0",
    }
    with pytest.raises(ValidationError, match="cannot carry provided"):
        UserInputRequests.model_validate(bad2)
    # absolute path rejected
    bad2["requests"][0]["status"] = "provided"
    bad2["requests"][0]["provided"]["file"] = "/etc/passwd"
    with pytest.raises(ValidationError):
        UserInputRequests.model_validate(bad2)


# ---------------------------------------------------------------- ④ RunReport overall rules
def test_run_report_overall_rules_for_needs_input_and_placeholders(fx):
    base = fx("run_report")
    all_done = [{**s, "status": "done", "started_at": base["started_at"], "finished_at": base["finished_at"]}
                for s in base["stages"]]
    # complete is forbidden when open or placeholder requests exist
    with pytest.raises(ValidationError, match="complete is not allowed"):
        RunReport.model_validate({
            **base,
            "overall": "complete",
            "stages": all_done,
            "user_input_requests": {"path": "user_input_requests.json", "open_count": 1, "blocking_open_count": 1},
        })
    with pytest.raises(ValidationError, match="complete is not allowed"):
        RunReport.model_validate({
            **base,
            "overall": "complete",
            "stages": all_done,
            "user_input_requests": {"path": "user_input_requests.json", "open_count": 0, "placeholder_count": 2},
        })
    # complete_with_placeholders is valid when all done, 0 open, >=1 placeholder
    ok_ph = RunReport.model_validate({
        **base,
        "overall": "complete_with_placeholders",
        "stages": all_done,
        "user_input_requests": {"path": "user_input_requests.json", "open_count": 0, "placeholder_count": 2},
    })
    assert ok_ph.overall is RunOverall.complete_with_placeholders
    # needs_input requires open_count > 0
    with pytest.raises(ValidationError, match="open user_input_requests"):
        RunReport.model_validate({
            **base,
            "overall": "needs_input",
            "user_input_requests": {"path": "user_input_requests.json", "open_count": 0},
        })


# ---------------------------------------------------------------- ③ CLI: --on-missing wait (exit 4), requests, provide, --resume
def test_on_missing_wait_exits_4_then_provide_and_resume(tmp_path, monkeypatch, capsys):
    # Prepare agent responses or patch client_from_env with FakeClient for CLI test
    monkeypatch.setenv("PPTV01_LLM_BACKEND", "agent")
    io_dir = tmp_path / "io"
    io_dir.mkdir()
    monkeypatch.setenv("PPTV01_AGENT_RESPONSES_DIR", str(io_dir))
    (io_dir / "A_response_1.json").write_text(json.dumps(GOOD_A, ensure_ascii=False), encoding="utf-8")
    (io_dir / "B_response_1.json").write_text(json.dumps(GOOD_B, ensure_ascii=False), encoding="utf-8")
    (io_dir / "C_response_1.json").write_text(json.dumps(GOOD_C, ensure_ascii=False), encoding="utf-8")

    runs_root = tmp_path / "runs"
    rc = cli.main(["run", U, "--out", str(runs_root), "--run-id", "w1", "--on-missing", "wait"])
    assert rc == 4
    run_dir = runs_root / "w1"
    rep = load_contract(RunReport, run_dir / "run_report.json")
    assert rep.overall is RunOverall.needs_input
    assert rep.user_input_requests is not None and rep.user_input_requests.open_count >= 1
    assert {s.stage: s.status.value for s in rep.stages}["E"] == "skipped"

    # pptv01 requests lists open requests
    assert cli.main(["requests", str(run_dir)]) == 0
    out = capsys.readouterr().out
    assert "U001" in out and "[open]" in out

    # pptv01 provide supplies the first open request without overwriting asset_registry.json missing record
    uir_before = load_contract(UserInputRequests, run_dir / "user_input_requests.json")
    first_req = uir_before.requests[0]
    if first_req.kind.value in ("image", "icon", "diagram"):
        supply_file = tmp_path / "supply.png"
        supply_file.write_bytes(_png_bytes(1280, 720))
        expected_rel = f"inputs/{first_req.request_id}.png"
    else:
        supply_file = tmp_path / "supply.json"
        supply_file.write_text(json.dumps({
            "nodes": [{"id": "n1", "label": "城市地表", "source": "R001"}],
            "edges": [{"from": "n1", "to": "n1", "label": "升温", "source": "R001"}],
        }, ensure_ascii=False), encoding="utf-8")
        expected_rel = f"inputs/{first_req.request_id}.json"
    reg_before = (run_dir / "asset_registry.json").read_text("utf-8")
    rc_p = cli.main([
        "provide", str(run_dir), first_req.request_id,
        "--file", str(supply_file),
        "--source", "R001 Oke 1982",
        "--license", "CC BY 4.0",
    ])
    assert rc_p == 0
    assert (run_dir / "asset_registry.json").read_text("utf-8") == reg_before
    uir = load_contract(UserInputRequests, run_dir / "user_input_requests.json")
    assert uir.requests[0].status is UserRequestStatus.provided
    assert uir.requests[0].provided is not None and uir.requests[0].provided.file == expected_rel
    assert (run_dir / expected_rel).is_file()

    # --resume preserves provided inputs and switches to placeholder when requested
    rc2 = cli.main(["run", U, "--out", str(runs_root), "--run-id", "w1", "--resume", "--on-missing", "placeholder"])
    assert rc2 == 0
    uir_after = load_contract(UserInputRequests, run_dir / "user_input_requests.json")
    assert uir_after.requests[0].status is UserRequestStatus.provided
    rep2 = load_contract(RunReport, run_dir / "run_report.json")
    assert rep2.user_input_requests.provided_count == 1


# ---------------------------------------------------------------- ⑥ reject invalid user-provided files
def test_provide_rejects_missing_provenance_and_type_mismatches(tmp_path):
    _make_run_with_all_missing_kinds(tmp_path)
    diag = tmp_path / "d.json"
    diag.write_text(json.dumps({"nodes": [{"id": "n1", "label": "x", "source": "R001"}]}), encoding="utf-8")
    # missing source or license
    assert cli.main(["provide", str(tmp_path), "U001", "--file", str(diag), "--license", "CC0"]) == 2
    assert cli.main(["provide", str(tmp_path), "U001", "--file", str(diag), "--source", "S"]) == 2
    assert cli.main(["provide", str(tmp_path), "U001", "--file", str(diag), "--source", "S", "--license", "All rights reserved"]) == 2

    # diagram JSON missing node source -> rejected
    bad_diag = tmp_path / "bad_diag.json"
    bad_diag.write_text(json.dumps({"nodes": [{"id": "n1", "label": "x"}]}), encoding="utf-8")
    assert cli.main(["provide", str(tmp_path), "U001", "--file", str(bad_diag), "--source", "S", "--license", "CC0"]) == 2

    # chart U002 given an image instead of JSON/CSV -> rejected
    png = tmp_path / "pic.png"
    png.write_bytes(_png_bytes())
    assert cli.main(["provide", str(tmp_path), "U002", "--file", str(png), "--source", "S", "--license", "CC0"]) == 2

    # chart U002 given non-numeric values -> rejected
    bad_chart = tmp_path / "bad_chart.json"
    bad_chart.write_text(json.dumps({"labels": ["A"], "values": ["high"], "source": "NOAA"}), encoding="utf-8")
    assert cli.main(["provide", str(tmp_path), "U002", "--file", str(bad_chart), "--source", "NOAA", "--license", "CC0"]) == 2

    # image U006 given corrupt bytes or .json -> rejected
    corrupt = tmp_path / "bad.png"
    corrupt.write_bytes(b"not a png")
    assert cli.main(["provide", str(tmp_path), "U006", "--file", str(corrupt), "--source", "S", "--license", "CC0"]) == 2
    assert cli.main(["provide", str(tmp_path), "U006", "--file", str(diag), "--source", "S", "--license", "CC0"]) == 2

    # valid image U006 accepted
    assert cli.main(["provide", str(tmp_path), "U006", "--file", str(png), "--source", "S", "--license", "CC0"]) == 0


# ---------------------------------------------------------------- ⑤ hard gates: no_fabricated_visuals & placeholders_marked
def _setup_minimal_deck_run(tmp_path: Path) -> Path:
    pp = ProductionPlan(run_id=RID, source_utterance=U, **GOOD_A)
    write_contract(pp, tmp_path / "production_plan.json")
    pages = [{"page_id": f"S{i:03d}", "status": "provisional", **p} for i, p in enumerate(GOOD_B["pages"], 1)]
    write_contract(SlidePlan(run_id=RID, status="provisional", narrative=GOOD_B["narrative"], pages=pages),
                   tmp_path / "slide_plan.json")
    write_contract(AssetRequirements(run_id=RID, requirements=[req("A001", "diagram", hints=("x",))]),
                   tmp_path / "asset_requirements.json")
    write_contract(AssetRegistry(run_id=RID, assets=[{"asset_id": "A001", "status": "missing", "type": "diagram", "notes": "none"}]),
                   tmp_path / "asset_registry.json")
    write_contract(UserInputRequests(run_id=RID, requests=[{
        "request_id": "U001",
        "kind": "diagram",
        "asset_id": "A001",
        "page_ids": ["S001"],
        "need": "热岛成因流程图",
        "why": "D 未找到",
        "format_wanted": "JSON 或 PNG",
        "acceptance": ["有来源"],
        "blocking": True,
        "fallback": "placeholder",
        "status": "open",
    }], summary="1 open (1 blocking)"), tmp_path / "user_input_requests.json")
    return tmp_path


def test_gates_fail_on_unbound_image_and_missing_placeholder(tmp_path):
    run_dir = _setup_minimal_deck_run(tmp_path)
    img_path = tmp_path / "rogue.png"
    img_path.write_bytes(_png_bytes(100, 100))

    prs = Presentation()
    s1 = prs.slides.add_slide(prs.slide_layouts[6])
    s2 = prs.slides.add_slide(prs.slide_layouts[6])
    # Deliberately add an unbound picture with default name ("Picture 1") and omit placeholder U001!
    s1.shapes.add_picture(str(img_path), Inches(1), Inches(1), width=Inches(2))
    prs.save(str(run_dir / "deck.pptx"))

    rep = check_gates(run_dir)
    by = {r.gate: r for r in rep.results}
    assert by["no_fabricated_visuals"].status == "fail"
    assert by["placeholders_marked"].status == "fail"
    assert rep.overall == "fail"


def test_gates_pass_with_marked_placeholder_and_bound_provided_visual(tmp_path):
    run_dir = _setup_minimal_deck_run(tmp_path)
    prs = Presentation()
    s1 = prs.slides.add_slide(prs.slide_layouts[6])
    s2 = prs.slides.add_slide(prs.slide_layouts[6])
    box = s1.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(2))
    box.name = "PLACEHOLDER_U001"
    box.text_frame.text = "待补充：热岛成因流程图（U001）"
    s1.notes_slide.notes_text_frame.text = "待补充素材 U001：热岛成因流程图"
    prs.save(str(run_dir / "deck.pptx"))

    rep = check_gates(run_dir)
    by = {r.gate: r for r in rep.results}
    assert by["no_fabricated_visuals"].status == "pass"
    assert by["placeholders_marked"].status == "pass"

    # Now provide U001 as a PNG and bind it properly in deck.pptx -> both gates still pass!
    good_png = tmp_path / "provided.png"
    good_png.write_bytes(_png_bytes(100, 100))
    assert cli.main(["provide", str(run_dir), "U001", "--file", str(good_png), "--source", "IPCC", "--license", "CC BY 4.0"]) == 0
    prs2 = Presentation()
    s1_2 = prs2.slides.add_slide(prs2.slide_layouts[6])
    prs2.slides.add_slide(prs2.slide_layouts[6])
    pic = s1_2.shapes.add_picture(str(run_dir / "inputs" / "U001.png"), Inches(1), Inches(1), width=Inches(2))
    pic.name = "INPUT_U001__diag"
    prs2.save(str(run_dir / "deck.pptx"))

    rep2 = check_gates(run_dir)
    by2 = {r.gate: r for r in rep2.results}
    assert by2["no_fabricated_visuals"].status == "pass"
    assert by2["placeholders_marked"].status == "pass"
