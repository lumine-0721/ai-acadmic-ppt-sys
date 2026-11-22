"""R8-1 regression & unit tests: zero diagonal stagger, upper-middle clustering, layout swap on underflow, and 2D/cell occupancy."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

from pptv01.contracts import (
    CANVAS_W_IN,
    AssetRegistry,
    DesignElement,
    ElementKind,
    PageDesign,
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
    eligible_layouts_for_page,
    run_stage_f,
    validate_page_geometry,
)
from pptv01.f_layout.capacity import wrap_mixed_text
from pptv01.f_layout.layouts import build_layout_elements
from pptv01.grading.gates import check_gates
from pptv01.h_qa.occupancy import VOID_MAX_IN, check_deck_occupancy, score_occupancy
from pptv01.renderers.protocol import TextMeasurement
from tests.conftest import GOOD_A

V01_ROOT = Path(__file__).resolve().parents[1]
SMOKE7_DESIGN_DIR = V01_ROOT / "tests" / "fixtures" / "smoke7_page_design"
R6_SNAPSHOT_ROOT = V01_ROOT / "tests" / "fixtures" / "r6_theme_snapshot"

EXPECTED_SMOKE7_STAGGERED_FLAGS: dict[str, set[str]] = {
    "T36": {"S003", "S004", "S005"},
    "T12": {"S002", "S005", "S006"},
    "T02": {"S002", "S003", "S005"},
    "T26": {"S008"},
}


def _sim_com_wrapped(entry: PageDesignEntry) -> dict[str, TextMeasurement]:
    out: dict[str, TextMeasurement] = {}
    for idx, el in enumerate(entry.elements, 1):
        if el.kind not in (ElementKind.title, ElementKind.body, ElementKind.bullet, ElementKind.caption, ElementKind.footer):
            continue
        pt = float(el.font_size_pt or 18.0)
        lines = wrap_mixed_text(el.text or "", el.w_in, pt) or ["x"]
        bound_h_pt = max(pt * 1.15, len(lines) * pt * 1.20 + 7.2)
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


def test_r8_1_no_stagger_word_or_threshold_hack_in_layouts_source():
    src = (V01_ROOT / "src" / "pptv01" / "f_layout" / "layouts.py").read_text(encoding="utf-8")
    assert "stagger" not in src.lower()
    assert "staircase" not in src.lower()
    assert "1.48" not in src


@pytest.mark.parametrize("tid", ["T36", "T12", "T26", "T02"])
def test_r8_1_smoke7_staggered_pages_now_fail_2d_occupancy_and_fill_rate_gate(tid: str, tmp_path: Path):
    """Frozen Smoke 7 fixtures MUST fail check_deck_occupancy() and fill_rate gate on every staggered three-card / two-content page."""
    design = load_contract(PageDesign, SMOKE7_DESIGN_DIR / f"{tid}.json")
    occ = check_deck_occupancy(design)
    flagged = {r.page_id for r in occ.flagged_pages}
    assert EXPECTED_SMOKE7_STAGGERED_FLAGS[tid].issubset(flagged), f"{tid}: got {flagged}, detail={occ.detail}"
    assert occ.passed is False

    # T02 S006 comparison with 2 photos must remain passing
    if tid == "T02":
        s6 = next(r for r in occ.pages if r.page_id == "S006")
        assert s6.passed is True

    write_contract(design, tmp_path / "page_design.json")
    rep = check_gates(tmp_path)
    fill_gate = next(r for r in rep.results if r.gate == "fill_rate")
    assert fill_gate.status == "fail"
    assert rep.overall == "fail"


@pytest.mark.parametrize("tid", ["T36", "T12", "T26", "T02"])
def test_r8_1_replayed_smoke_decks_have_aligned_cards_and_pass_occupancy(tid: str, tmp_path: Path):
    """Re-running Stage F on Smoke decks yields zero stagger, aligned cards, and full static + COM occupancy pass."""
    fx_dir = R6_SNAPSHOT_ROOT / tid
    for fn in ("production_plan.json", "slide_plan.json", "asset_registry.json", "user_input_requests.json", "slide_asset_mapping.json"):
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
        src = fx_dir / a.file_path
        if src.is_file():
            shutil.copy2(src, dst)
            updated_assets.append(a)
        else:
            col = ((35 + idx * 31) % 255, (75 + idx * 37) % 255, (125 + idx * 23) % 255)
            buf = io.BytesIO()
            Image.new("RGB", (max(8, min(a.width or 800, 128)), max(8, min(a.height or 600, 128))), col).save(
                buf, "JPEG" if dst.suffix.lower() in (".jpg", ".jpeg") else "PNG"
            )
            data = buf.getvalue()
            dst.write_bytes(data)
            updated_assets.append(a.model_copy(update={"sha256": hashlib.sha256(data).hexdigest()}))
    reg = reg.model_copy(update={"assets": updated_assets})
    write_contract(reg, tmp_path / "asset_registry.json")

    pd = run_stage_f(
        load_contract(ProductionPlan, tmp_path / "production_plan.json"),
        load_contract(SlidePlan, tmp_path / "slide_plan.json"),
        reg,
        load_contract(UserInputRequests, tmp_path / "user_input_requests.json"),
        load_contract(SlideAssetMapping, tmp_path / "slide_asset_mapping.json"),
        tmp_path,
    )

    occ = check_deck_occupancy(pd)
    assert occ.passed, f"{tid} failed static occupancy: {occ.detail}"
    for pg in pd.pages:
        assert not any("stagger" in (pg.notes or "").lower() for _ in [0])
        assert score_occupancy(pg, measured_boxes=_sim_com_wrapped(pg)).passed, f"{tid}/{pg.page_id} failed COM occupancy"


def test_r8_1_tall_card_with_single_line_fails_cell_void_and_diagonal_two_box_fails_column_void():
    # 1. Tall card (4.2 in) containing one short line -> fails cell-void automatically
    tall_card_page = PageDesignEntry(
        page_id="S002",
        layout_id="three-card",
        layout_name="three-card",
        elements=[
            DesignElement(element_id="title", kind=ElementKind.title, x_in=0.65, y_in=0.48, w_in=12.03, h_in=0.90, z_index=2, text="核心维度", font="Microsoft YaHei", font_size_pt=29.0),
            DesignElement(element_id="bg_card_0", kind=ElementKind.shape, x_in=0.65, y_in=1.72, w_in=3.80, h_in=4.20, z_index=0, fill_hex="#F8FAFC", line_hex="#CBD5E1"),
            DesignElement(element_id="card_text_0", kind=ElementKind.bullet, x_in=0.83, y_in=1.94, w_in=3.44, h_in=0.65, z_index=2, text="短句一点", font="Microsoft YaHei", font_size_pt=18.0),
            DesignElement(element_id="bg_card_1", kind=ElementKind.shape, x_in=4.76, y_in=1.72, w_in=3.80, h_in=4.20, z_index=0, fill_hex="#F8FAFC", line_hex="#CBD5E1"),
            DesignElement(element_id="card_text_1", kind=ElementKind.bullet, x_in=4.95, y_in=1.94, w_in=3.44, h_in=0.65, z_index=2, text="短句二点", font="Microsoft YaHei", font_size_pt=18.0),
            DesignElement(element_id="bg_card_2", kind=ElementKind.shape, x_in=8.88, y_in=1.72, w_in=3.80, h_in=4.20, z_index=0, fill_hex="#F8FAFC", line_hex="#CBD5E1"),
            DesignElement(element_id="card_text_2", kind=ElementKind.bullet, x_in=9.06, y_in=1.94, w_in=3.44, h_in=0.65, z_index=2, text="短句三点", font="Microsoft YaHei", font_size_pt=18.0),
        ],
    )
    res_cell = score_occupancy(tall_card_page)
    assert res_cell.passed is False
    assert any(f.kind in ("cell-void", "sparse-cell") for f in res_cell.cell_failures)

    # 2. Diagonal two-box slide (overlapping vertically in projection, blank top-right & bottom-left) -> fails column_void
    diag_page = PageDesignEntry(
        page_id="S004",
        layout_id="two-content",
        layout_name="two-content",
        elements=[
            DesignElement(element_id="title", kind=ElementKind.title, x_in=0.65, y_in=0.48, w_in=12.03, h_in=0.90, z_index=2, text="双栏对比", font="Microsoft YaHei", font_size_pt=29.0),
            DesignElement(element_id="bullets_left", kind=ElementKind.bullet, x_in=0.65, y_in=1.60, w_in=5.50, h_in=2.40, z_index=2, text="• 左上块内容第一行\n• 左上块内容第二行", font="Microsoft YaHei", font_size_pt=18.0),
            DesignElement(element_id="bullets_right", kind=ElementKind.bullet, x_in=7.00, y_in=4.20, w_in=5.50, h_in=2.40, z_index=2, text="• 右下块内容第一行\n• 右下块内容第二行", font="Microsoft YaHei", font_size_pt=18.0),
        ],
    )
    res_diag = score_occupancy(diag_page)
    assert res_diag.passed is False
    assert res_diag.max_col_void_in >= VOID_MAX_IN


def test_r8_1_aligned_three_card_rich_passes_and_ultra_sparse_swaps_layout():
    rich_pts = [
        "模块一接入多源工业传感流数据并完成跨通道时间窗口对齐与异常值清洗过滤",
        "模块二构建异常波形自编码器并实现在线增量参数微调与轻量量化部署上线",
        "模块三生成设备健康评分、寿命预测曲线与维保优先级调度执行建议清单",
        "支持跨车间实时流式并发推理并将端到端推理响应延迟稳定控制在三十毫秒内",
        "提供版本化模型快速回滚机制与预测置信度可解释性审计追踪完整运行日志",
        "沉淀历史故障知识图谱与根因诊断样本库持续反哺后续多轮模型迭代优化升级",
    ]
    p_rich = SlidePage(
        page_id="S003",
        title="系统三支柱架构与能力闭环",
        goal="展示三支柱",
        key_points=rich_pts,
        content_summary="规划备注",
        visual_intent="三卡片",
        asset_needs=[],
        source_refs=["R001"],
        status="provisional",
    )
    els = build_layout_elements("three-card", p_rich, rich_pts, [], [], [], [])
    entry = PageDesignEntry(page_id="S003", layout_id="three-card", layout_name="three-card", elements=els, canvas_width_in=CANVAS_W_IN)
    assert not validate_page_geometry(entry)
    cards = [e for e in els if e.element_id.startswith("bg_card_")]
    assert len(cards) == 3
    # Aligned horizontal row: identical y_in and equal h_in
    assert len({c.y_in for c in cards}) == 1
    assert len({c.h_in for c in cards}) == 1
    assert score_occupancy(entry).passed
    assert score_occupancy(entry, measured_boxes=_sim_com_wrapped(entry)).passed

    # Ultra-sparse 3-short-line page swaps out of three-card & two-content to vertical layouts
    p_sparse = SlidePage(
        page_id="S003",
        title="三个应用领域",
        goal="列举",
        key_points=["语音识别", "搜索推荐", "辅助科研"],
        content_summary="规划备注",
        visual_intent="三卡片",
        asset_needs=[],
        source_refs=[],
        status="provisional",
    )
    elig = eligible_layouts_for_page(p_sparse, 2, 5, [], [])
    assert "three-card" not in elig
    assert "two-content" not in elig
    assert "single-column" in elig or "hero-statement" in elig
