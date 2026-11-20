"""N6 (B13) tests: SELF_DRAW_RE strips negation prefixes while still rejecting genuine self-draw wording."""

from __future__ import annotations

import pytest

from pptv01.c_asset_requirements.validate import has_self_draw_suggestion, validate_requirements
from pptv01.contracts import AssetRequirements, ProductionPlan, SlidePlan
from tests.conftest import GOOD_A, GOOD_B, GOOD_C

RID = "20260101-000000-n600"


@pytest.mark.parametrize(
    "text",
    [
        "D 未找到可标来源的现成图；不允许凭空绘制，请用户提供结构化数据或图",
        "禁止自绘无来源示意图，缺素材时标注占位",
        "不得自行绘制图表，须提供真实出处",
        "严禁凭空绘制或编造趋势曲线",
        "不要自绘，请上传有许可的 PNG 或 JSON",
        "Do not draw natively without a verifiable source",
        "Missing diagrams must not be drawn natively in stage G",
    ],
)
def test_n6_negated_prohibitions_are_allowed(text):
    assert has_self_draw_suggestion(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "若取用现成图须许可明确；否则自绘并说明",
        "找不到现成素材时可自行绘制机制示意图",
        "无可用图片则凭空绘制概念图",
        "diagram: to be drawn natively in a later stage (F/G)",
        "If Commons has no match, draw natively as vector shapes",
        "不允许凭空绘制数据，但缺失示意图可自绘",
    ],
)
def test_n6_positive_self_draw_suggestions_are_rejected(text):
    assert has_self_draw_suggestion(text) is True


def test_n6_validate_requirements_accepts_negated_prohibition_and_rejects_self_draw():
    pp = ProductionPlan(run_id=RID, source_utterance="帮我做一个城市热岛效应的 PPT。", **GOOD_A)
    sp = SlidePlan(
        run_id=RID,
        status="provisional",
        narrative=GOOD_B["narrative"],
        pages=[{"page_id": f"S{i:03d}", "status": "provisional", **p} for i, p in enumerate(GOOD_B["pages"], 1)],
    )

    good_req_list = []
    for i, r in enumerate(GOOD_C["requirements"], 1):
        d = dict(r)
        d["asset_id"] = f"A{i:03d}"
        d["targets"] = (
            [{"name": "某城市热像图", "entity_type": "dataset", "verification": "to_verify"}]
            if i == 1
            else []
        )
        if i == 2:
            d["acceptance"] = [
                "与三类成因直接对应，许可明确",
                "找不到现成图时不允许凭空绘制，必须由用户提供带来源的结构化图",
            ]
        good_req_list.append(d)

    reqs_ok = AssetRequirements(run_id=RID, requirements=good_req_list, dropped_needs=[])
    assert validate_requirements(reqs_ok, sp, pp) == []

    bad_req_list = [dict(x) for x in good_req_list]
    bad_req_list[1]["acceptance"] = [
        "与三类成因直接对应，许可明确",
        "若取用现成图须许可明确；否则自绘并说明",
    ]
    reqs_bad = AssetRequirements(run_id=RID, requirements=bad_req_list, dropped_needs=[])
    probs = validate_requirements(reqs_bad, sp, pp)
    assert any("self-drawing" in p for p in probs)
