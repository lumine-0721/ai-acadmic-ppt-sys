"""R6-3 tests: content-shape layout eligibility, capacity contracts, sparse centered-cluster geometry, and diversity degradation."""

from __future__ import annotations

import hashlib
import io
import shutil
from pathlib import Path

import pytest
from PIL import Image

from pptv01.contracts import (
    AssetRegistry,
    CANVAS_W_IN,
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
    LAYOUT_CAPACITY_SPECS,
    get_layout_capacity,
    run_stage_f,
    select_deck_layouts,
    validate_page_geometry,
)
from pptv01.f_layout.layouts import SlotItem, build_layout_elements
from pptv01.g_build import compile_deck_ops
from pptv01.g_pptx import run_stage_g
from pptv01.grading.gates import check_gates
from pptv01.h_qa.occupancy import check_deck_occupancy, score_occupancy
from tests.conftest import GOOD_A

V01_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_ROOT = V01_ROOT / "tests" / "fixtures" / "r6_theme_snapshot"
RID = "20261121-000000-r6elig"


def _dummy_png(w: int = 800, h: int = 600, color: tuple[int, int, int] = (40, 90, 140)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (max(8, min(w, 128)), max(8, min(h, 128))), color).save(buf, "PNG")
    return buf.getvalue()


def _page(pid: str, title: str, key_points: list[str], *, goal: str = "说明核心要点", vi: str = "结构图") -> SlidePage:
    return SlidePage(
        page_id=pid,
        title=title,
        goal=goal,
        key_points=key_points,
        content_summary="规划备注（不渲染）",
        visual_intent=vi,
        asset_needs=[],
        status="provisional",
    )


@pytest.mark.parametrize("tid", ["T36", "T12", "T26", "T02"])
def test_r6_3_smoke5_replay_passes_fill_rate_and_all_gates(tid: str, tmp_path: Path):
    """Replaying Smoke 5 decks under R6-3 passes fill_rate on all non-exempt pages and all hard gates."""
    fx_dir = SNAPSHOT_ROOT / tid
    for fn in (
        "production_plan.json",
        "slide_plan.json",
        "asset_registry.json",
        "user_input_requests.json",
        "slide_asset_mapping.json",
    ):
        shutil.copy2(fx_dir / fn, tmp_path / fn)

    reg = load_contract(AssetRegistry, tmp_path / "asset_registry.json")
    (tmp_path / "assets").mkdir(exist_ok=True)
    updated_assets = []
    for idx, a in enumerate(reg.assets):
        if not a.file_path:
            updated_assets.append(a)
            continue
        dst = tmp_path / a.file_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        src_a = fx_dir / a.file_path
        if src_a.is_file():
            shutil.copy2(src_a, dst)
            updated_assets.append(a)
        else:
            # Generate unique tiny valid raster matching recorded dimensions + sha256 so full gates verify cleanly
            col = ((40 + idx * 29) % 255, (90 + idx * 43) % 255, (140 + idx * 19) % 255)
            data = _dummy_png(a.width or 800, a.height or 600, color=col)
            if dst.suffix.lower() in (".jpg", ".jpeg"):
                buf = io.BytesIO()
                Image.new("RGB", (max(8, min(a.width or 800, 128)), max(8, min(a.height or 600, 128))), col).save(buf, "JPEG")
                data = buf.getvalue()
            dst.write_bytes(data)
            updated_assets.append(a.model_copy(update={"sha256": hashlib.sha256(data).hexdigest()}))
    reg = reg.model_copy(update={"assets": updated_assets})
    write_contract(reg, tmp_path / "asset_registry.json")

    pp = load_contract(ProductionPlan, tmp_path / "production_plan.json")
    sp = load_contract(SlidePlan, tmp_path / "slide_plan.json")
    uir = load_contract(UserInputRequests, tmp_path / "user_input_requests.json")
    mp = load_contract(SlideAssetMapping, tmp_path / "slide_asset_mapping.json")

    pd = run_stage_f(pp, sp, reg, uir, mp, tmp_path)
    ops = compile_deck_ops(sp, pd, reg, uir, tmp_path)
    run_stage_g(sp, pd, reg, uir, tmp_path)

    occ = check_deck_occupancy(pd)
    assert occ.passed, f"{tid} fill_rate failed: {occ.detail}"
    lids = [p.layout_id for p in pd.pages]
    assert all(lids[i] != lids[i - 1] for i in range(1, len(lids))), f"{tid} adjacent repeat: {lids}"

    rep = check_gates(tmp_path)
    by = {r.gate: r for r in rep.results}
    assert by["fill_rate"].status == "pass", by["fill_rate"].detail
    if tid == "T36":
        assert by["layout_diversity"].status == "warn", by["layout_diversity"].detail
        assert "target 5 distinct layouts, got 4" in by["layout_diversity"].detail
    else:
        assert by["layout_diversity"].status == "pass", by["layout_diversity"].detail
    for g in (
        "no_fabricated_visuals",
        "placeholders_marked",
        "attribution_present",
        "mapped_visuals_placed",
    ):
        assert by[g].status == "pass", f"{tid} {g}: {by[g].detail}"
    assert len(ops.ops) >= len(sp.pages)


