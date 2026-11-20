import copy
import json
from pathlib import Path

import pytest

from conftest import GOOD_A, GOOD_B, GOOD_C
from fakes import FakeClient, FakeResearch
from pptv01.a_plan import build_plan
from pptv01.b_slide_plan.stage import build_slide_plan
from pptv01.c_asset_requirements import RequirementRuleError, run_stage_c, validate_requirements
from pptv01.c_asset_requirements.stage import build_requirements
from pptv01.contracts import AssetRequirements, StageStatus, Verification, load_contract
from pptv01.llm import LLMOutputError
from pptv01.runner import run_pipeline

U = "帮我做一个城市热岛效应的 PPT。"
PLAN = build_plan(GOOD_A, run_id="r1", utterance=U)
SLIDES = build_slide_plan(GOOD_B, PLAN, {})
# need refs in GOOD_B: S001#1 image (某城市热像图, to_verify) ; S002#1 diagram (no target)

def c(**over):
    d = copy.deepcopy(GOOD_C)
    d.update(over)
    return d


def build(raw):
    return build_requirements(raw, PLAN, SLIDES, {})


def test_good_requirements_ids_assigned_by_code_and_targets_carried(tmp_path):
    reqs, _ = run_stage_c(PLAN, SLIDES, FakeClient([GOOD_C]), tmp_path)
    assert [r.asset_id for r in reqs.requirements] == ["A001", "A002"]
    assert reqs.requirements[0].targets[0].verification is Verification.to_verify
    assert reqs.requirements[0].need_refs == ["S001#1"]
    assert load_contract(AssetRequirements, tmp_path / "asset_requirements.json") == reqs


def test_model_cannot_choose_asset_ids():
    d = c(); d["requirements"][0]["asset_id"] = "A007"
    with pytest.raises(ValueError):
        build(d)


def test_every_need_must_be_covered_or_dropped_with_reason():
    d = c(); d["requirements"] = d["requirements"][:1]
    with pytest.raises(RequirementRuleError, match=r"S002#1"):
        build(d)
    d["dropped_needs"] = [{"need_ref": "S002#1", "reason": "这一页没有图也能讲清"}]
    assert len(build(d).dropped_needs) == 1
    d["dropped_needs"] = [{"need_ref": "S002#1", "reason": ""}]
    with pytest.raises(ValueError):
        build(d)


def test_cannot_both_cover_and_drop():
    d = c(); d["dropped_needs"] = [{"need_ref": "S001#1", "reason": "x"}]
    with pytest.raises(ValueError, match="covered and dropped"):
        build(d)


def test_cannot_invent_needs_pages_types_or_targets():
    d = c(); d["requirements"][0]["need_refs"] = ["S001#9"]
    with pytest.raises(RequirementRuleError, match="invent needs"):
        build(d)
    d = c(); d["requirements"][0]["page_ids"] = ["S001", "S002"]
    with pytest.raises(RequirementRuleError, match="must equal the pages"):
        build(d)
    d = c(); d["requirements"][0]["type"] = "table"
    with pytest.raises(RequirementRuleError, match="differs from the type"):
        build(d)
    d = c(); d["requirements"][0]["targets"] = ["纽约热像图"]
    with pytest.raises(RequirementRuleError, match="invent targets"):
        build(d)
    d = c(); d["requirements"][0]["targets"] = []
    with pytest.raises(ValueError, match="concrete target"):
        build(d)


@pytest.mark.parametrize("purpose", ["装饰页面", "配图", "美化这一页的版面效果", "相关图片素材", "图"])
def test_generic_purposes_rejected(purpose):
    d = c(); d["requirements"][0]["purpose"] = purpose
    with pytest.raises(RequirementRuleError, match="generic|empty"):
        build(d)


def test_generic_or_duplicate_hints_rejected():
    d = c(); d["requirements"][0]["query_hints"] = ["图片", "素材"]
    with pytest.raises(RequirementRuleError, match="too generic"):
        build(d)
    d = c(); d["requirements"][0]["query_hints"] = ["热像图", "热像图"]
    with pytest.raises(RequirementRuleError, match="duplicate"):
        build(d)


def test_acceptance_rules():
    d = c(); d["requirements"][0]["acceptance"] = ["清晰", "好看"]
    with pytest.raises(RequirementRuleError, match="legally usable"):
        build(d)
    d = c(); d["requirements"][0]["acceptance"] = ["可合法使用，许可明确"]
    with pytest.raises(RequirementRuleError, match="at least 2"):
        build(d)
    d = c(); d["requirements"][0]["acceptance"] = ["可合法使用，许可明确", "清晰可读", "须核实城市"]
    with pytest.raises(RequirementRuleError, match="relevance"):
        build(d)
    d = c(); d["requirements"][0]["acceptance"] = ["与热岛直接相关", "可合法使用，许可明确"]
    with pytest.raises(RequirementRuleError, match="核实"):
        build(d)                                          # to_verify target -> must demand verification


