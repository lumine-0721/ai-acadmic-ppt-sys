"""R7-2 tests: two-content/three-card/hero-statement variants, capacity-driven selection, comparison & diversity gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pptv01.contracts import (
    CANVAS_W_IN,
    AssetRegistry,
    ElementKind,
    PageDesignEntry,
    ProductionPlan,
    SlideAssetMapping,
    SlidePage,
    SlidePlan,
    UserInputRequests,
    load_contract,
    write_contract,
)
from pptv01.f_layout import (
    run_stage_f,
    validate_page_geometry,
)
from pptv01.f_layout.capacity import classify_density, get_layout_capacity
from pptv01.f_layout.layouts import (
    SlotItem,
    build_layout_elements,
    eligible_layouts_for_page,
    is_comparison_eligible,
    partition_comparison_sides,
)
from pptv01.grading.gates import _check_layout_diversity_gate
from pptv01.h_qa.occupancy import VOID_MAX_IN, score_occupancy
from pptv01.renderers.protocol import TextMeasurement
from tests.conftest import GOOD_A

V01_ROOT = Path(__file__).resolve().parents[1]
SMOKE6_FIX = V01_ROOT / "tests" / "fixtures" / "smoke6_page_design"
SNAPSHOT_ROOT = V01_ROOT / "tests" / "fixtures" / "r6_theme_snapshot"
RID = "20261122-000001-r7r2"


def _page(
    pid: str,
    title: str,
    pts: list[str],
    *,
    goal: str = "说明核心内容",
    vi: str = "文字版式",
) -> SlidePage:
    return SlidePage(
        page_id=pid,
        title=title,
        goal=goal,
        key_points=pts,
        content_summary="规划备注（不渲染）",
        visual_intent=vi,
        asset_needs=[],
        source_refs=["R001"],
        status="provisional",
    )


def _sim_com_measurements(entry: PageDesignEntry) -> dict[str, TextMeasurement]:
    """Simulate strict PowerPoint COM TextFrame2.BoundHeight shrinking each text block by its actual line count."""
    out: dict[str, TextMeasurement] = {}
    for idx, el in enumerate(entry.elements, 1):
        if el.kind not in (ElementKind.title, ElementKind.body, ElementKind.bullet, ElementKind.caption, ElementKind.footer):
            continue
        lines = [ln for ln in (el.text or "").splitlines() if ln.strip()] or ["x"]
        pt = float(el.font_size_pt or 18.0)
        bound_h_pt = max(pt * 1.15, len(lines) * pt * 1.18)
        out[el.element_id] = TextMeasurement(
            slide_index=1,
            page_id=entry.page_id,
            shape_id=idx,
            shape_name=el.element_id,
            allocated_w_pt=round(el.w_in * 72.0, 2),
            allocated_h_pt=round(el.h_in * 72.0, 2),
            bound_w_pt=round(el.w_in * 70.0, 2),
            bound_h_pt=round(bound_h_pt, 2),
            font_size_pt=pt,
            text_length=len(el.text or ""),
        )
    return out


def _assert_b2_verbatim(page: SlidePage, entry: PageDesignEntry) -> None:
    allowed = {page.title.strip()} | {kp.strip() for kp in page.key_points if kp.strip()}
    for el in entry.elements:
        if el.kind not in (ElementKind.title, ElementKind.body, ElementKind.bullet):
            continue
        for raw in (el.text or "").splitlines():
            clean = raw.lstrip("•-*0123456789.、) ").strip()
            if not clean:
                continue
            assert clean in allowed, f"invented text {clean!r} in {entry.layout_id}: allowed={allowed}"


def test_r7_2_comparison_eligibility_rejects_flat_lists_and_accepts_grouped_sides():
    p_1v1 = _page("S002", "两者对比", ["方案A成本低", "方案B速度快"], vi="对比")
    assert not is_comparison_eligible(p_1v1)

    p_flat4 = _page("S003", "系统四项特性", ["响应延迟低于50ms", "支持并发流处理", "兼容多源异构数据", "自动故障恢复"])
    assert not is_comparison_eligible(p_flat4)

    p_grouped = _page(
        "S004",
        "方案A与方案B对比",
        ["方案A: 推理耗时更短", "方案A: 部署显存更低", "方案B: 长文本精度更高", "方案B: 多模态扩展强"],
    )
    assert is_comparison_eligible(p_grouped)
    sides = partition_comparison_sides(p_grouped)
    assert sides is not None and len(sides[0]) == 2 and len(sides[1]) == 2

    p_interleaved = _page("S005", "方案优劣比较", ["优势：准确率达95%", "局限：依赖标注样本", "优势：开源可控", "风险：长尾场景波动"])
    assert is_comparison_eligible(p_interleaved)

    vis2 = [
        SlotItem("image", "A001", None, "assets/a.png", "A", width_px=800, height_px=600),
        SlotItem("image", "A002", None, "assets/b.png", "B", width_px=800, height_px=600),
    ]
    assert is_comparison_eligible(p_flat4, vis2)


def test_r7_2_capacity_contracts_exclude_underfilled_layouts_and_drop_section_quote_on_bullets():
    p_2pts = _page("S002", "双要点核心原则", ["模型先学习局部边缘特征", "深层网络逐步组合全局语义"])
    elig_2 = eligible_layouts_for_page(p_2pts, 1, 5, [], [])
    assert "three-card" not in elig_2
    assert "timeline" not in elig_2
    assert "comparison" not in elig_2
    assert "section" not in elig_2
    assert "quote" not in elig_2
    assert "two-content" in elig_2
    assert "hero-statement" in elig_2

    p_1pt = _page("S002", "第二章 方法总览", ["下一部分将介绍实验设计与评估基准"], vi="章节分隔")
    elig_1 = eligible_layouts_for_page(p_1pt, 1, 5, [], [])
    assert "two-content" not in elig_1
    assert "three-card" not in elig_1
    assert "hero-statement" not in elig_1
    assert "section" in elig_1 or "quote" in elig_1


@pytest.mark.parametrize(
    ("lid", "density_target", "pts", "with_vis", "with_ph"),
    [
        ("two-content", "sparse", ["减少冗余停止词并统一编码", "剔除异常缺失值保证样本质量"], False, False),
        ("two-content", "normal", ["数据采集阶段覆盖多时段样本", "清洗规则过滤缺失与重复记录", "特征工程提取时空维度变量", "验证集按时间窗口严格切分"], False, True),
        ("two-content", "dense", [f"核心实验观察要点第{i}条：记录完整误差分布与收敛稳定性变化" for i in range(1, 8)], False, False),
        ("two-content", "normal", ["多尺度特征提取保持空间细节", "轻量化分支显著降低推理耗时", "消融实验验证核心模块必要性"], True, False),
        ("three-card", "sparse", ["采集原始文本语料", "构建词向量与句法索引", "训练分类与检索模型"], False, True),
        ("three-card", "normal", ["模块一支持实时信号接入", "模块二负责特征对齐归一化", "模块三执行增量在线更新", "模块四提供质量监控告警", "模块五导出标准评估报告"], False, False),
        ("three-card", "dense", [f"支柱{i}说明：覆盖全流程可追溯规范与跨平台兼容性验证细节" for i in range(1, 7)], False, False),
        ("three-card", "sparse", ["视觉分支编码纹理", "文本分支对齐语义", "融合头输出置信分数"], True, False),
        ("hero-statement", "sparse", ["大模型能力跃升源于规模法则与高质量反馈对齐的协同驱动", "预训练语料配比直接决定推理泛化上限"], False, False),
        ("hero-statement", "sparse", ["机制设计核心在于降低沟通摩擦", "统一接口减少模块耦合", "异步队列提升峰值吞吐"], False, True),
        ("hero-statement", "normal", ["城市绿色基础设施能显著缓解夜间热岛效应", "公园绿地与水体协同产生冷岛扩散", "屋顶绿化降低建筑表面蓄热", "通风廊道改善近地面微气候环流"], False, False),
        ("hero-statement", "dense", ["分层分布式架构兼顾低延迟在线服务与大规模离线批处理一致性", "无状态网关实现水平弹性扩容", "读写分离隔离分析负载干扰", "本地缓存降低热点键穿透压力", "全链路追踪快速定位尾部超时"], False, False),
        ("hero-statement", "normal", ["核心结论依赖实测对照组验证", "长期观测消除季节性偏差"], True, False),
    ],
)
def test_r7_2_new_content_layouts_pass_geometry_b2_and_live_com_occupancy(
    lid: str,
    density_target: str,
    pts: list[str],
    with_vis: bool,
    with_ph: bool,
):
    if not with_vis:
        spec = get_layout_capacity(lid)
        assert classify_density(len(pts), spec, total_chars=sum(len(p) for p in pts)) == density_target

    vis = (
        [SlotItem("image", "A001", None, "assets/v.png", "Figure 1", width_px=1200, height_px=800, attribution="A001: Fig 1 (CC-BY)")]
        if with_vis
        else []
    )
    ph = (
        [SlotItem("placeholder", "A002", "U001", None, "待补充：实验结构示意图（U001）")]
        if with_ph
        else []
    )
    page = _page("S003", "系统核心机制与阶段结论分析", pts)
    notes: list[str] = []
    els = build_layout_elements(lid, page, pts, vis, ph, [], notes)
    entry = PageDesignEntry(
        page_id="S003",
        layout_id=lid,
        layout_name=lid,
        elements=els,
        canvas_width_in=CANVAS_W_IN,
    )

    errs = validate_page_geometry(entry)
    assert not errs, f"{lid} ({density_target}) geometry errors: {errs}"
    _assert_b2_verbatim(page, entry)

    occ_static = score_occupancy(entry)
    assert not occ_static.exempt, f"{lid} must not be exempt"
    assert occ_static.passed and occ_static.max_void_in < VOID_MAX_IN, f"static void: {occ_static}"

    occ_com = score_occupancy(entry, measured_boxes=_sim_com_measurements(entry))
    assert occ_com.passed and occ_com.max_void_in < VOID_MAX_IN, f"live COM void: {occ_com}"


def test_r7_2_smoke6_flagged_five_pages_pass_non_exempt_under_stage_f(tmp_path: Path):
    """R7-1/R7-2 bridge check: reconstruct T36 (S003/S005) & T12 (S002/S005/S007) content from smoke6 -> non-exempt pass."""
    targets = {"T36": ("S003", "S005"), "T12": ("S002", "S005", "S007")}
    for tid, flag_pids in targets.items():
        smoke6_pages = json.loads((SMOKE6_FIX / f"{tid}.json").read_text(encoding="utf-8"))["pages"]
        sp_pages: list[SlidePage] = []
        for idx, p in enumerate(smoke6_pages):
            title = ""
            pts: list[str] = []
            for el in p["elements"]:
                if el["kind"] == "title":
                    title = (el.get("text") or "").strip()
                elif el["kind"] in ("bullet", "body"):
                    for ln in (el.get("text") or "").splitlines():
                        clean = ln.lstrip("•-* ").strip()
                        if clean:
                            pts.append(clean)
            sp_pages.append(
                SlidePage(
                    page_id=p["page_id"],
                    title=title or f"Slide {idx + 1}",
                    goal=title or "内容页",
                    key_points=pts,
                    content_summary="规划备注（不渲染）",
                    visual_intent="内容呈现",
                    asset_needs=[],
                    source_refs=["R001"],
                    status="provisional",
                )
            )

        sp = SlidePlan(run_id=RID, status="provisional", narrative=f"{tid} replay", pages=sp_pages)
        pp = ProductionPlan(run_id=RID, source_utterance=f"{tid} {len(sp_pages)}页演示。", **GOOD_A)
        reg = AssetRegistry(run_id=RID, assets=[])
        uir = UserInputRequests(run_id=RID, requests=[], summary="0 open")
        mp = SlideAssetMapping(run_id=RID, mappings=[], unused_assets=[])
        run_d = tmp_path / tid
        run_d.mkdir(parents=True, exist_ok=True)
        write_contract(pp, run_d / "production_plan.json")
        write_contract(sp, run_d / "slide_plan.json")
        write_contract(reg, run_d / "asset_registry.json")
        write_contract(uir, run_d / "user_input_requests.json")
        write_contract(mp, run_d / "slide_asset_mapping.json")

        pd = run_stage_f(pp, sp, reg, uir, mp, run_d)
        by_pid = {pg.page_id: pg for pg in pd.pages}
        for pid in flag_pids:
            pg = by_pid[pid]
            assert pg.layout_id not in ("section", "quote"), f"{tid} {pid} fell back to {pg.layout_id}"
            occ_static = score_occupancy(pg)
            assert not occ_static.exempt and occ_static.passed, f"{tid} {pid} static failed: {occ_static}"
            occ_com = score_occupancy(pg, measured_boxes=_sim_com_measurements(pg))
            assert occ_com.passed, f"{tid} {pid} simulated COM failed: {occ_com}"


def test_r7_2_layout_diversity_gate_warns_honestly_instead_of_passing_when_below_five(tmp_path: Path):
    """When 5-page deck only qualifies for 4 distinct layouts, gate returns 'warn' with explicit reason, never 'pass'."""
    fx_dir = SNAPSHOT_ROOT / "T36"
    for fn in ("production_plan.json", "slide_plan.json", "asset_registry.json", "user_input_requests.json", "slide_asset_mapping.json"):
        (tmp_path / fn).write_text((fx_dir / fn).read_text(encoding="utf-8"), encoding="utf-8")
    pp = load_contract(ProductionPlan, tmp_path / "production_plan.json")
    sp = load_contract(SlidePlan, tmp_path / "slide_plan.json")
    reg = load_contract(AssetRegistry, tmp_path / "asset_registry.json")
    uir = load_contract(UserInputRequests, tmp_path / "user_input_requests.json")
    mp = load_contract(SlideAssetMapping, tmp_path / "slide_asset_mapping.json")

    pd = run_stage_f(pp, sp, reg, uir, mp, tmp_path)
    gate = _check_layout_diversity_gate(tmp_path, pd, sp=sp, reg=reg, uir=uir, mapping=mp)
    assert gate.status == "warn", f"expected warn for 4/5 distinct layouts, got {gate}"
    assert "target 5" in gate.detail and "realizable eligible: 4" in gate.detail