def test_r6_3_one_point_per_side_never_picks_comparison_and_unsourced_number_never_picks_big_number():
    """Ineligible comparison (1 pt/side), unsourced big-number, and 2-step timeline are NEVER forced."""
    pp = ProductionPlan(run_id=RID, source_utterance="制作一份5页主题展示。", **GOOD_A)
    pages = [
        _page("S001", "项目开篇总览", ["背景要点一", "核心目标二"]),
        _page("S002", "方案A与方案B优劣对比", ["方案A成本更低", "方案B速度更快"], goal="对比两者", vi="左右对比"),
        _page("S003", "市场规模与增长数据", ["市场规模达 85% 增长", "年复合增速超过 30 倍"], goal="展示核心数据指标", vi="大数字卡片"),
        _page("S004", "两步演进历程", ["第一阶段原型验证", "第二阶段规模部署"], goal="展示两步流程", vi="时间线流程"),
        _page("S005", "总结与展望", ["总结关键启示", "后续重点方向"], goal="总结观点", vi="总结"),
    ]
    lids_no_src = select_deck_layouts(
        pages,
        [[] for _ in pages],
        [[] for _ in pages],
        pp,
        page_citations=[[] for _ in pages],
    )
    assert lids_no_src[1] != "comparison", f"1-point-per-side wrongly selected comparison: {lids_no_src}"
    assert lids_no_src[2] != "big-number", f"unsourced numbers wrongly selected big-number: {lids_no_src}"
    assert lids_no_src[3] != "timeline", f"2-step slide wrongly selected timeline: {lids_no_src}"
    assert all(lids_no_src[i] != lids_no_src[i - 1] for i in range(1, len(lids_no_src)))

    # When S002 gets 4 points (2 per side), S003 gets a sourced citation, and S004 gets 3 steps -> ALL become eligible!
    eligible_pages = [
        _page("S001", "项目开篇总览", ["背景要点一", "核心目标二"]),
        _page("S002", "方案A与方案B对比", ["A优势1", "A优势2", "B优势1", "B优势2"], goal="对比优劣", vi="对比"),
        _page("S003", "核心实证统计数据", ["城区夜间温差达 3.5℃", "绿地降温效应显著"], goal="展示数据指标", vi="数字卡片"),
        _page("S004", "三阶段演进时间线", ["2018年提出概念", "2021年完成标准", "2024年规模落地"], goal="历史演进时间线", vi="时间线"),
        _page("S005", "总结与展望", ["核心启示", "未来方向"], goal="总结"),
    ]
    cits = [[], [], ["[A001] Oke Urban Heat Island (2017) DOI:10.1000/uhi"], [], []]
    lids_elig = select_deck_layouts(
        eligible_pages,
        [[] for _ in eligible_pages],
        [[] for _ in eligible_pages],
        pp,
        page_citations=cits,
    )
    assert lids_elig[1] == "comparison"
    assert lids_elig[2] == "big-number"
    assert lids_elig[3] == "timeline"
    assert len(set(lids_elig)) == 5


def test_r6_3_capacity_specs_and_sparse_centered_cluster_pass_fill_rate_without_font_shrink():
    """Sparse builds of comparison/big-number/timeline center cleanly in upper-middle and pass fill_rate."""
    assert set(LAYOUT_CAPACITY_SPECS) >= {
        "cover",
        "section",
        "comparison",
        "big-number",
        "timeline",
        "image-left",
        "image-right",
        "quote",
        "full-bleed",
        "needs-input-list",
    }
    comp_spec = get_layout_capacity("comparison")
    assert comp_spec.requires_two_sided_points == 2
    assert get_layout_capacity("big-number").requires_sourced_number is True
    assert get_layout_capacity("timeline").requires_ordered_steps == 3

    ph = [SlotItem("placeholder", None, "U001", None, "待补充：实证数据（U001）")]
    page = _page(
        "S001",
        "实证对比与关键数据",
        ["城市中心昼夜温差 2.8℃", "绿地覆盖率提升可缓解热岛"],
    )
    for lid in ("comparison", "big-number", "timeline"):
        notes: list[str] = []
        els = build_layout_elements(lid, page, page.key_points, [], ph, ["[A001] Ref (2024)"], notes)
        entry = PageDesignEntry(page_id="S001", layout_id=lid, layout_name=lid, elements=els, canvas_width_in=CANVAS_W_IN)
        assert not validate_page_geometry(entry), f"{lid} failed geometry"
        occ = score_occupancy(entry)
        assert occ.passed, f"{lid} sparse centered-cluster failed fill_rate: {occ.detail}"
        assert any("sparse centered-cluster" in n for n in notes), notes
        # Verify font sizes were never shrunk below standard readable scale
        body_pts = [e.font_size_pt for e in els if e.kind in (ElementKind.body, ElementKind.bullet) and e.font_size_pt]
        assert min(body_pts) >= 17.0
