import pytest

from conftest import GOOD_A
from fakes import FakeClient
from pptv01.a_plan import PlanRuleError, build_plan, run_stage_a
from pptv01.contracts import ProductionPlan, load_contract
from pptv01.llm import LLMOutputError

U = "帮我做一个城市热岛效应的 PPT。"


def build(raw, utt=U):
    return build_plan(raw, run_id="r1", utterance=utt)


def with_(**kw):
    return {**GOOD_A, **kw}


def test_good_plan_accepted_and_injected_fields():
    p = build(GOOD_A)
    assert p.run_id == "r1" and p.source_utterance == U and p.schema_version == "0.1"
    assert {d.field for d in p.defaults_applied} == {"audience", "style"}


@pytest.mark.parametrize("field,text", [
    ("content_direction", "共 10 页，从现象到应对。"),
    ("content_direction", "建议做成约12张幻灯片。"),
    ("constraints", ["15 pages"]),
    ("constraints", ["时长 20 分钟"]),
    ("content_direction", "讲半小时，覆盖成因。"),
    ("open_questions", ["十页够不够？"]),
])
def test_rejects_invented_page_counts_and_durations(field, text):
    val = text if field == "content_direction" else text if isinstance(text, list) else [text]
    with pytest.raises(PlanRuleError, match="invented"):
        build(with_(**{field: val}))


def test_user_stated_limits_are_allowed():
    p = build(with_(constraints=["不超过5页", "minimal_text"]), utt="做一份不超过5页的人工智能介绍，尽量少用文字。")
    assert "不超过5页" in p.constraints
    p = build(with_(constraints=["约10分钟"]), utt="我明天要讲10分钟的半导体芯片，帮我做PPT。")
    assert p.constraints == ["约10分钟"]
    # but a *different* number than the user's is still invented
    with pytest.raises(PlanRuleError, match="invented"):
        build(with_(constraints=["约15分钟"]), utt="我明天要讲10分钟的半导体芯片，帮我做PPT。")


def test_rejects_duration_derived_page_count():
    with pytest.raises(PlanRuleError, match="invented page count 10"):
        build(with_(content_direction="10分钟讲解，约10页。"), utt="我明天要讲10分钟的半导体芯片，帮我做PPT。")


@pytest.mark.parametrize("text", ["第3页放成因图", "封面页+目录页+正文", "S001 讲概念", "slide 2 shows causes"])
def test_rejects_page_structure_locks(text):
    with pytest.raises(PlanRuleError, match="locks page structure"):
        build(with_(content_direction=text))


@pytest.mark.parametrize("text", ["研究表明热岛使气温上升3%", "数据显示城市更热", "升温 2.5%"])
def test_rejects_conclusion_claims(text):
    with pytest.raises(PlanRuleError, match="stage A may only give directions"):
        build(with_(content_direction=text))


@pytest.mark.parametrize("field", ["page_count", "duration", "时长", "slides"])
def test_page_count_and_duration_never_defaultable(field):
    bad = with_(defaults_applied=[{"field": field, "value": "10", "rationale": "默认"}])
    with pytest.raises(PlanRuleError, match="must not be defaulted"):
        build(bad)


def test_default_value_must_match_plan_field():
    bad = with_(defaults_applied=[{"field": "audience", "value": "experts", "rationale": "x"}])
    with pytest.raises(ValueError, match="does not match"):
        build(bad)


def test_unknown_keys_from_model_rejected():
    with pytest.raises(ValueError):
        build({**GOOD_A, "page_count": 8})


def test_stage_a_repair_retry_then_success(tmp_path):
    client = FakeClient([with_(content_direction="共 10 页"), GOOD_A])
    plan, attempts = run_stage_a(U, "r1", client, tmp_path)
    assert len(client.calls) == 2 and [a["error"] is None for a in attempts] == [False, True]
    assert "invented page count" in client.calls[1][-1]["content"]
    assert load_contract(ProductionPlan, tmp_path / "production_plan.json") == plan


def test_stage_a_fails_after_one_retry(tmp_path):
    bad = with_(content_direction="共 10 页")
    client = FakeClient([bad, bad, GOOD_A])
    with pytest.raises(LLMOutputError):
        run_stage_a(U, "r1", client, tmp_path)
    assert len(client.calls) == 2  # exactly one repair retry
    assert not (tmp_path / "production_plan.json").exists()


def test_stage_a_accepts_fenced_json():
    import json
    client = FakeClient(["好的：\n```json\n" + json.dumps(GOOD_A, ensure_ascii=False) + "\n```"])
    plan, _ = run_stage_a(U, "r1", client)
    assert plan.topic == "城市热岛效应"
