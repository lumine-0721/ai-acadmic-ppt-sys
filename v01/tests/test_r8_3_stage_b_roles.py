from __future__ import annotations

import copy
from pathlib import Path

from pptx import Presentation
import pytest

from pptv01.a_plan import build_plan
from pptv01.b_slide_plan import SlidePlanRuleError, validate_ghost_deck
from pptv01.b_slide_plan.stage import build_slide_plan
from pptv01.contracts import (
    AssetRegistry,
    ElementKind,
    PageDesignEntry,
    PageRole,
    PointGroup,
    SlideAssetMapping,
    SlidePage,
    SlidePlan,
    UserInputRequests,
    load_contract,
)
from pptv01.f_layout import run_stage_f
from pptv01.f_layout.layouts import (
    SlotItem,
    build_layout_elements,
    display_title,
    eligible_layouts_for_page,
)
from pptv01.g_build import compile_deck_ops
from pptv01.h_qa.occupancy import VOID_MAX_IN, score_occupancy
from tests.conftest import GOOD_A

U = "帮我做一个城市热岛效应的 PPT。"
PLAN = build_plan(GOOD_A, run_id="r8-3", utterance=U)


def _valid_stage_b_payload() -> dict:
    return {
        "narrative": "先区分现象与热浪，再解析两类成因与三项缓解路径。",
        "pages": [
            {
                "title": "城市热岛定义",
                "page_role": "cover",
                "headline": "城市热岛体现为城区气温长期高于郊区，与短期热浪截然不同。",
                "goal": "区分城市热岛和一般热浪",
                "key_points": [
                    "城区气温相对周边郊区偏高",
                    "与持续数天的热浪事件属于不同尺度现象",
                ],
                "groups": [],
                "content_summary": "定义加对比说明现象本质。",
                "speaker_notes": "开场先厘清两个容易混淆的概念。城市热岛关注的是建成区与周边郊区之间的持续温差，而不是某几天出现的极端热浪天气。",
                "visual_intent": "封面标题",
                "asset_needs": [],
                "source_refs": [],
            },
            {
                "title": "热岛成因分解",
                "page_role": "content",
                "headline": "地表吸热与人为排热两条机制共同驱动城区温度攀升。",
                "goal": "说明两大核心成因",
                "key_points": [
                    "硬质沥青与混凝土路面比热容较低且白天大量吸收太阳辐射热量",
                    "城市绿地和水体比例不足削弱了日间植被蒸腾冷却作用与潜热散失",
                    "密集建筑群阻挡夜间长波向天空散热并减缓冷却速率",
                    "机动车辆通行与工业装置运行持续向近地面街谷直接释放大量显热负荷",
                    "建筑空调外机排热在夏季峰值时段进一步抬高街道局部微气候体感温度",
                    "高层连续建筑布局降低边界层平均风速并阻碍热空气向高空水平扩散",
                ],
                "groups": [
                    {
                        "heading": "下垫面能量收支失衡",
                        "points": [
                            "硬质沥青与混凝土路面比热容较低且白天大量吸收太阳辐射热量",
                            "城市绿地和水体比例不足削弱了日间植被蒸腾冷却作用与潜热散失",
                            "密集建筑群阻挡夜间长波向天空散热并减缓冷却速率",
                        ],
                    },
                    {
                        "heading": "人为热源与通风受阻",
                        "points": [
                            "机动车辆通行与工业装置运行持续向近地面街谷直接释放大量显热负荷",
                            "建筑空调外机排热在夏季峰值时段进一步抬高街道局部微气候体感温度",
                            "高层连续建筑布局降低边界层平均风速并阻碍热空气向高空水平扩散",
                        ],
                    },
                ],
                "content_summary": "分两组讲解物理机制。",
                "speaker_notes": "理解现象之后，我们将城市热岛的成因归纳为两条并行的物理链条。左侧关注下垫面储热和植被蒸发缺失，右侧则展示汽车与建筑设备排放的额外热量如何留在街谷内部。",
                "visual_intent": "双栏要点",
                "asset_needs": [],
                "source_refs": [],
            },
            {
                "title": "规划缓解途径",
                "page_role": "summary",
                "headline": "绿地冷岛、高反射材料与通风廊道构成城市降温的三类抓手。",
                "goal": "概括三项核心策略",
                "key_points": [
                    "连续公园绿地通过蒸腾与遮阴形成局地冷岛效应",
                    "串联口袋公园与滨水廊道扩大冷空气外溢覆盖范围",
                    "屋顶与路面选用高太阳辐射反射率涂层降低蓄热",
                    "浅色透水铺装可减少午后地表热量积聚并加速排水",
                    "控制新建片区高层建筑连续面宽以维持夏季主风道",
                    "沿街道主导风向留出开敞廊道促进街谷热交换扩散",
                ],
                "groups": [
                    {
                        "heading": "生态冷源扩容",
                        "points": [
                            "连续公园绿地通过蒸腾与遮阴形成局地冷岛效应",
                            "串联口袋公园与滨水廊道扩大冷空气外溢覆盖范围",
                        ],
                    },
                    {
                        "heading": "材料反照率提升",
                        "points": [
                            "屋顶与路面选用高太阳辐射反射率涂层降低蓄热",
                            "浅色透水铺装可减少午后地表热量积聚并加速排水",
                        ],
                    },
                    {
                        "heading": "街区通风优化",
                        "points": [
                            "控制新建片区高层建筑连续面宽以维持夏季主风道",
                            "沿街道主导风向留出开敞廊道促进街谷热交换扩散",
                        ],
                    },
                ],
                "content_summary": "用三张卡片归纳对应策略。",
                "speaker_notes": "在掌握成因的基础上，最后把干预路径归为三个落地方向。通过新增绿地、改善屋面建材以及打开通风廊道，规划团队可以在不同尺度上协同改善近地面热环境。",
                "visual_intent": "三栏卡片",
                "asset_needs": [],
                "source_refs": [],
            },
        ],
    }


