"""Mechanical & visual QA checks over deck.pptx and rendered slide PNGs (Stage H)."""

from __future__ import annotations

import io
import re
from pathlib import Path

from PIL import Image, ImageStat
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from pptv01.contracts import (
    AssetRegistry,
    AssetStatus,
    ElementKind,
    PageDesign,
    QAIssue,
    QAIssueKind,
    Severity,
    SlideAssetMapping,
    SlidePlan,
    UserInputRequests,
    UserRequestStatus,
)
from pptv01.f_layout.capacity import estimate_slot_capacity, wrap_mixed_text
from pptv01.f_layout.validate import MIN_FONT_PT

EMU_PER_IN = 914400.0
_BG_NAMES = {"bg_header", "accent_bar", "card_main", "card_text"}
_ASSET_BIND_RE = re.compile(r"^(?:ASSET|CHART|TABLE|DIAGRAM|ICON)_(A\d{3,})(?:__|$)")
_INPUT_BIND_RE = re.compile(r"^(?:INPUT|CHART_INPUT|TABLE_INPUT|DIAGRAM_INPUT)_(U\d{3,})(?:__|$)")


def _element_kind_guess(shape) -> ElementKind:
    name = (shape.name or "").lower()
    if name.startswith("placeholder_"):
        return ElementKind.placeholder
    if "title" in name:
        return ElementKind.title
    if "caption" in name or name.startswith("cap_"):
        return ElementKind.caption
    if "footer" in name:
        return ElementKind.footer
    if "bullet" in name:
        return ElementKind.bullet
    return ElementKind.body


def _is_background_shape(shape, slide_w_in: float, slide_h_in: float) -> bool:
    name = (shape.name or "").lower()
    if name in _BG_NAMES or name.startswith("bg_") or name.startswith("card_"):
        return True
    if shape.shape_type is MSO_SHAPE_TYPE.AUTO_SHAPE:
        txt = shape.text_frame.text.strip() if getattr(shape, "has_text_frame", False) else ""
        if not txt and not name.startswith("placeholder_"):
            return True
    return False


def _shape_font_pt(shape) -> float | None:
    if not getattr(shape, "has_text_frame", False):
        return None
    sizes: list[float] = []
    for p in shape.text_frame.paragraphs:
        if p.font.size is not None:
            sizes.append(p.font.size.pt)
        for r in p.runs:
            if r.font.size is not None:
                sizes.append(r.font.size.pt)
    return max(sizes) if sizes else None


