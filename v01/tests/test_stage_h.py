"""Stage H (LibreOffice render + QA + bounded repair + recheck) unit tests."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from pptx import Presentation
from pptx.util import Inches, Pt

from pptv01.contracts import (
    QAIssueKind,
    QAReport,
    Recheck,
    SlidePlan,
    StageStatus,
    load_contract,
)
from pptv01.grading.gates import check_gates
from pptv01.grading.ppteval import Grader, grade_deck, slide_text_from_plan
from pptv01.h_qa import (
    MAX_REPAIR_ROUNDS,
    RendererUnavailableError,
    has_render_tools,
    inspect_deck,
    render_pptx_to_pngs,
    run_stage_h,
)
from pptv01.runner import run_pipeline
from tests.conftest import GOOD_A, GOOD_B, GOOD_C, GOOD_TH
from tests.fakes import FakeClient

RID = "20260101-000000-abcd"


def _stub_render(pptx_path: Path, out_dir: Path) -> list[Path]:
    prs = Presentation(str(pptx_path))
    out_dir.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    for i in range(1, len(prs.slides) + 1):
        p = out_dir / f"slide_{i:03d}.png"
        im = Image.new("RGB", (640, 360), (245, 248, 252))
        ImageDraw.Draw(im).rectangle([20, 20, 200, 100], fill=(30, 64, 175))
        im.save(p, "PNG")
        out.append(p)
    return out


def _slide_plan(n: int = 1) -> SlidePlan:
    return SlidePlan(
        run_id=RID,
        status="provisional",
        narrative=GOOD_B["narrative"],
        pages=[{"page_id": f"S{i:03d}", "status": "provisional", **GOOD_B["pages"][(i - 1) % 2]} for i in range(1, n + 1)],
    )


def _png_bytes(w=400, h=200) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (w, h), (30, 100, 180)).save(b, "PNG")
    return b.getvalue()


def test_missing_render_tools_fail_loudly_and_never_pretend_success(tmp_path, monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    # Also ensure candidate paths don't interfere by passing explicit non-existent paths if needed
    prs = Presentation()
    prs.slides.add_slide(prs.slide_layouts[6])
    pptx_p = tmp_path / "deck.pptx"
    prs.save(str(pptx_p))

    if not has_render_tools():
        with pytest.raises(RendererUnavailableError, match="missing required tool"):
            render_pptx_to_pngs(pptx_p, tmp_path / "render")

        with pytest.raises(RendererUnavailableError):
            run_stage_h(pptx_p, _slide_plan(1), tmp_path)
        qa = load_contract(QAReport, tmp_path / "qa_report.json")
        assert qa.rendered_pages == [] and "unavailable" in qa.renderer
        assert "RENDER FAILED" in qa.summary

        report, _ = run_pipeline(
            "帮我做一个城市热岛效应的 PPT。", tmp_path / "run_out", FakeClient([GOOD_A, GOOD_B, GOOD_C, GOOD_TH]), run_id="h-fail"
        )
        assert report.overall.value == "failed"
        assert {s.stage: s.status for s in report.stages}["H"] is StageStatus.failed


@pytest.mark.skipif(not has_render_tools(), reason="LibreOffice (soffice) and pdftoppm not installed in environment")
def test_real_libreoffice_render_to_slide_pngs(tmp_path):
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    s = prs.slides.add_slide(prs.slide_layouts[6])
    tb = s.shapes.add_textbox(Inches(1), Inches(1), Inches(8), Inches(1.5))
    tb.name = "title"
    tb.text_frame.text = "Real LibreOffice Render Fixture"
    pptx_p = tmp_path / "deck.pptx"
    prs.save(str(pptx_p))

    rep, pngs = run_stage_h(pptx_p, _slide_plan(1), tmp_path)
    assert len(pngs) == 1 and pngs[0].name == "slide_001.png" and pngs[0].is_file()
    assert rep.rendered_pages == ["S001"] and rep.renderer == "libreoffice-headless"


def test_qa_detects_and_repairs_text_overflow_occlusion_bounds_and_aspect_ratio(tmp_path):
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    s = prs.slides.add_slide(prs.slide_layouts[6])

    # 1) Deliberate text overflow (28pt text in a short 3.0 x 0.9 box)
    tb1 = s.shapes.add_textbox(Inches(0.5), Inches(0.5), Inches(3.0), Inches(0.9))
    tb1.name = "bullets_overflow"
    r1 = tb1.text_frame.paragraphs[0].add_run()
    r1.text = "城市热岛效应是城区温度显著高于周边乡村的现象，成因包含地表反照率降低与人为排热。"
    r1.font.size = Pt(28)

    # 2) Deliberate occlusion (overlaps tb1!) + out-of-bounds right edge
    tb2 = s.shapes.add_textbox(Inches(0.6), Inches(0.7), Inches(13.2), Inches(1.2))
    tb2.name = "body_occluded"
    r2 = tb2.text_frame.paragraphs[0].add_run()
    r2.text = "第二段独立内容"
    r2.font.size = Pt(16)

    # 3) Deliberate aspect ratio distortion (400x200 image placed in 2.0 x 4.0 box)
    img_p = tmp_path / "pic.png"
    img_p.write_bytes(_png_bytes(400, 200))
    pic = s.shapes.add_picture(str(img_p), Inches(8.0), Inches(2.0), Inches(2.0), Inches(4.0))
    pic.name = "ASSET_A001__pic"

    pptx_p = tmp_path / "deck.pptx"
    prs.save(str(pptx_p))

    initial = inspect_deck(pptx_p, _slide_plan(1))
    kinds = {i.kind for i in initial}
    assert {QAIssueKind.text_overflow, QAIssueKind.occlusion, QAIssueKind.layout_anomaly, QAIssueKind.font_or_ratio} <= kinds

    rep, pngs = run_stage_h(pptx_p, _slide_plan(1), tmp_path, render_fn=_stub_render)
    assert len(pngs) == 1 and pngs[0].name == "slide_001.png"
    fixed = [i for i in rep.issues if i.fix_applied]
    assert fixed and all(i.recheck is Recheck.passed for i in fixed)
    assert len(inspect_deck(pptx_p, _slide_plan(1), rendered_pngs=pngs)) == 0


def test_repair_loop_is_bounded_and_honestly_reports_unfixable_overflow(tmp_path):
    calls = {"renders": 0}

    def counting_render(pptx_path: Path, out_dir: Path) -> list[Path]:
        calls["renders"] += 1
        return _stub_render(pptx_path, out_dir)

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    s = prs.slides.add_slide(prs.slide_layouts[6])
    # Huge text crammed into a tiny 1.5 x 0.4 box that cannot fit even at 12pt and 1.45x height
    tb = s.shapes.add_textbox(Inches(0.5), Inches(0.5), Inches(1.5), Inches(0.4))
    tb.name = "bullets_huge"
    r = tb.text_frame.paragraphs[0].add_run()
    r.text = "这是一个极长极长的段落" * 20
    r.font.size = Pt(20)

    pptx_p = tmp_path / "deck.pptx"
    prs.save(str(pptx_p))

    rep, _ = run_stage_h(pptx_p, _slide_plan(1), tmp_path, render_fn=counting_render, max_repair_rounds=2)
    # Initial render + up to MAX_REPAIR_ROUNDS re-renders = at most 3
    assert calls["renders"] <= 1 + MAX_REPAIR_ROUNDS
    overflow_issues = [i for i in rep.issues if i.kind is QAIssueKind.text_overflow]
    assert overflow_issues and overflow_issues[0].fix_applied is True
    assert overflow_issues[0].recheck is Recheck.failed
    assert "unresolved blocker/major" in rep.summary


def test_end_to_end_a_to_h_enables_gates_and_ppteval_design_scoring(tmp_path):
    report, run_dir = run_pipeline(
        "帮我做一个城市热岛效应的 PPT。",
        tmp_path,
        FakeClient([GOOD_A, GOOD_B, GOOD_C, GOOD_TH]),
        run_id="full-e2h",
        render_fn=_stub_render,
    )
    assert report.overall.value == "complete_with_placeholders"
    assert all(s.status is StageStatus.done for s in report.stages)

    # All deterministic hard gates pass!
    gate_rep = check_gates(run_dir)
    assert gate_rep.overall == "pass", [r for r in gate_rep.results if r.status != "pass"]

    # Render images slide_001.png, slide_002.png allow PPTEval pptx_render Design scoring
    imgs = sorted((run_dir / "render").glob("slide_*.png"))
    assert len(imgs) == 2
    ev = slide_text_from_plan(load_contract(SlidePlan, run_dir / "slide_plan.json"))
    for e, im in zip(ev, imgs):
        e.image = im
    from pptv01.contracts import GraderIdentity
    text_judge = FakeClient([
        {"reason": "good design", "score": 4},
        {"reason": "good content", "score": 4},
        {"reason": "good design", "score": 4},
        {"reason": "good content", "score": 4},
        {"slide_descriptions": ["s1", "s2"], "background": {}},
        {"reason": "coherent", "score": 4},
    ])
    vision_desc = FakeClient(["desc1", "style1", "desc2", "style2"])
    grader = Grader(text_judge, vision_desc, GraderIdentity(kind="agent", label="agent:test (manual, not an API model)"))
    grade_rep, _ = grade_deck(ev, grader, run_id=report.run_id, level="pptx_render")
    assert grade_rep.evidence_level == "pptx_render"
    assert all(sl.design_status == "scored" for sl in grade_rep.slides)
    assert grade_rep.summary.design_mean == 4.0 and grade_rep.summary.passed is True