def test_r8_3_stage_b_rejects_missing_new_fields_and_bad_speaker_notes():
    base = _valid_stage_b_payload()
    plan = build_slide_plan(base, PLAN, notes={})
    assert [p.page_role for p in plan.pages] == [PageRole.cover, PageRole.content, PageRole.summary]

    # Missing page_role / headline / speaker_notes rejected by DraftPage schema validation
    for field in ("page_role", "headline", "speaker_notes"):
        bad = copy.deepcopy(base)
        del bad["pages"][0][field]
        with pytest.raises(ValueError):
            build_slide_plan(bad, PLAN, notes={})

    # speaker_notes equal to content_summary -> rejected
    bad_notes_sum = copy.deepcopy(base)
    bad_notes_sum["pages"][1]["speaker_notes"] = (
        f"{bad_notes_sum['pages'][1]['content_summary']}。请看页面内容。"
    )
    bad_eq = copy.deepcopy(base)
    bad_eq["pages"][1]["speaker_notes"] = "仅用一句概括。分两组讲解物理机制。"
    bad_eq["pages"][1]["content_summary"] = "仅用一句概括。分两组讲解物理机制。"
    with pytest.raises(SlidePlanRuleError, match="content_summary"):
        build_slide_plan(bad_eq, PLAN, notes={})

    # speaker_notes equal to concatenated key_points -> rejected
    bad_concat = copy.deepcopy(base)
    bad_concat["pages"][0]["speaker_notes"] = "。".join(bad_concat["pages"][0]["key_points"]) + "。"
    with pytest.raises(SlidePlanRuleError, match="concatenate key_points"):
        build_slide_plan(bad_concat, PLAN, notes={})

    # single-sentence speaker_notes -> rejected (must be 2-5 sentences)
    bad_one_sent = copy.deepcopy(base)
    bad_one_sent["pages"][0]["speaker_notes"] = "这一页只讲核心概念定义。"
    with pytest.raises(SlidePlanRuleError, match="2-5"):
        build_slide_plan(bad_one_sent, PLAN, notes={})


def test_r8_3_ghost_deck_rejects_category_labels_and_duplicates():
    base = _valid_stage_b_payload()
    plan = build_slide_plan(base, PLAN, notes={})
    assert validate_ghost_deck(plan) == []

    for label in ("背景介绍", "概述：热岛成因", "Introduction", "城市热岛简介"):
        bad = copy.deepcopy(base)
        bad["pages"][1]["headline"] = label
        with pytest.raises(SlidePlanRuleError, match="headline|category"):
            build_slide_plan(bad, PLAN, notes={})

    dup = copy.deepcopy(base)
    dup["pages"][2]["headline"] = dup["pages"][1]["headline"]
    with pytest.raises(SlidePlanRuleError, match="duplicates"):
        build_slide_plan(dup, PLAN, notes={})


def test_r8_3_grouped_vs_ungrouped_layout_routing_and_headline_rendering():
    plan = build_slide_plan(_valid_stage_b_payload(), PLAN, notes={})

    # Two groups -> two-content eligible and renders group headings into respective columns
    opts_2 = eligible_layouts_for_page(plan.pages[1], 1, 3, [], [])
    assert "two-content" in opts_2 and "three-card" not in opts_2
    els_2 = build_layout_elements("two-content", plan.pages[1], plan.pages[1].key_points, [], [], [], [])
    by_id_2 = {e.element_id: e for e in els_2}
    assert by_id_2["title"].text == display_title(plan.pages[1])
    assert by_id_2["heading_panel_left"].text == "下垫面能量收支失衡"
    assert by_id_2["heading_panel_right"].text == "人为热源与通风受阻"
    assert "硬质沥青与混凝土" in by_id_2["bullets_left"].text
    assert "机动车辆通行与工业装置" in by_id_2["bullets_right"].text

    # Three groups -> three-card eligible and renders 3 group headings into 3 cards
    opts_3 = eligible_layouts_for_page(plan.pages[2], 2, 3, [], [])
    assert "three-card" in opts_3 and "two-content" not in opts_3
    els_3 = build_layout_elements("three-card", plan.pages[2], plan.pages[2].key_points, [], [], [], [])
    by_id_3 = {e.element_id: e for e in els_3}
    assert by_id_3["card_heading_0"].text == "生态冷源扩容"
    assert by_id_3["card_heading_1"].text == "材料反照率提升"
    assert by_id_3["card_heading_2"].text == "街区通风优化"

    # Ungrouped text-only slide must NEVER select two-content or three-card
    ungrouped = SlidePage(
        page_id="S004",
        page_role=PageRole.content,
        title="未分组页面",
        headline="多条独立发现适合采用单栏阅读秩序直接呈现。",
        goal="列出独立发现",
        key_points=[
            "第一条独立观察强调连续空间分布梯度规律",
            "第二条独立观察关注夜间降温速率缓慢现象",
            "第三条独立观察对比风速衰减系数与密度关系",
        ],
        groups=[],
        content_summary="无分组三点。",
        speaker_notes="这三条观察各自独立且不构成严格对照或并列分组。因此我们保持单栏垂直阅读顺次展开。",
        visual_intent="单栏清单",
    )
    opts_un = eligible_layouts_for_page(ungrouped, 1, 3, [], [])
    assert "two-content" not in opts_un
    assert "three-card" not in opts_un
    assert "single-column" in opts_un or "hero-statement" in opts_un


