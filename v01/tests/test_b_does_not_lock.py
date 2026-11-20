import copy

import pytest

from conftest import GOOD_A, GOOD_B
from fakes import FakeClient
from pptv01.a_plan import build_plan
from pptv01.b_slide_plan import DisabledResearch, SlidePlanRuleError, run_stage_b
from pptv01.b_slide_plan.stage import build_slide_plan
from pptv01.contracts import SlidePlan, SlideStatus, load_contract
from pptv01.llm import LLMOutputError

U = "帮我做一个城市热岛效应的 PPT。"
PLAN = build_plan(GOOD_A, run_id="r1", utterance=U)


def b(**over):
    d = copy.deepcopy(GOOD_B)
    d.update(over)
    return d


def test_b_output_is_provisional_with_assigned_ids(tmp_path):
    plan, _, _ = run_stage_b(PLAN, FakeClient([GOOD_B]), tmp_path)
    assert plan.status is SlideStatus.provisional
    assert [p.page_id for p in plan.pages] == ["S001", "S002"]
    assert all(p.status is SlideStatus.provisional for p in plan.pages)
    assert plan.revision_log == []
    assert load_contract(SlidePlan, tmp_path / "slide_plan.json") == plan
    assert all(p.asset_needs for p in plan.pages)


def test_model_cannot_set_status_or_ids():
    d = b()
    d["pages"][0]["status"] = "revised"
    with pytest.raises(ValueError):
        build_slide_plan(d, PLAN, notes={})
    d = b(); d["pages"][0]["page_id"] = "S009"
    with pytest.raises(ValueError):
        build_slide_plan(d, PLAN, notes={})
    with pytest.raises(ValueError):
        build_slide_plan(b(status="revised"), PLAN, notes={})


def test_b_rejects_unsourced_numbers_and_claims():
    d = b(); d["pages"][0]["key_points"] = ["研究表明热岛使死亡率上升23%"]
    with pytest.raises(SlidePlanRuleError, match="unsourced|conclusion"):
        build_slide_plan(d, PLAN, notes={})


def test_b_rejects_invented_page_count_in_narrative():
    with pytest.raises(SlidePlanRuleError, match="invented page count"):
        build_slide_plan(b(narrative="全deck共10页，先讲概念。"), PLAN, notes={})


def test_b_respects_user_page_limit():
    plan = build_plan({**GOOD_A, "constraints": ["不超过1页"]}, run_id="r1", utterance="做一份不超过1页的热岛介绍")
    with pytest.raises(SlidePlanRuleError, match="at most 1"):
        build_slide_plan(GOOD_B, plan, notes={})


def test_b_user_exact_page_count():
    plan = build_plan({**GOOD_A, "constraints": ["2页"]}, run_id="r1", utterance="做一份2页的热岛介绍")
    assert len(build_slide_plan(GOOD_B, plan, notes={}).pages) == 2
    plan3 = build_plan({**GOOD_A, "constraints": ["3页"]}, run_id="r1", utterance="做一份3页的热岛介绍")
    with pytest.raises(SlidePlanRuleError, match="asked for 3"):
        build_slide_plan(GOOD_B, plan3, notes={})


def test_b_retry_once_then_fail():
    bad = b(); bad["pages"][0]["key_points"] = ["数据显示 30% 的城市更热"]
    c = FakeClient([bad, bad, GOOD_B])
    with pytest.raises(LLMOutputError):
        run_stage_b(PLAN, c)
    assert len(c.calls) == 2


def test_b_prompt_states_no_research_when_hook_is_stub():
    c = FakeClient([GOOD_B])
    run_stage_b(PLAN, c, research=DisabledResearch())
    assert "没有检索到任何条目" in c.calls[0][-1]["content"]


def test_duplicate_titles_rejected():
    d = b(); d["pages"][1]["title"] = d["pages"][0]["title"]
    with pytest.raises(SlidePlanRuleError, match="duplicate"):
        build_slide_plan(d, PLAN, notes={})


def test_b_rejects_verified_target_without_research():
    d = b(); d["pages"][0]["asset_needs"][0]["targets"][0]["verification"] = "verified"
    with pytest.raises(ValueError, match="verified"):
        build_slide_plan(d, PLAN, notes={})
    d = b(); d["pages"][0]["asset_needs"][0]["targets"][0].update(verification="verified", source_refs=["R001"])
    with pytest.raises(SlidePlanRuleError, match="no research was performed"):
        build_slide_plan(d, PLAN, notes={})


def test_b_rejects_unstructured_or_vague_needs():
    d = b(); d["pages"][0]["asset_needs"] = ["配几张图"]
    with pytest.raises(ValueError):
        build_slide_plan(d, PLAN, notes={})
    d = b(); d["pages"][0]["asset_needs"][0]["targets"] = []
    with pytest.raises(ValueError, match="concrete target"):
        build_slide_plan(d, PLAN, notes={})


def test_b_rejects_invented_numbers_in_targets_and_descriptions():
    d = b(); d["pages"][0]["asset_needs"][0]["targets"][0]["name"] = "高 324 米的某塔"
    with pytest.raises(SlidePlanRuleError, match="unsourced"):
        build_slide_plan(d, PLAN, notes={})


def test_b_candidates_are_marked_to_verify(tmp_path):
    plan, _, _ = run_stage_b(PLAN, FakeClient([GOOD_B]), tmp_path)
    tgs = [t for p in plan.pages for n in p.asset_needs for t in n.targets]
    assert tgs and all(t.verification.value == "to_verify" for t in tgs)