def test_reference_needs_verifiable_source():
    slides = copy.deepcopy(GOOD_B)
    slides["pages"][0]["asset_needs"].append({"type": "reference", "description": "为定义页提供可核验出处",
                                              "targets": [{"name": "城市热岛的综述", "entity_type": "publication"}]})
    sp = build_slide_plan(slides, PLAN, {})
    d = c()
    d["requirements"].append({"need_refs": ["S001#2"], "page_ids": ["S001"], "type": "reference",
                              "purpose": "为第一页的定义提供可核验的出处", "targets": ["城市热岛的综述"],
                              "query_hints": ["urban heat island review"], "priority": "optional",
                              "acceptance": ["与热岛定义直接相关", "须核实文献确实存在"]})
    with pytest.raises(RequirementRuleError, match="verifiable source"):
        build_requirements(d, PLAN, sp, {})
    d["requirements"][-1]["acceptance"] = ["与热岛定义直接相关", "来源可核验，有 DOI", "须核实文献确实存在"]
    assert len(build_requirements(d, PLAN, sp, {}).requirements) == 3


def test_numbers_and_claims_rejected_in_requirements():
    d = c(); d["requirements"][0]["purpose"] = "展示城区比郊区高 3 摄氏度的温度差"
    with pytest.raises(RequirementRuleError, match="unsourced"):
        build(d)
    d = c(); d["requirements"][0]["acceptance"].append("研究表明城区更热")
    with pytest.raises(RequirementRuleError, match="conclusion"):
        build(d)


def test_at_least_one_required():
    d = c(); d["requirements"][0]["priority"] = "optional"
    with pytest.raises(RequirementRuleError, match="priority=required"):
        build(d)


def test_page_with_no_needs_is_fine_and_empty_requirements_ok():
    sp = copy.deepcopy(GOOD_B)
    for p in sp["pages"]:
        p["asset_needs"] = []
    plan = build_slide_plan(sp, PLAN, {})
    assert build_requirements({"requirements": []}, PLAN, plan, {}).requirements == []


def test_repair_retry_then_success_and_hard_fail(tmp_path):
    bad = c(); bad["requirements"][0]["purpose"] = "装饰"
    client = FakeClient([bad, GOOD_C])
    reqs, attempts = run_stage_c(PLAN, SLIDES, client, tmp_path)
    assert [a["error"] is None for a in attempts] == [False, True]
    assert "generic" in client.calls[1][-1]["content"]
    client = FakeClient([bad, bad, GOOD_C])
    with pytest.raises(LLMOutputError):
        run_stage_c(PLAN, SLIDES, client)
    assert len(client.calls) == 2


def test_prompt_shows_need_refs_and_research_titles():
    client = FakeClient([GOOD_C])
    notes = {n.note_id: n for n in FakeResearch([("城市热岛", "x")]).research(PLAN).notes}
    run_stage_c(PLAN, SLIDES, client, notes=notes)
    body = client.calls[0][-1]["content"]
    assert '"need_ref": "S001#1"' in body and "[R001] 城市热岛" in body


def _stub_render(pptx_path: Path, out_dir: Path) -> list[Path]:
    from PIL import Image, ImageDraw
    from pptx import Presentation
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


def test_runner_runs_a_to_d_and_reports_e_to_h_not_implemented(tmp_path):
    report, run_dir = run_pipeline(
        U, tmp_path, FakeClient([GOOD_A, GOOD_B, GOOD_C]), run_id="t-c", render_fn=_stub_render
    )
    st = {s.stage: s.status for s in report.stages}
    assert [st[x] for x in "ABCDEFGH"] == [StageStatus.done] * 8
    assert (run_dir / "asset_requirements.json").is_file() and (run_dir / "llm_trace" / "C.json").is_file()
    assert report.overall.value == "complete_with_placeholders"
    # conftest sets PPTV01_ASSETS=none: D records every requirement as missing, saying retrieval was disabled
    reg = json.loads((run_dir / "asset_registry.json").read_text("utf-8"))
    assert all(a["status"] == "missing" and "disabled" in a["notes"] for a in reg["assets"])
    assert report.missing_assets == [a["asset_id"] for a in reg["assets"]]
    assert (run_dir / "deck.pptx").is_file() and (run_dir / "qa_report.json").is_file()


def test_runner_stage_c_failure_reported(tmp_path):
    bad = c(); bad["requirements"][0]["purpose"] = "装饰"
    report, run_dir = run_pipeline(U, tmp_path, FakeClient([GOOD_A, GOOD_B, bad, bad]), run_id="t-cf")
    st = {s.stage: s.status for s in report.stages}
    assert st["B"] is StageStatus.done and st["C"] is StageStatus.failed
    assert report.overall.value == "failed" and "stage C failed" in report.failure_reason
    assert (run_dir / "slide_plan.json").is_file() and not (run_dir / "asset_requirements.json").exists()


def test_runner_skips_c_when_b_fails(tmp_path):
    badb = copy.deepcopy(GOOD_B); badb["pages"][0]["key_points"] = ["数据显示 30% 更热"]
    report, _ = run_pipeline(U, tmp_path, FakeClient([GOOD_A, badb, badb]), run_id="t-cs")
    st = {s.stage: s.status for s in report.stages}
    assert st["B"] is StageStatus.failed and st["C"] is StageStatus.skipped


@pytest.mark.parametrize("purpose,flagged", [
    ("展示一座装饰艺术风格摩天楼的外观，与现代主义形成对照", False),
    ("展示一座 Art Deco 风格摩天楼的外观，对照现代主义的简洁", False),
    ("装饰页面，让这一页更好看一些", True),
    ("美化这一页的版面效果", True),
    ("作为配图使用即可", True),
])
def test_generic_purpose_check_does_not_flag_art_deco(purpose, flagged):
    from pptv01.c_asset_requirements.validate import GENERIC_PURPOSE
    assert bool(GENERIC_PURPOSE.search(purpose)) is flagged
