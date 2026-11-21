"""Pure-arithmetic slide geometry, alignment, occlusion, and fill checks (Linux + Windows testable).

Adapted from:
  - thomas-villani/pptlive src/pptlive/_linting_geometry.py (MIT, 4b33e69217ba):
    shape-off-slide, edge-alignment tolerance, unintended text overlap.
  - ykuwai/ppt-mcp src/ppt_com/layout_check.py (MIT, 2155ecc9050e):
    separation of COM reading from pure-arithmetic geometry rules.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GeoBox:
    element_id: str
    x_pt: float
    y_pt: float
    w_pt: float
    h_pt: float
    is_background: bool = False
    is_connector: bool = False
    is_picture: bool = False
    has_text: bool = False
    text_len: int = 0
    role: str = ""

    @property
    def right(self) -> float:
        return self.x_pt + self.w_pt

    @property
    def bottom(self) -> float:
        return self.y_pt + self.h_pt

    @property
    def area(self) -> float:
        return max(0.0, self.w_pt) * max(0.0, self.h_pt)


def box_overlap_area(a: GeoBox, b: GeoBox) -> float:
    x1 = max(a.x_pt, b.x_pt)
    y1 = max(a.y_pt, b.y_pt)
    x2 = min(a.right, b.right)
    y2 = min(a.bottom, b.bottom)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    return (x2 - x1) * (y2 - y1)


def find_off_slide(
    boxes: list[GeoBox], slide_w_pt: float, slide_h_pt: float, *, tol_pt: float = 1.5
) -> list[tuple[str, bool, str]]:
    """Return [(element_id, is_severe, detail)] for shapes extending outside slide bounds."""
    out: list[tuple[str, bool, str]] = []
    for b in boxes:
        if b.is_connector:
            continue
        over = max(-b.x_pt, -b.y_pt, b.right - slide_w_pt, b.bottom - slide_h_pt)
        if over > tol_pt:
            out.append(
                (
                    b.element_id,
                    over > 36.0,
                    f"shape {b.element_id} extends {over:.1f}pt outside slide ({slide_w_pt:.0f}x{slide_h_pt:.0f}pt)",
                )
            )
    return out


def find_overlaps(boxes: list[GeoBox], *, min_ratio: float = 0.05) -> list[tuple[str, str, float]]:
    """Return [(id_a, id_b, overlap_sq_pt)] for unintended foreground overlaps."""
    fg = [b for b in boxes if not b.is_background and not b.is_connector]
    out: list[tuple[str, str, float]] = []
    for i in range(len(fg)):
        for j in range(i + 1, len(fg)):
            a, b = fg[i], fg[j]
            area = box_overlap_area(a, b)
            if area <= 0:
                continue
            min_a = min(a.area, b.area)
            if min_a > 0 and (area / min_a) > min_ratio:
                out.append((a.element_id, b.element_id, area))
    return out


def find_edge_misalignments(
    boxes: list[GeoBox], *, min_gap_pt: float = 1.0, max_gap_pt: float = 3.5
) -> list[tuple[str, str, str, float]]:
    """Return [(id_a, id_b, edge_name, delta_pt)] for foreground shapes nearly-but-not aligned.

    Adapted from thomas-villani/pptlive `_linting_geometry.py` edge-alignment rule (default ~3pt tolerance).
    """
    fg = [b for b in boxes if not b.is_background and not b.is_connector]
    findings: list[tuple[str, str, str, float]] = []
    for i in range(len(fg)):
        for j in range(i + 1, len(fg)):
            a, b = fg[i], fg[j]
            for edge, va, vb in (
                ("left", a.x_pt, b.x_pt),
                ("right", a.right, b.right),
                ("top", a.y_pt, b.y_pt),
                ("bottom", a.bottom, b.bottom),
            ):
                delta = abs(va - vb)
                if min_gap_pt <= delta <= max_gap_pt:
                    findings.append((a.element_id, b.element_id, edge, round(delta, 2)))
    return findings


def find_empty_cards(
    boxes: list[GeoBox],
    *,
    bound_heights_pt: dict[str, float] | None = None,
    min_card_area_pt2: float = 40000.0,
    min_fill_ratio: float = 0.08,
) -> list[tuple[str, str]]:
    """Detect large background cards that are visually empty or severely under-filled (B6 regression).

    Returns [(card_element_id, detail)].
    """
    cards = [
        b
        for b in boxes
        if (b.is_background and ("card" in b.element_id.lower() or "card" in b.role.lower()))
        and b.area >= min_card_area_pt2
    ]
    fg = [b for b in boxes if not b.is_background and not b.is_connector]
    findings: list[tuple[str, str]] = []
    bh_map = bound_heights_pt or {}

    for card in cards:
        covered_area = 0.0
        has_any_content = False
        for child in fg:
            ov = box_overlap_area(card, child)
            if ov <= 0:
                continue
            if child.has_text and child.text_len > 0:
                has_any_content = True
                # If we have a real measured BoundHeight, use actual text ink height instead of empty box height
                eff_h = min(child.h_pt, bh_map.get(child.element_id, child.h_pt))
                eff_area = max(0.0, child.w_pt) * max(0.0, eff_h)
                covered_area += min(ov, eff_area)
            elif child.is_picture or child.element_id.startswith(("CHART_", "TABLE_", "DIAGRAM_", "PLACEHOLDER_")):
                has_any_content = True
                covered_area += ov

        fill_ratio = covered_area / max(card.area, 1.0)
        if not has_any_content:
            findings.append((card.element_id, f"card {card.element_id} is completely empty (0% content fill)"))
        elif fill_ratio < min_fill_ratio:
            findings.append(
                (
                    card.element_id,
                    f"card {card.element_id} has severely low fill ratio ({fill_ratio:.1%} < {min_fill_ratio:.0%})",
                )
            )
    return findings


def find_undersized_image_slots(
    boxes: list[GeoBox],
    slide_w_pt: float,
    slide_h_pt: float,
    *,
    min_slide_area_ratio: float = 0.020,
    min_dim_pt: float = 96.0,
) -> list[tuple[str, str]]:
    """Detect postage-stamp or severely under-filled picture slots (B6 regression).

    Icons (explicitly named ``ICON_*``) are exempted from the minimum-slot-area rule.
    Flags pictures occupying less than ``min_slide_area_ratio`` of the slide OR smaller than
    ``min_dim_pt`` on both dimensions. Returns [(picture_element_id, detail)].
    """
    slide_area = max(1.0, slide_w_pt * slide_h_pt)
    findings: list[tuple[str, str]] = []
    for b in boxes:
        if not b.is_picture:
            continue
        if b.element_id.upper().startswith("ICON_"):
            continue
        ratio = b.area / slide_area
        if ratio < min_slide_area_ratio or (b.w_pt < min_dim_pt and b.h_pt < min_dim_pt):
            findings.append(
                (
                    b.element_id,
                    (
                        f"picture {b.element_id} is undersized ({b.w_pt:.0f}x{b.h_pt:.0f}pt, "
                        f"{ratio:.1%} of slide area < {min_slide_area_ratio:.1%})"
                    ),
                )
            )
    return findings


def find_oversized_placeholders(
    boxes: list[GeoBox],
    slide_w_pt: float,
    slide_h_pt: float,
    *,
    max_content_area_ratio: float = 0.15,
    allow_largest_slot: bool = False,
) -> list[tuple[str, str]]:
    """Detect placeholder boxes that exceed ~15% of slide content area or hijack the largest content slot.

    Round-4 visual review regression (T36 slides 1-3 & T12): placeholders must stay compact
    utility badges/strips and must NEVER dominate the page as hero visuals.
    Returns [(placeholder_element_id, detail)].
    """
    usable_w = max(72.0, slide_w_pt - 2.0 * 0.65 * 72.0)
    usable_h = max(72.0, slide_h_pt - 1.50 * 72.0)
    content_area = max(1.0, usable_w * usable_h)

    placeholders = [
        b for b in boxes if not b.is_background and not b.is_connector and b.element_id.upper().startswith("PLACEHOLDER_")
    ]
    if not placeholders:
        return []

    fg_non_ph = [
        b
        for b in boxes
        if not b.is_background
        and not b.is_connector
        and not b.element_id.upper().startswith("PLACEHOLDER_")
        and not b.element_id.lower().startswith(("footer", "cap_"))
    ]
    visual_boxes = [
        b
        for b in fg_non_ph
        if b.is_picture
        or b.element_id.upper().startswith(("ASSET_", "INPUT_", "CHART_", "TABLE_", "DIAGRAM_", "VIS_", "DIAG_"))
        or b.role in ("primary_visual", "full_bleed_hero", "data_chart", "data_table", "diagram_json")
    ]
    title_roles = {
        "title",
        "cover_title",
        "section_title",
        "bleed_title",
        "split_title",
        "quote_kicker_title",
        "needs_input_title",
    }
    body_boxes = [
        b
        for b in fg_non_ph
        if b not in visual_boxes
        and b.element_id.lower() != "title"
        and not b.element_id.lower().startswith("title_")
        and b.role not in title_roles
    ]
    if visual_boxes:
        max_primary_area = max(b.area for b in visual_boxes)
        primary_basis = "primary visual slot"
    elif body_boxes:
        max_primary_area = max(b.area for b in body_boxes)
        primary_basis = "primary body slot"
    else:
        max_primary_area = max((b.area for b in fg_non_ph), default=0.0)
        primary_basis = "title slot (no visual/body slot)"

    findings: list[tuple[str, str]] = []
    for ph in placeholders:
        ratio = ph.area / content_area
        reasons: list[str] = []
        if ratio > max_content_area_ratio + 1e-6:
            reasons.append(f"area {ratio:.1%} of content area exceeds {max_content_area_ratio:.0%} limit")
        if not allow_largest_slot and max_primary_area > 0 and ph.area >= max_primary_area:
            reasons.append(
                f"occupies the largest content slot ({ph.area:.0f} >= {max_primary_area:.0f} sq pt vs {primary_basis})"
            )
        if reasons:
            findings.append((ph.element_id, f"placeholder {ph.element_id} oversized: {'; '.join(reasons)}"))
    return findings
