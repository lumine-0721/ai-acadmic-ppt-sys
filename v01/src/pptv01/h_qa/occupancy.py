# Ported/adapted from SlideSpeak/slide-design-skill (MIT): engine/occupancy.ts (Copyright (c) 2026 SlideSpeak).
"""Slide-level and card-level vertical occupancy / fill-rate analysis (underfill detector).

Detects slides whose real foreground content leaves an oversized contiguous vertical void
(e.g., upper-half content leaving the lower half blank), as well as tall cards with an interior
void or sparse text coverage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Sequence

from pptv01.contracts import CANVAS_H_IN, CANVAS_W_IN, ElementKind, PageDesign, PageDesignEntry
from pptv01.f_layout.capacity import _char_advance_pt, estimate_text_height_in, wrap_mixed_text
from pptv01.f_layout.layouts import normalize_layout_id
from pptv01.renderers.protocol import MeasureResult, TextMeasurement

# SlideSpeak threshold: VOID_MAX_PX = 230 on 1080px slide height (~21.3% of height) -> 1.60 in on 7.5 in canvas.
VOID_MAX_IN = 1.60
# Card-level thresholds ported from SlideSpeak engine/occupancy.ts scaled from 1920x1080px to 13.333x7.5 in:
#   CELL_VOID_MAX_PX = 180 / 1080 * 7.5 = 1.25 in
#   CELL_VOID_MIN_HEIGHT = 240 / 1080 * 7.5 = 1.67 in
#   SPARSE_CELL_MIN_AREA = 60_000 / (1920 * 1080) * (13.333 * 7.5) = 2.89 in^2
CELL_VOID_MAX_IN = 1.25
CELL_VOID_MAX_FRACTION = 0.45
CELL_VOID_MIN_H_IN = 1.67
SPARSE_CELL_MIN_AREA_IN2 = 2.89
SPARSE_TEXT_COVERAGE = 0.035

# Default vertical content band [DEFAULT_CONTENT_TOP_IN, DEFAULT_CONTENT_BOTTOM_IN] when header/footer vary.
DEFAULT_CONTENT_TOP_IN = 1.20
DEFAULT_CONTENT_BOTTOM_IN = 7.00
_MERGE_TOL_IN = 0.015

# Role-based exemptions apply ONLY to true anchor/breathing pages without bullets or visual content.
ROLE_EXEMPT_REASONS: dict[str, str] = {
    "cover": "role=cover (anchor title slide)",
    "section_divider": "role=section_divider (section title/subtitle only, no bullets)",
    "quote_statement": "role=quote_statement (single conclusion/quote statement only, no bullets)",
}


def infer_page_role(page: PageDesignEntry) -> tuple[str, bool, str]:
    """Infer occupancy page role from actual elements/content (never exempt bullet-bearing content by layout_id)."""
    lid = normalize_layout_id(page.layout_id or page.layout_name or "")
    bullet_els = [
        el
        for el in page.elements
        if el.kind is ElementKind.bullet and (el.text or "").strip()
    ]
    body_point_lines = sum(
        1
        for el in page.elements
        if el.kind in (ElementKind.bullet, ElementKind.body)
        for ln in (el.text or "").splitlines()
        if ln.strip()
    )
    has_real_visual = any(
        el.kind in (ElementKind.image, ElementKind.chart, ElementKind.table)
        or (el.kind is ElementKind.shape and el.role == "diagram_json")
        for el in page.elements
    )

    if lid == "cover":
        return "cover", True, ROLE_EXEMPT_REASONS["cover"]
    if lid == "section" and not bullet_els and body_point_lines <= 1 and not has_real_visual:
        return "section_divider", True, ROLE_EXEMPT_REASONS["section_divider"]
    if lid == "quote" and not bullet_els and body_point_lines <= 1 and not has_real_visual:
        return "quote_statement", True, ROLE_EXEMPT_REASONS["quote_statement"]
    return "content", False, ""


@dataclass(frozen=True)
class CellInput:
    """Card container geometry and child vertical content intervals in inches relative to the card top."""

    cell_id: str
    height_in: float
    area_in2: float
    pad_in: float
    rects_in: Sequence[tuple[float, float]]
    text_area_in2: float
    has_visual: bool = False
    bg_rgb: str | None = None


@dataclass(frozen=True)
class CellFailure:
    index: int
    cell_id: str
    kind: Literal["cell-void", "sparse-cell"]
    detail: str


@dataclass(frozen=True)
class CellOccupancyResult:
    filled: bool
    failures: list[CellFailure] = field(default_factory=list)


@dataclass(frozen=True)
class PageOccupancyResult:
    page_id: str
    layout_id: str
    passed: bool
    exempt: bool
    exempt_reason: str
    max_void_in: float
    void_position: Literal["top", "middle", "bottom"] | None
    content_band: tuple[float, float]
    detail: str
    page_role: str = "content"
    cell_failures: list[CellFailure] = field(default_factory=list)
    used_live_measurements: bool = False
    max_col_void_in: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_id": self.page_id,
            "layout_id": self.layout_id,
            "page_role": self.page_role,
            "passed": self.passed,
            "exempt": self.exempt,
            "exempt_reason": self.exempt_reason,
            "max_void_in": self.max_void_in,
            "max_col_void_in": self.max_col_void_in,
            "void_position": self.void_position,
            "content_band": list(self.content_band),
            "detail": self.detail,
            "cell_failures": [
                {"index": f.index, "cell_id": f.cell_id, "kind": f.kind, "detail": f.detail}
                for f in self.cell_failures
            ],
            "used_live_measurements": self.used_live_measurements,
        }


@dataclass(frozen=True)
class DeckOccupancyReport:
    pages: list[PageOccupancyResult]
    flagged_pages: list[PageOccupancyResult]
    exempt_pages: list[PageOccupancyResult]
    passed: bool
    detail: str


def _merge_intervals(
    intervals: Sequence[tuple[float, float]], band_top: float, band_bottom: float
) -> list[tuple[float, float]]:
    clipped = [
        (max(band_top, min(a, b)), min(band_bottom, max(a, b)))
        for a, b in intervals
    ]
    valid = sorted((a, b) for a, b in clipped if b - a > 0.01)
    if not valid:
        return []
    merged: list[tuple[float, float]] = [valid[0]]
    for a, b in valid[1:]:
        pa, pb = merged[-1]
        if a <= pb + _MERGE_TOL_IN:
            merged[-1] = (pa, max(pb, b))
        else:
            merged.append((a, b))
    return merged


def _max_void(
    merged: Sequence[tuple[float, float]], band_top: float, band_bottom: float
) -> tuple[float, Literal["top", "middle", "bottom"] | None]:
    if band_bottom <= band_top:
        return 0.0, None
    if not merged:
        return round(band_bottom - band_top, 3), "middle"

    best = 0.0
    where: Literal["top", "middle", "bottom"] | None = None
    candidates: list[tuple[float, Literal["top", "middle", "bottom"]]] = [
        (max(0.0, merged[0][0] - band_top), "top"),
        *(
            (max(0.0, merged[i][0] - merged[i - 1][1]), "middle")
            for i in range(1, len(merged))
        ),
        (max(0.0, band_bottom - merged[-1][1]), "bottom"),
    ]
    for size, pos in candidates:
        if size > best:
            best = size
            where = pos
    return round(best, 3), where


def _max_bg_spread(bgs: Sequence[str]) -> int:
    cols: list[tuple[int, int, int]] = []
    for raw in bgs:
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) == 3 and all(p.isdigit() for p in parts):
            cols.append((int(parts[0]), int(parts[1]), int(parts[2])))
    best = 0
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            dist = abs(cols[i][0] - cols[j][0]) + abs(cols[i][1] - cols[j][1]) + abs(cols[i][2] - cols[j][2])
            if dist > best:
                best = dist
    return best


def score_cell_occupancy(
    cells: Sequence[CellInput],
    *,
    density: str = "normal",
) -> CellOccupancyResult:
    """Detect interior voids (``cell-void``) or sparse coverage (``sparse-cell``) inside cards."""
    if density == "editorial":
        return CellOccupancyResult(filled=True, failures=[])

    opaque_bgs = [c.bg_rgb for c in cells if c.bg_rgb]
    unique_bgs = list(dict.fromkeys(opaque_bgs))
    is_exhibit = (
        len(unique_bgs) >= 3
        and len(unique_bgs) >= len(cells) / 2.0
        and _max_bg_spread(unique_bgs) >= 120
    )

    failures: list[CellFailure] = []
    for idx, cell in enumerate(cells):
        if is_exhibit and cell.bg_rgb:
            continue
        has_text = cell.text_area_in2 > 0
        if (
            has_text
            and not cell.has_visual
            and cell.area_in2 >= SPARSE_CELL_MIN_AREA_IN2
            and (cell.text_area_in2 / max(cell.area_in2, 1e-6)) < SPARSE_TEXT_COVERAGE
        ):
            pct = 100.0 * cell.text_area_in2 / max(cell.area_in2, 1e-6)
            failures.append(
                CellFailure(
                    index=idx,
                    cell_id=cell.cell_id,
                    kind="sparse-cell",
                    detail=f"{cell.cell_id}: text covers {pct:.1f}% of {cell.area_in2:.2f} sq in card",
                )
            )
            continue

        if not has_text or cell.height_in < CELL_VOID_MIN_H_IN:
            continue
        top = cell.pad_in
        bottom = cell.height_in - cell.pad_in
        merged = _merge_intervals(cell.rects_in, top, bottom)
        if not merged:
            continue
        max_gap, where = _max_void(merged, top, bottom)
        threshold = max(CELL_VOID_MAX_IN, CELL_VOID_MAX_FRACTION * cell.height_in)
        if max_gap > threshold:
            failures.append(
                CellFailure(
                    index=idx,
                    cell_id=cell.cell_id,
                    kind="cell-void",
                    detail=(
                        f"cell-void in {cell.cell_id}: {max_gap:.2f}in interior void at {where or 'bottom'} "
                        f"in {cell.height_in:.2f}in card (> {threshold:.2f}in)"
                    ),
                )
            )
    return CellOccupancyResult(filled=not failures, failures=failures)


def _is_full_page_background(el, canvas_w: float, canvas_h: float) -> bool:
    return el.w_in >= 0.95 * canvas_w and el.h_in >= 0.85 * canvas_h


def _is_decorative_shape(el) -> bool:
    if el.kind is not ElementKind.shape:
        return False
    if el.role == "diagram_json" or el.asset_id or el.request_id:
        return False
    eid = (el.element_id or "").lower()
    if eid.startswith(("bg_", "accent_")):
        return True
    return not (el.text or "").strip() and el.z_index <= 1


def _is_card_container(el, canvas_w: float, canvas_h: float) -> bool:
    """Return True when ``el`` is a visible card/panel container whose interior occupancy should be checked."""
    if el.kind is not ElementKind.shape:
        return False
    if el.role == "diagram_json" or el.asset_id or el.request_id:
        return False
    if _is_full_page_background(el, canvas_w, canvas_h):
        return False
    eid = (el.element_id or "").lower()
    if eid.startswith(("bg_motif_", "accent_", "bg_canvas")):
        return False
    if el.h_in <= 0.22 or el.w_in <= 0.30:
        return False
    return eid.startswith(("bg_card_", "bg_panel_", "bg_hero", "bg_support_", "bg_fact_panel", "bg_row_", "card_"))


def _estimated_text_footprint(
    el,
    measured_boxes: Mapping[str, TextMeasurement] | None = None,
) -> tuple[float, float]:
    """Return ``(occupied_w_in, occupied_h_in)`` for a text element using live COM bounds or font wrapping."""
    if measured_boxes:
        m = measured_boxes.get(el.element_id)
        if m is not None and m.bound_h_pt > 0:
            occ_h = min(float(el.h_in), (m.bound_h_pt / 72.0) + 0.14)
            occ_w = min(float(el.w_in), (m.bound_w_pt / 72.0) + 0.14) if m.bound_w_pt > 0 else float(el.w_in)
            return occ_w, occ_h

    text = (el.text or "").strip()
    if not text:
        return 0.0, 0.0
    pt = float(el.font_size_pt or 16.0)
    lines = wrap_mixed_text(text, float(el.w_in), pt)
    occ_h = min(float(el.h_in), estimate_text_height_in(lines, pt))
    max_line_pt = max((sum(_char_advance_pt(ch, pt) for ch in ln) for ln in lines if ln), default=0.0)
    occ_w = min(float(el.w_in), max(0.25, max_line_pt / 72.0 + 0.16))
    return occ_w, occ_h


def extract_cell_inputs(
    page: PageDesignEntry,
    measured_boxes: Mapping[str, TextMeasurement] | None = None,
) -> list[CellInput]:
    """Extract card/panel ``CellInput`` entries automatically from a ``PageDesignEntry``."""
    canvas_w = float(page.canvas_width_in or CANVAS_W_IN)
    canvas_h = float(page.canvas_height_in or CANVAS_H_IN)
    containers = [el for el in page.elements if _is_card_container(el, canvas_w, canvas_h)]
    if not containers:
        return []

    fg_elements = [
        el
        for el in page.elements
        if el.kind in (ElementKind.title, ElementKind.body, ElementKind.bullet, ElementKind.caption, ElementKind.footer, ElementKind.image, ElementKind.chart, ElementKind.table)
        or (el.kind is ElementKind.shape and el.role == "diagram_json")
    ]

    cells: list[CellInput] = []
    for c in containers:
        rects_rel: list[tuple[float, float]] = []
        text_area = 0.0
        has_visual = False
        pad_in = 0.16
        for fg in fg_elements:
            cx = float(fg.x_in) + float(fg.w_in) / 2.0
            cy = float(fg.y_in) + float(fg.h_in) / 2.0
            if not (c.x_in - 0.08 <= cx <= c.x_in + c.w_in + 0.08 and c.y_in - 0.08 <= cy <= c.y_in + c.h_in + 0.08):
                continue
            if fg.kind in (ElementKind.image, ElementKind.chart, ElementKind.table) or fg.role == "diagram_json":
                has_visual = True
                rel_top = max(0.0, float(fg.y_in) - float(c.y_in))
                rel_bot = min(float(c.h_in), float(fg.y_in + fg.h_in) - float(c.y_in))
                if rel_bot > rel_top:
                    rects_rel.append((rel_top, rel_bot))
                continue
            occ_w, occ_h = _estimated_text_footprint(fg, measured_boxes)
            if occ_h <= 0.0 or occ_w <= 0.0:
                continue
            rel_top = max(0.0, float(fg.y_in) - float(c.y_in))
            rel_bot = min(float(c.h_in), float(fg.y_in) + occ_h - float(c.y_in))
            if rel_bot > rel_top:
                rects_rel.append((rel_top, rel_bot))
            text_area += occ_w * occ_h

        bg_rgb: str | None = None
        if c.fill_hex and c.fill_hex.startswith("#") and len(c.fill_hex) == 7:
            try:
                r = int(c.fill_hex[1:3], 16)
                g = int(c.fill_hex[3:5], 16)
                b = int(c.fill_hex[5:7], 16)
                bg_rgb = f"{r},{g},{b}"
            except ValueError:
                bg_rgb = None

        cells.append(
            CellInput(
                cell_id=c.element_id,
                height_in=float(c.h_in),
                area_in2=float(c.w_in) * float(c.h_in),
                pad_in=pad_in,
                rects_in=rects_rel,
                text_area_in2=round(text_area, 4),
                has_visual=has_visual,
                bg_rgb=bg_rgb,
            )
        )
    return cells


def _score_per_column_void(
    page: PageDesignEntry,
    col_items: Sequence[tuple[float, float, float, float]],
    band_top: float,
    band_bottom: float,
) -> tuple[float, str | None]:
    """Measure max vertical void per horizontal column on non-visual multi-column slides."""
    has_real_visual = any(
        el.kind in (ElementKind.image, ElementKind.chart, ElementKind.table)
        or (el.kind is ElementKind.shape and el.role == "diagram_json")
        for el in page.elements
    )
    if has_real_visual:
        return 0.0, None

    narrow = [item for item in col_items if item[1] < 7.5]
    if len(narrow) < 2:
        return 0.0, None

    # Cluster narrow foreground elements into horizontal columns by x-center
    clusters: list[tuple[float, list[tuple[float, float]]]] = []
    for x0, x1, _w, y0, y1 in [(x, x + w, w, y0, y1) for (x, w, y0, y1) in narrow]:
        cx = (x0 + x1) / 2.0
        placed = False
        for i, (mean_cx, intervals) in enumerate(clusters):
            if abs(cx - mean_cx) < 1.5:
                intervals.append((y0, y1))
                clusters[i] = ((mean_cx * (len(intervals) - 1) + cx) / len(intervals), intervals)
                placed = True
                break
        if not placed:
            clusters.append((cx, [(y0, y1)]))

    if len(clusters) < 2:
        return 0.0, None

    # Include full-width foreground bands (e.g., anchored footer, hero_main) in columns they span
    wide = [item for item in col_items if item[1] >= 7.5]
    worst = 0.0
    worst_pos: str | None = None
    for mean_cx, intervals in clusters:
        combined = list(intervals)
        for wx, ww, wy0, wy1 in wide:
            if wx - 0.1 <= mean_cx <= wx + ww + 0.1:
                combined.append((wy0, wy1))
        merged = _merge_intervals(combined, band_top, band_bottom)
        gap, pos = _max_void(merged, band_top, band_bottom)
        if gap > worst:
            worst = gap
            worst_pos = pos
    return round(worst, 3), worst_pos


def score_occupancy(
    page: PageDesignEntry,
    *,
    measured_boxes: Mapping[str, TextMeasurement] | None = None,
    cell_inputs: Sequence[CellInput] | None = None,
) -> PageOccupancyResult:
    """Measure contiguous vertical whitespace, per-column 2D void, and card occupancy inside the slide content band."""
    lid = normalize_layout_id(page.layout_id or page.layout_name or "")
    canvas_w = float(page.canvas_width_in or CANVAS_W_IN)
    canvas_h = float(page.canvas_height_in or CANVAS_H_IN)

    # Identify top header title and bottom chrome (footer / compact placeholder tray)
    header_bottoms = [
        el.y_in + el.h_in
        for el in page.elements
        if el.kind is ElementKind.title and el.y_in < 1.20 and (el.y_in + el.h_in) <= 2.20
    ]
    band_top = round(max(DEFAULT_CONTENT_TOP_IN, min(header_bottoms) if header_bottoms else DEFAULT_CONTENT_TOP_IN), 3)

    bottom_chrome_tops = [
        el.y_in
        for el in page.elements
        if (el.kind is ElementKind.placeholder and el.y_in >= 6.00)
        or (el.kind is ElementKind.footer and el.role != "anchored_citation" and el.y_in >= 6.40)
    ]
    band_bottom = round(min(DEFAULT_CONTENT_BOTTOM_IN, min(bottom_chrome_tops) if bottom_chrome_tops else DEFAULT_CONTENT_BOTTOM_IN), 3)

    # Measure intervals regardless so even exempt slides carry quantitative context if inspected
    rects: list[tuple[float, float]] = []
    col_items: list[tuple[float, float, float, float]] = []
    used_live = False
    for el in page.elements:
        if _is_full_page_background(el, canvas_w, canvas_h):
            continue
        if _is_decorative_shape(el):
            continue
        if el.kind is ElementKind.placeholder:
            continue
        if el.kind is ElementKind.footer and el.role != "anchored_citation":
            continue
        if el.kind is ElementKind.title and el.y_in < 1.20 and (el.y_in + el.h_in) <= 2.20:
            continue

        top_y = float(el.y_in)
        h_in = float(el.h_in)
        if measured_boxes and el.kind in (
            ElementKind.title,
            ElementKind.body,
            ElementKind.bullet,
            ElementKind.caption,
            ElementKind.footer,
        ):
            m = measured_boxes.get(el.element_id)
            if m is not None and m.bound_h_pt > 0:
                used_live = True
                h_in = min(h_in, (m.bound_h_pt / 72.0) + 0.16)
        rects.append((top_y, top_y + max(0.0, h_in)))
        if el.kind in (ElementKind.body, ElementKind.bullet) or el.role == "anchored_citation":
            col_items.append((float(el.x_in), float(el.w_in), top_y, top_y + max(0.0, h_in)))

    merged = _merge_intervals(rects, band_top, band_bottom)
    max_void_in, void_pos = _max_void(merged, band_top, band_bottom)
    effective_cells = list(cell_inputs) if cell_inputs is not None else extract_cell_inputs(page, measured_boxes)
    cell_res = score_cell_occupancy(effective_cells) if effective_cells else CellOccupancyResult(filled=True, failures=[])
    max_col_void_in, col_void_pos = _score_per_column_void(page, col_items, band_top, band_bottom)
    page_role, is_exempt, exempt_reason = infer_page_role(page)

    if is_exempt:
        return PageOccupancyResult(
            page_id=page.page_id,
            layout_id=lid,
            page_role=page_role,
            passed=True,
            exempt=True,
            exempt_reason=exempt_reason,
            max_void_in=max_void_in,
            void_position=void_pos,
            content_band=(band_top, band_bottom),
            detail=f"{page.page_id} ({lid}): exempt ({exempt_reason}; max_void={max_void_in:.2f}in)",
            cell_failures=[],
            used_live_measurements=used_live,
            max_col_void_in=0.0,
        )

    void_fail = bool(merged and max_void_in >= VOID_MAX_IN)
    col_void_fail = max_col_void_in >= VOID_MAX_IN
    passed = (not void_fail) and (not col_void_fail) and cell_res.filled
    parts: list[str] = []
    if void_fail:
        parts.append(f"{max_void_in:.2f}in void at {void_pos or 'bottom'} (>= {VOID_MAX_IN:.2f}in)")
    if col_void_fail:
        parts.append(f"column_void={max_col_void_in:.2f}in at {col_void_pos or 'bottom'} (>= {VOID_MAX_IN:.2f}in)")
    if not cell_res.filled:
        parts.extend(f.detail for f in cell_res.failures)
    if not parts:
        extra = f", max_col_void={max_col_void_in:.2f}in" if max_col_void_in > 0 else ""
        parts.append(f"ok (max_void={max_void_in:.2f}in{extra} < {VOID_MAX_IN:.2f}in)")

    return PageOccupancyResult(
        page_id=page.page_id,
        layout_id=lid or "unknown",
        page_role=page_role,
        passed=passed,
        exempt=False,
        exempt_reason="",
        max_void_in=max_void_in,
        void_position=void_pos,
        content_band=(band_top, band_bottom),
        detail=f"{page.page_id} ({lid or 'unknown'}): {'; '.join(parts)}",
        cell_failures=cell_res.failures,
        used_live_measurements=used_live,
        max_col_void_in=max_col_void_in,
    )


def check_deck_occupancy(
    design: PageDesign,
    *,
    measure_result: MeasureResult | None = None,
    cell_inputs_by_page: Mapping[str, Sequence[CellInput]] | None = None,
) -> DeckOccupancyReport:
    """Run ``score_occupancy`` across every page in ``PageDesign``."""
    by_page_meas: dict[str, dict[str, TextMeasurement]] = {}
    if measure_result is not None:
        for m in measure_result.measurements:
            by_page_meas.setdefault(m.page_id, {})[m.shape_name] = m

    pages: list[PageOccupancyResult] = []
    for p in design.pages:
        pages.append(
            score_occupancy(
                p,
                measured_boxes=by_page_meas.get(p.page_id),
                cell_inputs=(cell_inputs_by_page.get(p.page_id) if cell_inputs_by_page is not None else None),
            )
        )

    flagged = [r for r in pages if not r.exempt and not r.passed]
    exempt = [r for r in pages if r.exempt]
    exempt_desc = ", ".join(f"{r.page_id} ({r.exempt_reason})" for r in exempt)
    if flagged:
        flag_parts = []
        for r in flagged:
            reasons = [f"{r.max_void_in:.2f}in {r.void_position or 'bottom'}"]
            if r.max_col_void_in >= VOID_MAX_IN:
                reasons.append(f"col_void={r.max_col_void_in:.2f}in")
            if r.cell_failures:
                reasons.append(f"{len(r.cell_failures)} cell issue(s): {r.cell_failures[0].kind}")
            flag_parts.append(f"{r.page_id} ({r.layout_id}: {', '.join(reasons)})")
        flag_desc = "; ".join(flag_parts)
        detail = f"{len(flagged)} underfilled page(s): {flag_desc}"
        if exempt_desc:
            detail += f" | exempt: {exempt_desc}"
    else:
        worst = max((max(r.max_void_in, r.max_col_void_in) for r in pages if not r.exempt), default=0.0)
        detail = f"all {len(pages) - len(exempt)} non-exempt slide(s) pass fill_rate (max_void={worst:.2f}in < {VOID_MAX_IN:.2f}in)"
        if exempt_desc:
            detail += f" | exempt: {exempt_desc}"

    return DeckOccupancyReport(
        pages=pages,
        flagged_pages=flagged,
        exempt_pages=exempt,
        passed=not flagged,
        detail=detail,
    )
