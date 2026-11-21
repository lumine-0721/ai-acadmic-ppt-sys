"""R6-2 tests: SlideSpeak-ported occupancy scorer, fill_rate warn gate, cell-void detection, and QAReport integration."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from pptv01.contracts import (
    AssetRegistry,
    GateReport,
    PageDesign,
    SlidePlan,
    UserInputRequests,
    load_contract,
    write_contract,
)
from pptv01.g_pptx import run_stage_g
from pptv01.grading.gates import check_gates
from pptv01.h_qa.occupancy import CellInput, check_deck_occupancy, score_cell_occupancy
from pptv01.h_qa.stage import run_stage_h
from pptv01.renderers.protocol import MeasureResult, TextMeasurement

V01_ROOT = Path(__file__).resolve().parents[1]
SMOKE5_DESIGN_DIR = V01_ROOT / "tests" / "fixtures" / "smoke5_page_design"
RID = "20261121-000000-r6fill"

EXPECTED_SMOKE5_FLAGS: dict[str, list[str]] = {
    "T36": ["S003", "S004"],
    "T12": ["S004", "S005", "S006"],
    "T02": ["S002", "S004", "S005"],
    "T26": ["S004"],
}


@pytest.mark.parametrize("tid", ["T36", "T12", "T02", "T26"])
def test_r6_2_fill_rate_smoke5_exact_flags_and_exemptions(tid: str, tmp_path: Path):
    """fill_rate flags underfilled Smoke 5 pages (including bullet-bearing section pages) as hard fail."""
    design = load_contract(PageDesign, SMOKE5_DESIGN_DIR / f"{tid}.json")
    occ = check_deck_occupancy(design)

    flagged_ids = [r.page_id for r in occ.flagged_pages]
    assert flagged_ids == EXPECTED_SMOKE5_FLAGS[tid], f"{tid} unexpected fill_rate flags: {occ.detail}"

    for r in occ.pages:
        if r.layout_id == "cover":
            assert r.exempt is True
            assert r.exempt_reason
            assert r.passed is True
        else:
            # Smoke 5 non-cover pages all have bullets or multiple body blocks -> role=content (never exempt)
            assert r.exempt is False

    # Wire through check_gates: underfilled decks fail fill_rate hard gate
    write_contract(design, tmp_path / "page_design.json")
    rep = check_gates(tmp_path)
    gate = {r.gate: r for r in rep.results}["fill_rate"]
    assert gate.status == "fail"
    for pid in EXPECTED_SMOKE5_FLAGS[tid]:
        assert pid in gate.detail
    assert "bottom" in gate.detail or "middle" in gate.detail or "top" in gate.detail
    assert "in" in gate.detail


def test_r6_2_cell_void_and_sparse_cell_detection():
    """Tall card with single line at top triggers cell-void; well-filled card passes."""
    tall_empty_card = CellInput(
        cell_id="card_empty",
        height_in=4.50,
        area_in2=4.50 * 4.00,
        pad_in=0.18,
        rects_in=[(0.18, 0.72)],  # single header line only -> ~3.60in empty void below!
        text_area_in2=3.5 * 0.54,
        has_visual=False,
    )
    res_bad = score_cell_occupancy([tall_empty_card])
    assert not res_bad.filled
    assert any(f.kind == "cell-void" for f in res_bad.failures)

    # Very sparse card (area >= 2.89 in2 with < 3.5% text area) -> sparse-cell
    tiny_word_card = CellInput(
        cell_id="card_sparse",
        height_in=2.00,
        area_in2=6.00,
        pad_in=0.15,
        rects_in=[(0.15, 0.35)],
        text_area_in2=0.15,  # 2.5% < 3.5%
        has_visual=False,
    )
    res_sparse = score_cell_occupancy([tiny_word_card])
    assert not res_sparse.filled
    assert any(f.kind == "sparse-cell" for f in res_sparse.failures)

    # Well-filled card -> filled=True
    filled_card = CellInput(
        cell_id="card_good",
        height_in=3.00,
        area_in2=12.00,
        pad_in=0.15,
        rects_in=[(0.15, 1.35), (1.50, 2.75)],
        text_area_in2=6.5,
        has_visual=False,
    )
    res_good = score_cell_occupancy([filled_card])
    assert res_good.filled
    assert not res_good.failures


def test_r6_2_qa_report_records_occupancy_diagnostics(tmp_path: Path):
    """Stage H records occupancy details and fill_rate diagnostic line in qa_report.json."""
    from PIL import Image, ImageDraw
    from pptx import Presentation

    design = load_contract(PageDesign, SMOKE5_DESIGN_DIR / "T36.json")
    src_dir = V01_ROOT / "tests" / "fixtures" / "r6_theme_snapshot" / "T36"
    for fn in ("production_plan.json", "slide_plan.json", "asset_registry.json", "user_input_requests.json", "slide_asset_mapping.json"):
        shutil.copy2(src_dir / fn, tmp_path / fn)
    (tmp_path / "assets").mkdir(exist_ok=True)
    for p in (src_dir / "assets").glob("*.json"):
        shutil.copy2(p, tmp_path / "assets" / p.name)

    sp = load_contract(SlidePlan, tmp_path / "slide_plan.json")
    reg = load_contract(AssetRegistry, tmp_path / "asset_registry.json")
    uir = load_contract(UserInputRequests, tmp_path / "user_input_requests.json")
    write_contract(design, tmp_path / "page_design.json")
    deck_path = run_stage_g(sp, design, reg, uir, tmp_path)

    def _stub_render(pptx: Path, out_dir: Path) -> list[Path]:
        prs = Presentation(str(pptx))
        out_dir.mkdir(parents=True, exist_ok=True)
        out: list[Path] = []
        for i in range(1, len(prs.slides) + 1):
            im = Image.new("RGB", (640, 360), (245, 248, 252))
            ImageDraw.Draw(im).rectangle([20, 20, 200, 100], fill=(30, 64, 175))
            png = out_dir / f"slide_{i:03d}.png"
            im.save(png, "PNG")
            out.append(png)
        return out

    qa, _pngs = run_stage_h(
        deck_path,
        sp,
        tmp_path,
        design=design,
        reg=reg,
        uir=uir,
        render_fn=_stub_render,
    )
    assert len(qa.occupancy) == len(design.pages)
    assert any(line.startswith("fill_rate:") for line in qa.diagnostics)
    assert "S003" in qa.diagnostics[0] and "S004" in qa.diagnostics[0]

    # MeasureResult with short bound_h_pt catches an artificially tall box via live height
    meas = MeasureResult(
        renderer_name="powerpoint-com",
        renderer_version="16.0",
        measurements=[
            TextMeasurement(
                slide_index=2,
                page_id="S002",
                shape_id=i,
                shape_name=sname,
                allocated_w_pt=400.0,
                allocated_h_pt=237.6,
                bound_w_pt=380.0,
                bound_h_pt=36.0,  # 0.50 in real rendered text vs 3.30 in box!
            )
            for i, sname in enumerate(("step_1", "step_2"), 1)
        ],
    )
    occ_live = check_deck_occupancy(design, measure_result=meas)
    s2 = next(r for r in occ_live.pages if r.page_id == "S002")
    assert s2.used_live_measurements is True
    assert not s2.passed
    assert s2.max_void_in >= 1.60
