"""Deterministic bounded repair actions on deck.pptx for Stage H QA issues."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.util import Emu, Pt

from pptv01.contracts import DesignElement, ElementKind, PageDesign, QAIssue, QAIssueKind
from pptv01.f_layout.capacity import fit_text_to_box
from pptv01.f_layout.validate import MIN_FONT_PT
from pptv01.renderers.protocol import MeasureResult

from .inspect import EMU_PER_IN, PT_PER_IN, _element_kind_guess, _is_background_shape, _overlap_area, _shape_font_pt


def _set_run_font_pt(shape, new_pt: float) -> None:
    if not getattr(shape, "has_text_frame", False):
        return
    for p in shape.text_frame.paragraphs:
        if p.font.size is not None:
            p.font.size = Pt(new_pt)
        for r in p.runs:
            r.font.size = Pt(new_pt)


def _find_shape_by_id(slide, element_id: str | None):
    if not element_id:
        return None
    for i, sh in enumerate(slide.shapes):
        eid = (sh.name or f"shape_{i}")[:64]
        if eid == element_id:
            return sh
    return None


def apply_repairs(
    pptx_path: Path,
    issues: list[QAIssue],
    page_ids: list[str],
) -> dict[tuple[str, QAIssueKind, str | None], str]:
    """Apply targeted repairs to ``pptx_path``. Returns {(page_id, kind, element_id): fix_note}."""
    prs = Presentation(str(pptx_path))
    slide_w = prs.slide_width / EMU_PER_IN
    slide_h = prs.slide_height / EMU_PER_IN
    pid_to_idx = {pid: i for i, pid in enumerate(page_ids)}
    applied: dict[tuple[str, QAIssueKind, str | None], str] = {}

    for issue in issues:
        idx = pid_to_idx.get(issue.page_id)
        if idx is None or idx >= len(prs.slides):
            continue
        slide = prs.slides[idx]
        sh = _find_shape_by_id(slide, issue.element_id)

        if issue.kind is QAIssueKind.text_overflow and sh is not None and getattr(sh, "has_text_frame", False):
            w = sh.width / EMU_PER_IN
            h = sh.height / EMU_PER_IN
            cur_pt = _shape_font_pt(sh) or 14.0
            floor = MIN_FONT_PT.get(_element_kind_guess(sh), 12.0)
            fit = fit_text_to_box(sh.text_frame.text, w, h, cur_pt, min_font_pt=floor)
            notes: list[str] = []
            if fit.font_size_pt < cur_pt:
                _set_run_font_pt(sh, fit.font_size_pt)
                notes.append(f"font {cur_pt:g}pt->{fit.font_size_pt:g}pt")
            if fit.still_overflows:
                top_in = sh.top / EMU_PER_IN
                max_h = max(h, slide_h - top_in - 0.15)
                extra_h = min(max_h, h * 1.45)
                if extra_h > h + 0.05:
                    sh.height = Emu(int(extra_h * EMU_PER_IN))
                    notes.append(f"height {h:.2f}in->{extra_h:.2f}in")
            if notes:
                applied[(issue.page_id, issue.kind, issue.element_id)] = "; ".join(notes)

        elif issue.kind is QAIssueKind.font_or_ratio and sh is not None:
            if sh.shape_type is MSO_SHAPE_TYPE.PICTURE:
                try:
                    with Image.open(io.BytesIO(sh.image.blob)) as im:
                        iw, ih = im.size
                    img_ar = iw / max(ih, 1e-6)
                    w = sh.width / EMU_PER_IN
                    h = sh.height / EMU_PER_IN
                    if w / max(h, 1e-6) > img_ar:
                        nw = h * img_ar
                        sh.left = Emu(int((sh.left / EMU_PER_IN + (w - nw) / 2.0) * EMU_PER_IN))
                        sh.width = Emu(int(nw * EMU_PER_IN))
                    else:
                        nh = w / img_ar
                        sh.top = Emu(int((sh.top / EMU_PER_IN + (h - nh) / 2.0) * EMU_PER_IN))
                        sh.height = Emu(int(nh * EMU_PER_IN))
                    applied[(issue.page_id, issue.kind, issue.element_id)] = f"restored aspect ratio {iw}x{ih}"
                except Exception:  # noqa: BLE001
                    pass
            elif getattr(sh, "has_text_frame", False):
                floor = MIN_FONT_PT.get(_element_kind_guess(sh), 12.0)
                cur_pt = _shape_font_pt(sh) or floor
                if cur_pt < floor:
                    _set_run_font_pt(sh, floor)
                    applied[(issue.page_id, issue.kind, issue.element_id)] = f"raised font {cur_pt:g}pt->{floor:g}pt"

        elif issue.kind is QAIssueKind.layout_anomaly and sh is not None:
            margin = 0.10
            x = max(margin, sh.left / EMU_PER_IN)
            y = max(margin, sh.top / EMU_PER_IN)
            w = min(sh.width / EMU_PER_IN, slide_w - 2 * margin)
            h = min(sh.height / EMU_PER_IN, slide_h - 2 * margin)
            if x + w > slide_w - margin:
                x = max(margin, slide_w - margin - w)
            if y + h > slide_h - margin:
                y = max(margin, slide_h - margin - h)
            sh.left = Emu(int(x * EMU_PER_IN))
            sh.top = Emu(int(y * EMU_PER_IN))
            sh.width = Emu(int(w * EMU_PER_IN))
            sh.height = Emu(int(h * EMU_PER_IN))
            applied[(issue.page_id, issue.kind, issue.element_id)] = "clamped shape within slide margins"

        elif issue.kind is QAIssueKind.occlusion and sh is not None:
            # Separate overlapping foreground shapes by trimming/pushing the lower/right shape
            fg = [
                (s_i, s, (s.left / EMU_PER_IN, s.top / EMU_PER_IN, s.width / EMU_PER_IN, s.height / EMU_PER_IN))
                for s_i, s in enumerate(slide.shapes)
                if not _is_background_shape(s, slide_w, slide_h) and s is not sh
            ]
            bx = (sh.left / EMU_PER_IN, sh.top / EMU_PER_IN, sh.width / EMU_PER_IN, sh.height / EMU_PER_IN)
            for _, other, ob in fg:
                if _overlap_area(ob, bx) <= 0:
                    continue
                ox, oy, ow, oh = ob
                x, y, w, h = bx
                gap = 0.12
                if y >= oy and oy + oh + gap + 0.5 <= slide_h - 0.1:
                    new_y = oy + oh + gap
                    new_h = max(0.4, min(h, slide_h - 0.1 - new_y))
                    sh.top = Emu(int(new_y * EMU_PER_IN))
                    sh.height = Emu(int(new_h * EMU_PER_IN))
                    applied[(issue.page_id, issue.kind, issue.element_id)] = (
                        f"shifted vertically to y={new_y:.2f}in to clear occlusion"
                    )
                    break
                if x >= ox and ox + ow + gap + 0.8 <= slide_w - 0.1:
                    new_x = ox + ow + gap
                    new_w = max(0.8, min(w, slide_w - 0.1 - new_x))
                    sh.left = Emu(int(new_x * EMU_PER_IN))
                    sh.width = Emu(int(new_w * EMU_PER_IN))
                    applied[(issue.page_id, issue.kind, issue.element_id)] = (
                        f"shifted horizontally to x={new_x:.2f}in to clear occlusion"
                    )
                    break
                # Fallback: trim height of the upper shape slightly if lower can't move
                if oy < y and oh > 0.8:
                    trim = max(0.5, y - oy - gap)
                    other.height = Emu(int(trim * EMU_PER_IN))
                    applied[(issue.page_id, issue.kind, issue.element_id)] = (
                        f"trimmed upper shape height to {trim:.2f}in to clear occlusion"
                    )
                    break

    if applied:
        prs.save(str(pptx_path))
    return applied


def repair_page_design(
    design: PageDesign,
    issues: list[QAIssue],
    *,
    measure_result: MeasureResult | None = None,
) -> tuple[PageDesign, dict[tuple[str, QAIssueKind, str | None], str]]:
    """Apply bounded repairs directly to PageDesign (for recompile -> COM rebuild -> recheck).

    Never mutates deck.pptx directly; all edits stay in the declarative PageDesign contract.
    """
    updated = PageDesign.model_validate(design.model_dump())
    page_by_id = {p.page_id: p for p in updated.pages}
    meas_by_key = (
        {(m.page_id, m.shape_name): m for m in measure_result.measurements}
        if measure_result is not None
        else {}
    )
    applied: dict[tuple[str, QAIssueKind, str | None], str] = {}

    def _find_el(page, eid: str | None) -> DesignElement | None:
        if not eid:
            return None
        for el in page.elements:
            if el.element_id == eid or eid.endswith(f"__{el.element_id}"):
                return el
        return None

    for issue in issues:
        page = page_by_id.get(issue.page_id)
        if page is None:
            continue
        el = _find_el(page, issue.element_id)
        if el is None:
            continue
        slide_w = page.canvas_width_in
        slide_h = page.canvas_height_in

        if issue.kind is QAIssueKind.text_overflow and el.text:
            floor = MIN_FONT_PT.get(el.kind, 12.0)
            cur_pt = float(el.font_size_pt or 16.0)
            notes: list[str] = []
            m = meas_by_key.get((issue.page_id, issue.element_id or "")) or meas_by_key.get((issue.page_id, el.element_id))

            # Step down font size toward floor
            new_pt = cur_pt
            if cur_pt > floor + 0.1:
                if m and m.bound_h_pt > m.allocated_h_pt > 0:
                    ratio = max(0.65, (m.allocated_h_pt / m.bound_h_pt) ** 0.75)
                    new_pt = max(floor, round(cur_pt * ratio, 1))
                    if new_pt >= cur_pt:
                        new_pt = max(floor, round(cur_pt - 2.0, 1))
                else:
                    fit = fit_text_to_box(el.text, el.w_in, el.h_in, cur_pt, min_font_pt=floor)
                    new_pt = fit.font_size_pt if fit.font_size_pt < cur_pt else max(floor, round(cur_pt - 2.0, 1))
                if new_pt < cur_pt:
                    el.font_size_pt = new_pt
                    notes.append(f"font {cur_pt:g}pt->{new_pt:g}pt")

            # Also expand height if measured overflow exceeds what font shrink alone handles
            if m and m.overflow_h_pt > 4.0:
                needed_h = round((m.bound_h_pt + 10.0) / PT_PER_IN, 3)
                max_h = round(max(el.h_in, slide_h - el.y_in - 0.15), 3)
                new_h = min(max_h, max(el.h_in, needed_h))
                if new_h > el.h_in + 0.05:
                    notes.append(f"height {el.h_in:.2f}in->{new_h:.2f}in")
                    el.h_in = new_h
            elif new_pt <= floor + 0.1:
                max_h = round(max(el.h_in, slide_h - el.y_in - 0.15), 3)
                new_h = min(max_h, round(el.h_in * 1.35, 3))
                if new_h > el.h_in + 0.05:
                    notes.append(f"height {el.h_in:.2f}in->{new_h:.2f}in")
                    el.h_in = new_h

            if notes:
                applied[(issue.page_id, issue.kind, issue.element_id)] = "page_design: " + "; ".join(notes)

        elif issue.kind is QAIssueKind.font_or_ratio and el.font_size_pt is not None:
            floor = MIN_FONT_PT.get(el.kind, 12.0)
            if el.font_size_pt < floor:
                old = el.font_size_pt
                el.font_size_pt = floor
                applied[(issue.page_id, issue.kind, issue.element_id)] = f"page_design: raised font {old:g}pt->{floor:g}pt"

        elif issue.kind is QAIssueKind.layout_anomaly:
            if "empty" in issue.description.lower() or "low fill ratio" in issue.description.lower():
                # Remove an empty decorative card rather than leaving an empty box
                if el.kind is ElementKind.shape and "card" in el.element_id.lower():
                    page.elements = [e for e in page.elements if e.element_id != el.element_id]
                    applied[(issue.page_id, issue.kind, issue.element_id)] = f"page_design: removed under-filled card {el.element_id}"
            else:
                margin = 0.12
                nx = max(margin, el.x_in)
                ny = max(margin, el.y_in)
                nw = min(el.w_in, slide_w - 2 * margin)
                nh = min(el.h_in, slide_h - 2 * margin)
                if nx + nw > slide_w - margin:
                    nx = max(margin, slide_w - margin - nw)
                if ny + nh > slide_h - margin:
                    ny = max(margin, slide_h - margin - nh)
                el.x_in, el.y_in, el.w_in, el.h_in = round(nx, 3), round(ny, 3), round(nw, 3), round(nh, 3)
                applied[(issue.page_id, issue.kind, issue.element_id)] = "page_design: clamped within canvas margins"

        elif issue.kind is QAIssueKind.occlusion:
            gap = 0.12
            fg = [e for e in page.elements if e.kind is not ElementKind.shape and e.element_id != el.element_id]
            for other in fg:
                if _overlap_area((other.x_in, other.y_in, other.w_in, other.h_in), (el.x_in, el.y_in, el.w_in, el.h_in)) <= 0:
                    continue
                if el.y_in >= other.y_in and other.y_in + other.h_in + gap + 0.5 <= slide_h - 0.12:
                    new_y = round(other.y_in + other.h_in + gap, 3)
                    el.y_in = new_y
                    el.h_in = round(max(0.45, min(el.h_in, slide_h - 0.12 - new_y)), 3)
                    applied[(issue.page_id, issue.kind, issue.element_id)] = f"page_design: shifted to y={new_y:.2f}in"
                    break
                if el.x_in >= other.x_in and other.x_in + other.w_in + gap + 0.8 <= slide_w - 0.12:
                    new_x = round(other.x_in + other.w_in + gap, 3)
                    el.x_in = new_x
                    el.w_in = round(max(0.8, min(el.w_in, slide_w - 0.12 - new_x)), 3)
                    applied[(issue.page_id, issue.kind, issue.element_id)] = f"page_design: shifted to x={new_x:.2f}in"
                    break

    return updated, applied
