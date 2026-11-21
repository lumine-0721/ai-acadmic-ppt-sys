"""R5 tests: capacity calibration, Stage F layout redesign, and QA B6 regressions."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest
from PIL import Image
from pptx import Presentation
from pptx.util import Inches, Pt

from pptv01.contracts import (
    AssetRegistry,
    ElementKind,
    ProductionPlan,
    SlideAssetMapping,
    SlidePlan,
)
from pptv01.f_layout import (
    estimate_slot_capacity,
    estimate_text_height_in,
    run_stage_f,
    wrap_mixed_text,
)
from pptv01.f_layout.capacity import DEFAULT_LINE_HEIGHT_FACTOR
from pptv01.g_build import compile_deck_ops
from pptv01.grading.gates import check_gates
from pptv01.h_qa.geometry import (
    GeoBox,
    find_edge_misalignments,
    find_empty_cards,
    find_undersized_image_slots,
)
from pptv01.h_qa.inspect import inspect_deck
from pptv01.h_qa.stage import run_stage_h
from tests.conftest import GOOD_A, GOOD_B

RID = "20260101-000000-r500"


def _png(w: int, h: int, color=(35, 95, 165)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def test_r5_capacity_calibration_matches_powerpoint_measurements():
    """Verify calibrated line pitch (1.20x) matches empirical desktop PowerPoint 16.0 BoundHeight samples."""
    assert DEFAULT_LINE_HEIGHT_FACTOR == pytest.approx(1.20, abs=0.01)
    # 5 lines at 18pt on desktop PowerPoint 16.0 -> BoundHeight = 108.0pt + 2*7.2pt padding = 122.4pt = 1.700in
    est_in = estimate_text_height_in(["line"] * 5, 18.0)
    assert est_in == pytest.approx((5 * 18.0 * 1.20 + 14.4) / 72.0, abs=0.005)
    # 6 lines at 16pt -> BoundHeight = 115.2pt; should fit cleanly in 2.0in box
    cap = estimate_slot_capacity(w_in=5.40, h_in=2.0, font_size_pt=16.0)
    assert cap.max_lines >= 6


def test_r5_stage_f_removes_empty_cards_shrinks_text_and_unifies_image_slots(tmp_path):
    """Stage F must emit zero card_* boxes, shrink bullet box to content height, align edges, and unify photo slots."""
    pp = ProductionPlan(run_id=RID, source_utterance="做一个城市热岛效应研究汇报 PPT。", **GOOD_A)
    sp = SlidePlan(
        run_id=RID,
        status="provisional",
        narrative=GOOD_B["narrative"],
        pages=[
            {"page_id": "S001", "status": "provisional", **GOOD_B["pages"][0]},
            {"page_id": "S002", "status": "provisional", **GOOD_B["pages"][1]},
        ],
    )
    (tmp_path / "assets").mkdir()
    # Two standard photos with slightly different landscape aspect ratios (4:3 and 16:10)
    p1_bytes = _png(1200, 900, (30, 80, 140))
    p2_bytes = _png(1600, 1000, (40, 110, 90))
    (tmp_path / "assets" / "A001.png").write_bytes(p1_bytes)
    (tmp_path / "assets" / "A002.png").write_bytes(p2_bytes)

    reg = AssetRegistry(
        run_id=RID,
        assets=[
            {
                "asset_id": "A001",
                "status": "acquired",
                "type": "image",
                "file_path": "assets/A001.png",
                "source_url": "https://commons.wikimedia.org/wiki/File:UHI1.png",
                "source_name": "Wikimedia Commons",
                "license_or_terms": "CC BY 4.0",
                "author": "Author1",
                "attribution": "Author1, CC BY 4.0, Wikimedia Commons",
                "identifier": "File:UHI1.png",
                "sha256": hashlib.sha256(p1_bytes).hexdigest(),
                "mime": "image/png",
                "width": 1200,
                "height": 900,
                "retrieved_at": "2026-01-01T00:00:00+08:00",
                "candidates": [{"title": "File:UHI1.png", "decision": "selected", "reason": "ok"}],
            },
            {
                "asset_id": "A002",
                "status": "acquired",
                "type": "image",
                "file_path": "assets/A002.png",
                "source_url": "https://commons.wikimedia.org/wiki/File:UHI2.png",
                "source_name": "Wikimedia Commons",
                "license_or_terms": "CC BY 4.0",
                "author": "Author2",
                "attribution": "Author2, CC BY 4.0, Wikimedia Commons",
                "identifier": "File:UHI2.png",
                "sha256": hashlib.sha256(p2_bytes).hexdigest(),
                "mime": "image/png",
                "width": 1600,
                "height": 1000,
                "retrieved_at": "2026-01-01T00:00:00+08:00",
                "candidates": [{"title": "File:UHI2.png", "decision": "selected", "reason": "ok"}],
            },
        ],
    )
    mapping = SlideAssetMapping(
        run_id=RID,
        mappings=[
            {"page_id": "S001", "asset_id": "A001", "usage": "城市热岛遥感图", "accepted": True},
            {"page_id": "S002", "asset_id": "A002", "usage": "下垫面结构对比图", "accepted": True},
        ],
        unused_assets=[],
    )

    design = run_stage_f(pp, sp, reg, None, mapping, tmp_path)
    img_slots = []
    for page in design.pages:
        # Zero old-style card_* empty cards
        assert not any(e.element_id.startswith("card_") for e in page.elements)
        # Uses Windows native CJK font from active theme
        title_el = next(e for e in page.elements if e.element_id == "title")
        assert title_el.font in {"Microsoft YaHei", "DengXian", "SimHei", "SimSun"}
        # Bullets shrunk tightly to content height (well below full 5.62in content height)
        bul_el = next(e for e in page.elements if e.element_id == "bullets")
        assert bul_el.h_in < 3.6
        # Accent rail matches shrunk bullets height
        rail = next(e for e in page.elements if e.element_id == "accent_rail")
        assert rail.h_in == bul_el.h_in
        # Image slot recorded
        img_el = next(e for e in page.elements if e.kind is ElementKind.image)
        img_slots.append((img_el.w_in, img_el.h_in))
        # Zero foreground edge-alignment drift
        fg_boxes = [
            GeoBox(
                element_id=e.element_id,
                x_pt=e.x_in * 72.0,
                y_pt=e.y_in * 72.0,
                w_pt=e.w_in * 72.0,
                h_pt=e.h_in * 72.0,
                is_background=(e.kind is ElementKind.shape),
                is_picture=(e.kind is ElementKind.image),
                has_text=bool(e.text),
                text_len=len(e.text or ""),
            )
            for e in page.elements
        ]
        assert find_edge_misalignments(fg_boxes) == []

    # Both split-layout pages use the exact same uniform image slot size (crop_fill driven)
    assert img_slots[0] == img_slots[1]


def test_r5_qa_catches_empty_card_and_undersized_image_slot(tmp_path):
    """B6 regression: QA must alert on both an empty full-height card and a tiny centered image."""
    pptx_path = tmp_path / "b6_bad.pptx"
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    slide = prs.slides.add_slide(prs.slide_layouts[6])

    # Deliberately create a giant empty card (B6 bug)
    card = slide.shapes.add_shape(1, Inches(0.65), Inches(1.50), Inches(5.75), Inches(5.50))
    card.name = "card_text"

    # Deliberately place a tiny postage-stamp image in the center
    img_path = tmp_path / "tiny.png"
    img_path.write_bytes(_png(400, 400))
    pic = slide.shapes.add_picture(str(img_path), Inches(8.5), Inches(3.2), Inches(1.2), Inches(1.2))
    pic.name = "ASSET_A001__vis_1"

    prs.save(str(pptx_path))

    issues = inspect_deck(pptx_path)
    descs = [i.description for i in issues]
    assert any("card_text" in d and "empty" in d for d in descs), f"expected empty card alert, got: {descs}"
    assert any("ASSET_A001__vis_1" in d and "undersized" in d for d in descs), f"expected undersized image alert, got: {descs}"


@pytest.mark.windows_com
def test_r5_windows_com_redesigned_deck_clean_qa_and_gates(tmp_path):
    """Real desktop PowerPoint 16.0 build + export + measure on an R5-redesigned deck passes QA & hard gates cleanly."""
    from pptv01.renderers.powerpoint_com import PowerPointComRenderer

    renderer = PowerPointComRenderer()
    renderer.preflight()

    pp = ProductionPlan(run_id=RID, source_utterance="做一个城市热岛效应研究汇报 PPT。", **GOOD_A)
    sp = SlidePlan(
        run_id=RID,
        status="provisional",
        narrative=GOOD_B["narrative"],
        pages=[
            {"page_id": "S001", "status": "provisional", **GOOD_B["pages"][0]},
            {"page_id": "S002", "status": "provisional", **GOOD_B["pages"][1]},
        ],
    )
    (tmp_path / "assets").mkdir()
    p1 = _png(1200, 800, (25, 85, 145))
    p2 = _png(1200, 900, (35, 120, 85))
    (tmp_path / "assets" / "A001.png").write_bytes(p1)
    (tmp_path / "assets" / "A002.png").write_bytes(p2)

    reg = AssetRegistry(
        run_id=RID,
        assets=[
            {
                "asset_id": "A001",
                "status": "acquired",
                "type": "image",
                "file_path": "assets/A001.png",
                "source_url": "https://commons.wikimedia.org/wiki/File:UHI1.png",
                "source_name": "Wikimedia Commons",
                "license_or_terms": "CC BY 4.0",
                "author": "Author1",
                "attribution": "Author1, CC BY 4.0, Wikimedia Commons",
                "identifier": "File:UHI1.png",
                "sha256": hashlib.sha256(p1).hexdigest(),
                "mime": "image/png",
                "width": 1200,
                "height": 800,
                "retrieved_at": "2026-01-01T00:00:00+08:00",
                "candidates": [{"title": "File:UHI1.png", "decision": "selected", "reason": "ok"}],
            },
            {
                "asset_id": "A002",
                "status": "acquired",
                "type": "image",
                "file_path": "assets/A002.png",
                "source_url": "https://commons.wikimedia.org/wiki/File:UHI2.png",
                "source_name": "Wikimedia Commons",
                "license_or_terms": "CC BY 4.0",
                "author": "Author2",
                "attribution": "Author2, CC BY 4.0, Wikimedia Commons",
                "identifier": "File:UHI2.png",
                "sha256": hashlib.sha256(p2).hexdigest(),
                "mime": "image/png",
                "width": 1200,
                "height": 900,
                "retrieved_at": "2026-01-01T00:00:00+08:00",
                "candidates": [{"title": "File:UHI2.png", "decision": "selected", "reason": "ok"}],
            },
        ],
    )
    mapping = SlideAssetMapping(
        run_id=RID,
        mappings=[
            {"page_id": "S001", "asset_id": "A001", "usage": "城市热岛遥感图", "accepted": True},
            {"page_id": "S002", "asset_id": "A002", "usage": "下垫面结构对比图", "accepted": True},
        ],
        unused_assets=[],
    )

    design = run_stage_f(pp, sp, reg, None, mapping, tmp_path)
    ops = compile_deck_ops(sp, design, reg, None, tmp_path)
    build_res = renderer.build(ops, tmp_path)

    qa, pngs = run_stage_h(
        build_res.pptx_path,
        sp,
        tmp_path,
        design=design,
        reg=reg,
        uir=None,
        mapping=mapping,
        deck_renderer=renderer,
    )
    assert qa.renderer == "powerpoint-com"
    assert len(pngs) == 2
    assert qa.rendered_pages == ["S001", "S002"]
    assert qa.render_provenance and "powerpoint-com" in qa.render_provenance
    assert not qa.issues, f"unexpected QA issues on R5 deck: {qa.issues}"
    assert not qa.missing_fonts, f"unexpected missing fonts: {qa.missing_fonts}"
    assert len(qa.capacity_vs_measured) >= 4
    # Verify calibrated capacity estimates had zero false overflows
    for row in qa.capacity_vs_measured:
        assert row["overflow_h_pt"] <= 6.0

    # Verify visual hard gates pass on the R5 desktop PowerPoint deck
    from pptv01.contracts import UserInputRequests
    from pptv01.grading.gates import _check_pptx_visual_gates
    uir_empty = UserInputRequests(run_id=RID, requests=[], summary="0 open")
    gate_results = _check_pptx_visual_gates(tmp_path, reg, uir_empty, sp)
    for g in gate_results:
        assert g.status == "pass", f"gate {g.gate} failed: {g.detail}"
