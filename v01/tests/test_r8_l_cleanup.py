from __future__ import annotations

from pptv01.contracts import ElementKind, PageRole, PointGroup, SlidePage
from pptv01.f_layout.layouts import SlotItem, build_layout_elements


def _has_content_under_bar(bar, elements) -> bool:
    """True when at least one non-decorative text or visual element sits below `bar` in the same horizontal column."""
    bx0, bx1 = bar.x_in, bar.x_in + bar.w_in
    by1 = bar.y_in + bar.h_in
    for el in elements:
        if el.kind is ElementKind.shape and el.role != "diagram_json":
            continue
        if el.kind is ElementKind.footer:
            continue
        ex0, ex1 = el.x_in, el.x_in + el.w_in
        horiz_overlap = min(bx1, ex1) - max(bx0, ex0)
        if horiz_overlap >= 0.45 * min(bar.w_in, el.w_in) and el.y_in >= by1 - 0.02 and el.y_in <= by1 + 0.50:
            return True
    return False


def test_r8_l_every_comparison_accent_bar_has_content_directly_below():
    # Case 1: Single-point comparison must not emit an orphan right accent bar
    single_pt_page = SlidePage(
        page_id="S006",
        title="单侧要点",
        page_role=PageRole.content,
        headline="只有一组要点时右侧不得出现空表头色条。",
        goal="对比展示",
        key_points=["城区植被与硬质铺装比例对夜间冷却具有直接决定性影响"],
        content_summary="单组要点。",
        speaker_notes="这一页只强调一个关键对照结论。因此右侧没有文字时不应画出空的表头装饰栏。",
        visual_intent="对比",
    )
    els_single = build_layout_elements("comparison", single_pt_page, single_pt_page.key_points, [], [], [], [])
    bars_single = [el for el in els_single if el.element_id.startswith("accent_col_")]
    assert [b.element_id for b in bars_single] == ["accent_col_left"]
    assert all(_has_content_under_bar(b, els_single) for b in bars_single)

    # Case 2: Comparison with 2 visuals under full-width intro bullets (Smoke 7 T02 S006 pattern)
    vis_page = SlidePage(
        page_id="S006",
        title="实景对比",
        page_role=PageRole.content,
        headline="高绿覆街道与硬化街道在热像分布上呈现鲜明反差。",
        goal="双图对比",
        key_points=["两组观测地点位于同一气象观测站相邻辐射圈内"],
        content_summary="两图并排。",
        speaker_notes="我们把两张实测照片并排呈现。左侧绿地片区表面温度明显低，右侧硬化道路储热更久。",
        visual_intent="两张实测图并排对照",
    )
    vis2 = [
        SlotItem("image", "A001", None, "assets/v1.jpg", "城市公园", width_px=1200, height_px=800),
        SlotItem("image", "A002", None, "assets/v2.jpg", "中心街道", width_px=1200, height_px=800),
    ]
    els_vis = build_layout_elements("comparison", vis_page, vis_page.key_points, vis2, [], [], [])
    bars_vis = [el for el in els_vis if el.element_id.startswith("accent_col_")]
    assert len(bars_vis) == 2
    for b in bars_vis:
        assert _has_content_under_bar(b, els_vis), f"orphan bar {b.element_id} at y={b.y_in}"


def test_r8_l_citations_anchor_at_content_band_bottom_never_mid_canvas():
    sparse_page = SlidePage(
        page_id="S004",
        title="引用锚定测试",
        page_role=PageRole.content,
        headline="引文必须锚定在内容区底部，不能悬浮在页面中段留白处。",
        goal="检验引文位置",
        key_points=[
            "核心结论指出观测偏差随季节湿度变化波动",
            "不同下垫面采样深度会直接影响热通量反演结果",
        ],
        groups=[
            PointGroup(heading="误差来源分析", points=["核心结论指出观测偏差随季节湿度变化波动"]),
            PointGroup(heading="采样条件约束", points=["不同下垫面采样深度会直接影响热通量反演结果"]),
        ],
        content_summary="双栏极简测试。",
        speaker_notes="这里对比观测中的两组误差约束条件。请注意页面底部的参考文献出处条始终齐整锚在版面下方。",
        visual_intent="引用页",
    )
    ph = [SlotItem("placeholder", "A002", "U001", None, "待补充：温度统计图（U001）")]
    citations = ["[A001] Oke TR (1982), Q J R Meteorol Soc"]

    for lid in ("two-content", "single-column", "hero-statement", "comparison"):
        els_no_ph = build_layout_elements(lid, sparse_page, sparse_page.key_points, [], [], citations, [])
        foot_no_ph = next(e for e in els_no_ph if e.kind is ElementKind.footer and e.role == "anchored_citation")
        assert foot_no_ph.y_in >= 6.50, f"{lid} (no ph) citation floated at y={foot_no_ph.y_in}"

        els_ph = build_layout_elements(lid, sparse_page, sparse_page.key_points, [], ph, citations, [])
        foot_ph = next(e for e in els_ph if e.kind is ElementKind.footer and e.role == "anchored_citation")
        assert 5.85 <= foot_ph.y_in <= 6.20, f"{lid} (with ph) citation not anchored above tray: y={foot_ph.y_in}"