def test_r8_3_cover_occupancy_exemption_checks_content_not_just_layout_id():
    # Pure cover (title + 1 subtitle line, NO bullets) -> exempt
    pure_cover_page = SlidePage(
        page_id="S001",
        page_role=PageRole.cover,
        title="城市热岛效应研究进展",
        headline="城市下垫面改造与人为排放正在重塑都市夜间热环境。",
        goal="开场定位",
        key_points=["城市气候与建筑热环境专题报告"],
        content_summary="纯封面。",
        speaker_notes="欢迎参加今天的专题报告。接下来我将从观测事实与规划缓解两条线介绍城市热岛的最新研究脉络。",
        visual_intent="简洁封面",
    )
    pure_els = build_layout_elements("cover", pure_cover_page, pure_cover_page.key_points, [], [], [], [])
    pure_entry = PageDesignEntry(page_id="S001", layout_id="cover", layout_name="cover", elements=pure_els)
    pure_occ = score_occupancy(pure_entry)
    assert pure_occ.exempt is True and pure_occ.page_role == "cover"

    # Bullet-bearing cover (>= 2 bullets) -> NOT exempt, and clustered cover passes < 1.60in
    bullet_cover_page = SlidePage(
        page_id="S001",
        page_role=PageRole.cover,
        title="人工智能是什么",
        headline="人工智能通过数据模式学习来辅助人类处理复杂任务。",
        goal="概念开场",
        key_points=[
            "让计算机完成通常需要人类智能的复杂决策任务",
            "通过大规模样本规律归纳而非僵硬穷举规则实现适应性",
            "与科幻叙事中具备主观意识的虚构机器存在本质边界区分",
        ],
        content_summary="带要点的封面。",
        speaker_notes="开篇我们先明确人工智能在工程研究中的边界定义。随后重点关注机器如何在海量样例上学习并区分它与科幻想象之间的差别。",
        visual_intent="封面与要点",
    )
    bul_els = build_layout_elements("cover", bullet_cover_page, bullet_cover_page.key_points, [], [], [], [])
    bul_entry = PageDesignEntry(page_id="S001", layout_id="cover", layout_name="cover", elements=bul_els)
    bul_occ = score_occupancy(bul_entry)
    assert bul_occ.exempt is False and bul_occ.page_role == "content"
    assert bul_occ.passed and bul_occ.max_void_in < VOID_MAX_IN, bul_occ


def test_r8_3_compile_ops_and_pptx_write_speaker_notes_never_planning_summary(tmp_path):
    sp = build_slide_plan(_valid_stage_b_payload(), PLAN, notes={})
    reg = AssetRegistry(run_id=PLAN.run_id, assets=[], sources=[], summary="0/0")
    uir = UserInputRequests(run_id=PLAN.run_id, requests=[], summary="0 open (0 blocking)")
    mp = SlideAssetMapping(run_id=PLAN.run_id, mappings=[], unused_assets=[])

    design = run_stage_f(PLAN, sp, reg, uir, mp, tmp_path)
    ops = compile_deck_ops(sp, design, reg, uir, tmp_path)
    note_ops = [op for op in ops.ops if op.kind.value == "set_notes"]
    assert len(note_ops) == len(sp.pages)
    for sp_page, op in zip(sp.pages, note_ops):
        note_txt = str(op.params["text"])
        assert sp_page.speaker_notes.strip() in note_txt
        assert sp_page.content_summary not in note_txt
        assert "Speaker/planning summary:" not in note_txt
        assert "Layout notes:" not in note_txt

    # Verify read-only python-pptx stage_g also writes speaker_notes
    from pptv01.g_pptx.stage import run_stage_g

    pptx_path = run_stage_g(sp, design, reg, uir, tmp_path)
    prs = Presentation(str(pptx_path))
    for sp_page, slide in zip(sp.pages, prs.slides):
        notes_txt = slide.notes_slide.notes_text_frame.text
        assert sp_page.speaker_notes.strip() in notes_txt
        assert sp_page.content_summary not in notes_txt
