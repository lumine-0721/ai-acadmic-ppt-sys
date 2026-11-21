"""Tests for R3: PowerPoint COM rendering, live BoundHeight measurement, pure-arithmetic geometry lint,
and PageDesign-only repair -> recompile -> rebuild -> recheck loop."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from pptx import Presentation
from pptx.util import Inches, Pt

from pptv01.contracts import (
    AssetRegistry,
    DeckOps,
    DesignElement,
    ElementKind,
    PageDesign,
    PageDesignEntry,
    QAIssueKind,
    QAReport,
    Recheck,
    SlidePlan,
    load_contract,
)
from pptv01.g_build import compile_deck_ops
from pptv01.h_qa.geometry import (
    GeoBox,
    find_edge_misalignments,
    find_empty_cards,
    find_off_slide,
    find_overlaps,
)
from pptv01.h_qa.stage import run_stage_h
from pptv01.renderers import (
    BuildResult,
    ExportResult,
    MeasureResult,
    PowerPointComRenderer,
    RendererInfo,
    RendererUnavailableError,
    TextMeasurement,
)

RID = "r3-qa-test"


def _slide_plan() -> SlidePlan:
    return SlidePlan.model_validate(
        {
            "run_id": RID,
            "status": "provisional",
            "narrative": "测试",
            "pages": [
                {
                    "page_id": "S001",
                    "title": "热岛效应测量测试",
                    "goal": "测试",
                    "key_points": ["这是一段刻意超长用于触发真实溢出检测的文本要点"],
                    "content_summary": "仅备注",
                    "visual_intent": "图",
                    "asset_needs": [],
                    "status": "provisional",
                }
            ],
        }
    )


# ------------------------------------------------------------------ 1. Pure-arithmetic geometry tests (Linux + Windows)
def test_pure_arithmetic_geometry_off_slide_overlap_alignment_and_empty_card():
    boxes = [
        GeoBox("card_empty", 40, 100, 500, 350, is_background=True, role="background_card"),
        GeoBox("title", 40, 20, 860, 60, has_text=True, text_len=12),
        GeoBox("col_a", 42, 110, 200, 120, has_text=True, text_len=30),  # 2pt misaligned vs title.left=40
        GeoBox("col_b", 120, 130, 200, 120, has_text=True, text_len=25),  # overlaps col_a
        GeoBox("oob", 900, 100, 120, 80, has_text=True, text_len=10),     # right=1020 > 960
    ]
    off = find_off_slide(boxes, 960.0, 540.0)
    assert len(off) == 1 and off[0][0] == "oob" and off[0][1] is True

    ov = find_overlaps(boxes)
    assert any({a, b} == {"col_a", "col_b"} for a, b, _ in ov)

    mis = find_edge_misalignments(boxes)
    assert any(edge == "left" and {a, b} == {"title", "col_a"} for a, b, edge, _ in mis)

    # Empty card (no content inside) -> flagged (B6 regression)
    empty_only = [
        GeoBox("card_main", 40, 100, 860, 380, is_background=True, role="background_card"),
        GeoBox("title", 40, 15, 860, 60, has_text=True, text_len=10),
    ]
    emp = find_empty_cards(empty_only)
    assert len(emp) == 1 and emp[0][0] == "card_main" and "empty" in emp[0][1]

    # Severely under-filled card using real measured BoundHeight -> flagged
    low_fill = [
        GeoBox("card_main", 40, 100, 860, 380, is_background=True, role="background_card"),
        GeoBox("bullets", 60, 120, 300, 340, has_text=True, text_len=8),
    ]
    lf = find_empty_cards(low_fill, bound_heights_pt={"bullets": 24.0})
    assert len(lf) == 1 and "low fill ratio" in lf[0][1]


# ------------------------------------------------------------------ 2. Measured QA + PageDesign-only repair loop (Linux + Windows)
class _TestDeckRenderer:
    """Test-only DeckRenderer adapter (lives in tests/) that drives python-pptx read-back + synthetic BoundHeight."""

    name = "powerpoint-com"

    def __init__(self) -> None:
        self.build_calls = 0
        self.built_template_paths: list[str | None] = []

    def preflight(self) -> RendererInfo:
        return RendererInfo(
            name=self.name,
            available=True,
            platform="Windows 11",
            version="16.0",
            build="20527",
            excel_available=True,
            fonts=["Microsoft YaHei", "Calibri"],
        )

    def build(self, ops: DeckOps, run_dir: Path) -> BuildResult:
        self.build_calls += 1
        self.built_template_paths.append(ops.template_path)
        prs = Presentation()
        prs.slide_width = Inches(ops.canvas_width_in)
        prs.slide_height = Inches(ops.canvas_height_in)
        slide = None
        for op in ops.ops:
            p = op.params
            if op.kind.value == "add_slide":
                slide = prs.slides.add_slide(prs.slide_layouts[6])
            elif op.kind.value in ("set_placeholder_text", "add_textbox") and slide is not None:
                tb = slide.shapes.add_textbox(Inches(p["x_in"]), Inches(p["y_in"]), Inches(p["w_in"]), Inches(p["h_in"]))
                tb.name = p.get("shape_name") or op.element_id or "tb"
                run = tb.text_frame.paragraphs[0].add_run()
                run.text = p.get("text", "")
                run.font.size = Pt(float(p.get("font_size_pt", 16.0)))
        out_pptx = run_dir / "deck.pptx"
        prs.save(str(out_pptx))
        dump = run_dir / "deck_dump.json"
        dump.write_text("{}", encoding="utf-8")
        return BuildResult(
            pptx_path=out_pptx,
            dump_path=dump,
            slide_count=len(prs.slides),
            renderer_name=self.name,
            renderer_version="16.0",
            renderer_build="20527",
        )

    def export(self, pptx: Path, out_dir: Path, width_px: int = 1920) -> ExportResult:
        out_dir.mkdir(parents=True, exist_ok=True)
        p = out_dir / "slide_001.png"
        im = Image.new("RGB", (640, 360), (245, 248, 252))
        ImageDraw.Draw(im).rectangle([20, 20, 220, 120], fill=(30, 58, 138))
        im.save(p, "PNG")
        pdf = out_dir / "deck.pdf"
        pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
        return ExportResult(
            png_paths=[p],
            pdf_path=pdf,
            renderer_name=self.name,
            renderer_version="16.0",
            renderer_build="20527",
        )

    def measure(self, pptx: Path, page_ids: list[str] | None = None) -> MeasureResult:
        prs = Presentation(str(pptx))
        ms: list[TextMeasurement] = []
        for idx, sl in enumerate(prs.slides, 1):
            pid = page_ids[idx - 1] if page_ids else f"S{idx:03d}"
            for s_i, sh in enumerate(sl.shapes, 1):
                if not sh.has_text_frame or not sh.text_frame.text.strip():
                    continue
                alloc_w = sh.width / 914400.0 * 72.0
                alloc_h = sh.height / 914400.0 * 72.0
                pt = sh.text_frame.paragraphs[0].runs[0].font.size.pt if sh.text_frame.paragraphs[0].runs else 16.0
                # Simulate real PowerPoint line wrapping height
                chars_per_line = max(1, int(alloc_w / (pt * 1.0)))
                lines = max(1, (len(sh.text_frame.text) + chars_per_line - 1) // chars_per_line)
                bound_h = round(lines * pt * 1.35, 2)
                ms.append(
                    TextMeasurement(
                        slide_index=idx,
                        page_id=pid,
                        shape_id=s_i,
                        shape_name=sh.name,
                        allocated_w_pt=round(alloc_w, 2),
                        allocated_h_pt=round(alloc_h, 2),
                        bound_w_pt=round(min(alloc_w, len(sh.text_frame.text) * pt), 2),
                        bound_h_pt=bound_h,
                        overflow_h_pt=round(max(0.0, bound_h - alloc_h), 2),
                        font_name="Calibri",
                        font_name_fareast="Microsoft YaHei",
                        font_size_pt=pt,
                        text_length=len(sh.text_frame.text),
                    )
                )
        return MeasureResult(
            renderer_name=self.name,
            renderer_version="16.0",
            renderer_build="20527",
            measurements=ms,
            presentation_fonts=["Microsoft YaHei", "MissingCustomFont"],
        )


def test_r3_measured_overflow_repairs_page_design_recompiles_and_records_provenance(tmp_path):
    sp = _slide_plan()
    reg = AssetRegistry(run_id=RID, assets=[], sources=[], summary="0/0")
    design = PageDesign(
        run_id=RID,
        pages=[
            PageDesignEntry(
                page_id="S001",
                elements=[
                    DesignElement(element_id="title", kind=ElementKind.title, x_in=0.6, y_in=0.2, w_in=12.0, h_in=0.8, text="热岛效应测量测试", font_size_pt=26.0, bold=True),
                    # Deliberately short box (0.55in = 39.6pt) with 24pt long text -> measured overflow!
                    DesignElement(
                        element_id="bullets",
                        kind=ElementKind.bullet,
                        x_in=0.6,
                        y_in=1.5,
                        w_in=3.2,
                        h_in=0.55,
                        text="• 城市热岛效应是城区温度显著高于周边乡村地区的典型城市微气候现象，涉及地表反照率与人为排热。",
                        font_size_pt=24.0,
                    ),
                ],
            )
        ],
    )
    renderer = _TestDeckRenderer()
    ops = compile_deck_ops(sp, design, reg, None, tmp_path)
    build_res = renderer.build(ops, tmp_path)

    qa, pngs = run_stage_h(
        build_res.pptx_path,
        sp,
        tmp_path,
        design=design,
        reg=reg,
        deck_renderer=renderer,
        cross_render=True,
    )
    assert len(pngs) == 1 and pngs[0].name == "slide_001.png"
    assert qa.render_provenance == "powerpoint-com 16.0 (build 20527)"
    assert "MissingCustomFont" in qa.missing_fonts
    assert len(qa.capacity_vs_measured) >= 2
    assert qa.cross_render_notes  # LibreOffice cross-render recorded honestly (either ran or skipped)

    # Overflow was detected via measured BoundHeight, repaired via page_design.json + rebuild, and passed recheck!
    assert renderer.build_calls >= 2
    repaired_design = load_contract(PageDesign, tmp_path / "page_design.json")
    repaired_bullets = next(e for e in repaired_design.pages[0].elements if e.element_id == "bullets")
    assert repaired_bullets.font_size_pt < 24.0 or repaired_bullets.h_in > 0.55

    ov_issues = [i for i in qa.issues if i.kind is QAIssueKind.text_overflow]
    assert ov_issues and ov_issues[0].fix_applied is True
    assert "page_design:" in ov_issues[0].fix_note
    assert ov_issues[0].recheck is Recheck.passed
    assert "BoundHeight=" in ov_issues[0].description


def test_n3_repair_loop_preserves_template_path_and_unified_measure_protocol(tmp_path):
    """N3 (B10 + B11): repair recompilation preserves template_path & DeckRenderer.measure accepts page_ids cleanly."""
    import inspect
    from pptv01.renderers.protocol import DeckRenderer
    import pptv01.h_qa.stage as h_stage

    sig = inspect.signature(DeckRenderer.measure)
    assert "page_ids" in sig.parameters
    assert "co_varnames" not in inspect.getsource(h_stage)

    (tmp_path / "templates").mkdir()
    tpl_rel = "templates/academic.potx"
    (tmp_path / tpl_rel).write_bytes(b"placeholder-potx")

    sp = _slide_plan()
    reg = AssetRegistry(run_id=RID, assets=[], sources=[], summary="0/0")
    design = PageDesign(
        run_id=RID,
        pages=[
            PageDesignEntry(
                page_id="S001",
                elements=[
                    DesignElement(element_id="title", kind=ElementKind.title, x_in=0.6, y_in=0.2, w_in=12.0, h_in=0.8, text="模板保留测试", font_size_pt=26.0, bold=True),
                    DesignElement(
                        element_id="bullets",
                        kind=ElementKind.bullet,
                        x_in=0.6,
                        y_in=1.5,
                        w_in=3.2,
                        h_in=0.55,
                        text="• 城市热岛效应是城区温度显著高于周边乡村地区的典型城市微气候现象，涉及地表反照率与人为排热。",
                        font_size_pt=24.0,
                    ),
                ],
            )
        ],
    )
    renderer = _TestDeckRenderer()
    ops = compile_deck_ops(sp, design, reg, None, tmp_path, template_path=tpl_rel)
    build_res = renderer.build(ops, tmp_path)

    qa, _ = run_stage_h(
        build_res.pptx_path,
        sp,
        tmp_path,
        design=design,
        reg=reg,
        deck_renderer=renderer,
        template_path=tpl_rel,
    )
    assert renderer.build_calls >= 2
    # Every build (initial + every repair rebuild) must keep template_path!
    assert all(tp == tpl_rel for tp in renderer.built_template_paths)
    recompiled_ops = load_contract(DeckOps, tmp_path / "deck_ops.json")
    assert recompiled_ops.template_path == tpl_rel
    assert any(i.recheck is Recheck.passed for i in qa.issues)


def test_runner_relative_out_dir_handles_resolved_renderer_paths(tmp_path, monkeypatch):
    """Smoke 4 regression: runner must accept relative --out paths when DeckRenderer returns resolved Paths."""
    from pptv01.runner import run_pipeline
    from tests.conftest import GOOD_A, GOOD_B, GOOD_C
    from tests.fakes import FakeClient

    monkeypatch.chdir(tmp_path)

    class _ResolvedPathRenderer(_TestDeckRenderer):
        def build(self, ops: DeckOps, run_dir: Path) -> BuildResult:
            res = super().build(ops, run_dir.resolve())
            return res.model_copy(
                update={"pptx_path": res.pptx_path.resolve(), "dump_path": res.dump_path.resolve()}
            )

        def export(self, pptx: Path, out_dir: Path, width_px: int = 1920) -> ExportResult:
            res = super().export(pptx, out_dir.resolve(), width_px=width_px)
            return res.model_copy(
                update={
                    "png_paths": [p.resolve() for p in res.png_paths],
                    "pdf_path": res.pdf_path.resolve() if res.pdf_path else None,
                }
            )

    rep, run_dir = run_pipeline(
        "帮我做一个城市热岛效应的 PPT。",
        Path("runs/smoke4"),  # relative out_dir just like CLI `--out runs/smoke4`
        FakeClient([GOOD_A, GOOD_B, GOOD_C]),
        run_id="rel-test",
        renderer=_ResolvedPathRenderer(),
    )
    st = {s.stage: s for s in rep.stages}
    assert st["G"].status.value == "done", st["G"].error
    assert st["H"].status.value == "done", st["H"].error
    assert "deck_dump.json" in st["G"].output_files
    assert any(f.startswith("render/slide_") for f in st["H"].output_files)


def test_qa_detects_bg_header_occluding_title_in_z_order(tmp_path):
    """Real Smoke 4 regression: bg_header placed above native title in Z-order must trigger blocker occlusion."""
    from pptv01.h_qa.inspect import inspect_deck

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    sl = prs.slides.add_slide(prs.slide_layouts[5])  # Title Only (placeholder at z=0)
    sl.shapes.title.name = "title"
    sl.shapes.title.left = Inches(0.65)
    sl.shapes.title.top = Inches(0.22)
    sl.shapes.title.width = Inches(12.03)
    sl.shapes.title.height = Inches(0.88)
    sl.shapes.title.text = "咖啡店创业计划"
    # Adding bg_header AFTER title puts it at z=1 (ABOVE title, hiding it!)
    bg = sl.shapes.add_shape(1, Inches(0.0), Inches(0.0), Inches(13.333), Inches(1.24))
    bg.name = "bg_header"
    p = tmp_path / "zorder_bug.pptx"
    prs.save(str(p))

    issues = inspect_deck(p)
    assert any(
        i.kind is QAIssueKind.occlusion and i.severity.value == "blocker" and "bg_header" in i.description
        for i in issues
    ), f"expected z-order blocker occlusion, got: {issues}"


# ------------------------------------------------------------------ 3. Real Windows PowerPoint COM R3 test
@pytest.mark.windows_com
def test_r3_real_powerpoint_com_detects_and_repairs_measured_text_overflow(tmp_path):
    renderer = PowerPointComRenderer()
    info = renderer.preflight()

    sp = _slide_plan()
    reg = AssetRegistry(run_id=RID, assets=[], sources=[], summary="0/0")
    design = PageDesign(
        run_id=RID,
        pages=[
            PageDesignEntry(
                page_id="S001",
                elements=[
                    DesignElement(element_id="title", kind=ElementKind.title, x_in=0.6, y_in=0.2, w_in=12.0, h_in=0.85, text="热岛效应测量测试", font_size_pt=26.0, bold=True),
                    # Deliberate overflow: 28pt multi-line Chinese text inside a 3.0 x 0.6 in box
                    DesignElement(
                        element_id="bullets",
                        kind=ElementKind.bullet,
                        x_in=0.6,
                        y_in=1.6,
                        w_in=3.0,
                        h_in=0.60,
                        text="• 城市热岛效应是城区温度显著高于周边乡村地区的典型城市微气候现象，成因包含地表反照率降低、建筑密度增大以及工业交通人为排热。",
                        font_size_pt=28.0,
                    ),
                ],
            )
        ],
    )
    ops = compile_deck_ops(sp, design, reg, None, tmp_path)
    build_res = renderer.build(ops, tmp_path)

    qa, pngs = run_stage_h(
        build_res.pptx_path,
        sp,
        tmp_path,
        design=design,
        reg=reg,
        deck_renderer=renderer,
        installed_fonts=info.fonts,
    )
    assert len(pngs) == 1 and pngs[0].name == "slide_001.png" and pngs[0].is_file()
    assert qa.render_provenance.startswith("powerpoint-com 16.")
    assert len(qa.capacity_vs_measured) >= 2
    ov_issues = [i for i in qa.issues if i.kind is QAIssueKind.text_overflow]
    assert ov_issues, f"expected real PowerPoint BoundHeight overflow detection, got issues={qa.issues}"
    assert ov_issues[0].fix_applied is True
    assert ov_issues[0].recheck is Recheck.passed