def _overlap_area(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax1, ay1, aw, ah = a
    bx1, by1, bw, bh = b
    x1 = max(ax1, bx1)
    y1 = max(ay1, by1)
    x2 = min(ax1 + aw, bx1 + bw)
    y2 = min(ay1 + ah, by1 + bh)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    return (x2 - x1) * (y2 - y1)


def inspect_deck(
    pptx_path: Path,
    slide_plan: SlidePlan | None = None,
    design: PageDesign | None = None,
    reg: AssetRegistry | None = None,
    uir: UserInputRequests | None = None,
    mapping: SlideAssetMapping | None = None,
    rendered_pngs: list[Path] | None = None,
) -> list[QAIssue]:
    """Detect all 6 QAIssueKind categories across the generated deck and rendered PNGs."""
    issues: list[QAIssue] = []
    prs = Presentation(str(pptx_path))
    slide_w = prs.slide_width / EMU_PER_IN
    slide_h = prs.slide_height / EMU_PER_IN

    page_ids = [p.page_id for p in slide_plan.pages] if slide_plan else [f"S{i:03d}" for i in range(1, len(prs.slides) + 1)]
    design_by_id = {p.page_id: p for p in design.pages} if design else {}

    valid_assets = (
        {a.asset_id for a in reg.assets if a.status in (AssetStatus.acquired, AssetStatus.drawn, AssetStatus.substituted)}
        if reg
        else set()
    )
    valid_inputs: set[str] = set()
    if uir:
        for r in uir.requests:
            if r.status is UserRequestStatus.provided:
                valid_inputs.add(r.request_id)
                if r.asset_id:
                    valid_assets.add(r.asset_id)

    irrelevant_pairs: set[tuple[str, str]] = set()
    if mapping:
        for m in mapping.mappings:
            if m.relevance == "irrelevant":
                if m.asset_id:
                    irrelevant_pairs.add((m.page_id, m.asset_id))
                if m.request_id:
                    irrelevant_pairs.add((m.page_id, m.request_id))

    if slide_plan and len(prs.slides) != len(slide_plan.pages):
        issues.append(QAIssue(
            page_id=page_ids[0] if page_ids else "S001",
            kind=QAIssueKind.layout_anomaly,
            severity=Severity.blocker,
            description=f"slide count {len(prs.slides)} != planned {len(slide_plan.pages)}",
        ))

    for idx, slide in enumerate(prs.slides):
        pid = page_ids[idx] if idx < len(page_ids) else f"S{idx + 1:03d}"
        fg_boxes: list[tuple[str, tuple[float, float, float, float]]] = []
        pic_count = 0

        for s_i, sh in enumerate(slide.shapes):
            x = sh.left / EMU_PER_IN
            y = sh.top / EMU_PER_IN
            w = sh.width / EMU_PER_IN
            h = sh.height / EMU_PER_IN
            eid = (sh.name or f"shape_{s_i}")[:64]

            # 1) layout_anomaly: out of slide canvas bounds
            if x < -0.02 or y < -0.02 or x + w > slide_w + 0.02 or y + h > slide_h + 0.02:
                sev = Severity.blocker if (x < -0.5 or y < -0.5 or x + w > slide_w + 0.5 or y + h > slide_h + 0.5) else Severity.major
                issues.append(QAIssue(
                    page_id=pid,
                    kind=QAIssueKind.layout_anomaly,
                    severity=sev,
                    element_id=eid,
                    description=f"element {eid} extends outside slide bounds ({x:.2f},{y:.2f},{w:.2f}x{h:.2f})",
                ))

            # 2) text_overflow & font_or_ratio (font floor)
            if getattr(sh, "has_text_frame", False) and sh.text_frame.text.strip():
                pt = _shape_font_pt(sh) or 14.0
                kind_guess = _element_kind_guess(sh)
                floor = MIN_FONT_PT.get(kind_guess, 12.0)
                if pt < floor - 0.1:
                    issues.append(QAIssue(
                        page_id=pid,
                        kind=QAIssueKind.font_or_ratio,
                        severity=Severity.major,
                        element_id=eid,
                        description=f"font size {pt:g}pt is below floor {floor:g}pt for {kind_guess.value}",
                    ))
                cap = estimate_slot_capacity(w, h, pt)
                lines = wrap_mixed_text(sh.text_frame.text, w, pt)
                if len(lines) > cap.max_lines:
                    issues.append(QAIssue(
                        page_id=pid,
                        kind=QAIssueKind.text_overflow,
                        severity=Severity.major,
                        element_id=eid,
                        description=f"text overflows box ({len(lines)} lines > capacity {cap.max_lines} at {pt:g}pt)",
                    ))

            # 3) missing_image, font_or_ratio (aspect distortion), content_asset_mismatch
            if sh.shape_type is MSO_SHAPE_TYPE.PICTURE:
                pic_count += 1
                try:
                    blob = sh.image.blob
                    with Image.open(io.BytesIO(blob)) as im:
                        iw, ih = im.size
                    shape_ar = w / max(h, 1e-6)
                    img_ar = iw / max(ih, 1e-6)
                    if abs(shape_ar - img_ar) / max(img_ar, 1e-6) > 0.15:
                        issues.append(QAIssue(
                            page_id=pid,
                            kind=QAIssueKind.font_or_ratio,
                            severity=Severity.major,
                            element_id=eid,
                            description=f"image aspect ratio distorted: shape {shape_ar:.2f} vs image {img_ar:.2f}",
                        ))
                except Exception as e:  # noqa: BLE001
                    issues.append(QAIssue(
                        page_id=pid,
                        kind=QAIssueKind.missing_image,
                        severity=Severity.blocker,
                        element_id=eid,
                        description=f"embedded image blob unreadable: {type(e).__name__}",
                    ))

                m_a = _ASSET_BIND_RE.match(sh.name or "")
                m_u = _INPUT_BIND_RE.match(sh.name or "")
                bound_id = m_a.group(1) if m_a else (m_u.group(1) if m_u else None)
                if not bound_id or (m_a and bound_id not in valid_assets) or (m_u and bound_id not in valid_inputs):
                    if reg is not None or uir is not None:
                        issues.append(QAIssue(
                            page_id=pid,
                            kind=QAIssueKind.content_asset_mismatch,
                            severity=Severity.blocker,
                            element_id=eid,
                            description=f"image {eid} is unbound or references an unacquired asset",
                        ))
                elif (pid, bound_id) in irrelevant_pairs:
                    issues.append(QAIssue(
                        page_id=pid,
                        kind=QAIssueKind.content_asset_mismatch,
                        severity=Severity.major,
                        element_id=eid,
                        description=f"asset {bound_id} was marked irrelevant to {pid}",
                    ))

            if not _is_background_shape(sh, slide_w, slide_h):
                fg_boxes.append((eid, (x, y, w, h)))

        # Check expected images from design actually exist on slide
        if pid in design_by_id:
            want_imgs = sum(1 for e in design_by_id[pid].elements if e.kind is ElementKind.image)
            if pic_count < want_imgs:
                issues.append(QAIssue(
                    page_id=pid,
                    kind=QAIssueKind.missing_image,
                    severity=Severity.blocker,
                    description=f"expected {want_imgs} image(s) from page_design, found {pic_count}",
                ))

        # 4) occlusion between foreground shapes
        for i in range(len(fg_boxes)):
            for j in range(i + 1, len(fg_boxes)):
                id_a, box_a = fg_boxes[i]
                id_b, box_b = fg_boxes[j]
                area = _overlap_area(box_a, box_b)
                if area <= 0:
                    continue
                min_area = min(box_a[2] * box_a[3], box_b[2] * box_b[3])
                if area / max(min_area, 1e-6) > 0.05:
                    issues.append(QAIssue(
                        page_id=pid,
                        kind=QAIssueKind.occlusion,
                        severity=Severity.major,
                        element_id=id_b,
                        description=f"foreground occlusion between {id_a} and {id_b} ({area:.2f} sq in)",
                    ))

        # 5) rendered PNG sanity check (non-blank)
        if rendered_pngs and idx < len(rendered_pngs):
            png = rendered_pngs[idx]
            try:
                with Image.open(png) as rim:
                    st = ImageStat.Stat(rim.convert("RGB"))
                if sum(st.stddev) < 1.0:
                    issues.append(QAIssue(
                        page_id=pid,
                        kind=QAIssueKind.layout_anomaly,
                        severity=Severity.blocker,
                        description=f"rendered slide image {png.name} appears blank/uniform",
                    ))
            except Exception as e:  # noqa: BLE001
                issues.append(QAIssue(
                    page_id=pid,
                    kind=QAIssueKind.layout_anomaly,
                    severity=Severity.blocker,
                    description=f"rendered slide image {png.name} unreadable: {type(e).__name__}",
                ))

    return issues
