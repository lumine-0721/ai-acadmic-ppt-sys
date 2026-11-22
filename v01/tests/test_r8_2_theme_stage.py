from __future__ import annotations

import json
from pathlib import Path

import pytest

from pptv01.contracts import PageDesign, ProductionPlan, load_contract
from pptv01.llm import AgentClient, AwaitingAgentResponse, LLMOutputError
from pptv01.runner import run_pipeline
from pptv01.theme.select import select_theme
from pptv01.theme.tokens import load_selection_index
from tests.conftest import GOOD_A, GOOD_B, GOOD_C
from tests.fakes import FakeClient

HOLDOUT_PATH = Path(__file__).resolve().parent / "fixtures" / "theme_holdout.json"


def _make_plan(case: dict) -> ProductionPlan:
    return ProductionPlan(
        run_id=f"th-{case['id']}",
        source_utterance=f"请制作一份关于{case['topic']}的演示文稿。",
        topic=case["topic"],
        purpose=case["purpose"],
        audience=case["audience"],
        language="zh-CN",
        style=case["style"],
        constraints=[],
        defaults_applied=[],
        content_direction=f"围绕{case['topic']}展开核心内容。",
        open_questions=[],
        retrieval_brief=[
            {
                "topic": case["topic"],
                "why": "background",
                "queries": [{"text": case["topic"], "lang": "zh"}],
            }
        ],
    )


def test_r8_2_selection_index_cleaned_of_business_and_smoke_overfits():
    idx = load_selection_index()
    presets = idx["presets"]
    espresso_blob = json.dumps(presets["warm-espresso"], ensure_ascii=False).casefold()
    for banned in ("business", "pitch", "创业", "商业计划", "品牌", "投资人", "路演", "entrepreneurship"):
        assert banned not in espresso_blob, f"warm-espresso must not contain generic cue {banned!r}"

    all_keywords_blob = " ".join(
        " ".join(p.get("keywords", []))
        for p in presets.values()
    ).casefold()
    for smoke_overfit in ("热岛", "城市热岛", "海绵城市", "帕特农", "通俗", "中学生", "选修", "名建筑"):
        assert smoke_overfit not in all_keywords_blob, f"selection-index keywords overfit smoke title {smoke_overfit!r}"


def test_r8_2_keyword_fallback_holdout_suite_and_prefix():
    cases = json.loads(HOLDOUT_PATH.read_text(encoding="utf-8"))["cases"]
    assert len(cases) >= 10
    for case in cases:
        plan = _make_plan(case)
        sel = select_theme(plan, client=None)
        assert sel.method == "keyword_fallback"
        assert sel.rationale.startswith("关键词兜底："), f"{case['id']}: missing fallback prefix in {sel.rationale!r}"
        assert sel.theme_id not in case["forbidden_themes"], (
            f"{case['id']} ({case['topic']}) landed on forbidden theme {sel.theme_id}: {sel.rationale}"
        )
        assert sel.theme_id in case["expected_themes"], (
            f"{case['id']} ({case['topic']}) landed on {sel.theme_id}, expected one of {case['expected_themes']}"
        )


def test_r8_2_agent_stage_th_awaits_response_and_rejects_invalid_without_fallback(tmp_path):
    cases = json.loads(HOLDOUT_PATH.read_text(encoding="utf-8"))["cases"]
    plan = _make_plan(cases[1])  # 新能源汽车市场分析

    io = tmp_path / "io"
    client = AgentClient(io)
    with pytest.raises(AwaitingAgentResponse) as exc_info:
        select_theme(plan, client=client)
    assert exc_info.value.request_path.name == "TH_request_1.json"
    req = json.loads((io / "TH_request_1.json").read_text(encoding="utf-8"))
    assert req["stage"] == "TH"
    assert "新能源汽车市场分析" in req["messages"][1]["content"]
    assert "themes" in req["messages"][1]["content"]

    # Invalid attempt 1 + invalid attempt 2 must fail validation without silent keyword fallback
    (io / "TH_response_1.json").write_text(
        json.dumps({"theme_id": "not-in-library", "rationale": "x"}, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(AwaitingAgentResponse) as retry_exc:
        select_theme(plan, client=AgentClient(io))
    assert retry_exc.value.request_path.name == "TH_request_2.json"

    (io / "TH_response_2.json").write_text(
        json.dumps({"theme_id": "data-journalism", "rationale": "   "}, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(LLMOutputError):
        select_theme(plan, client=AgentClient(io))

    # Valid agent response succeeds with theme_method='agent'
    (io / "TH_response_1.json").write_text(
        json.dumps(
            {
                "theme_id": "data-journalism",
                "rationale": "新能源汽车市场分析以量化数据与政策趋势为核心，适合采用编辑级高对比数据主题。",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    sel = select_theme(plan, client=AgentClient(io))
    assert sel.theme_id == "data-journalism"
    assert sel.method == "agent"
    assert "量化数据" in sel.rationale


def test_r8_2_llm_client_sets_theme_method_llm():
    cases = json.loads(HOLDOUT_PATH.read_text(encoding="utf-8"))["cases"]
    plan = _make_plan(cases[3])  # 量子计算入门
    client = FakeClient([{"theme_id": "tech-indigo", "rationale": "面向本科生的前沿计算专题适合现代科研蓝紫主题。"}])
    sel = select_theme(plan, client=client)
    assert sel.theme_id == "tech-indigo"
    assert sel.method == "llm"
    assert client.stage == "TH"


def test_r8_2_runner_stage_th_pauses_on_missing_and_records_agent_method(tmp_path):
    io = tmp_path / "io"
    io.mkdir()
    for code, data in (("A", GOOD_A), ("B", GOOD_B), ("C", GOOD_C)):
        (io / f"{code}_response_1.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(AwaitingAgentResponse) as exc_info:
        run_pipeline("帮我做一个城市热岛效应的 PPT。", tmp_path, AgentClient(io), run_id="r8-2-th")
    assert exc_info.value.request_path.name == "TH_request_1.json"
    assert not (tmp_path / "r8-2-th" / "page_design.json").exists()

    (io / "TH_response_1.json").write_text(
        json.dumps(
            {
                "theme_id": "eco-canopy",
                "rationale": "城市热岛属于城市生态与气候适应专题，契合森林生态主题调性。",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    report, run_dir = run_pipeline(
        "帮我做一个城市热岛效应的 PPT。",
        tmp_path,
        AgentClient(io),
        run_id="r8-2-th",
        resume=True,
    )
    design = load_contract(PageDesign, run_dir / "page_design.json")
    assert design.theme_id == "eco-canopy"
    assert design.theme_method == "agent"
    assert "城市生态" in design.theme_rationale
    assert {s.stage: s.status.value for s in report.stages}["F"] == "done"
