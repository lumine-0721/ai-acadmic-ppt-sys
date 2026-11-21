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

# Anchor / breathing slide types exempt from underfill flagging (exempt reason always recorded in detail).
EXEMPT_LAYOUT_IDS: dict[str, str] = {
    "cover": "cover anchor title slide",
    "section": "section divider breathing slide",
    "quote": "editorial statement breathing slide",
}


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
    cell_failures: list[CellFailure] = field(default_factory=list)
    used_live_measurements: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_id": self.page_id,
            "layout_id": self.layout_id,
            "passed": self.passed,
            "exempt": self.exempt,
            "exempt_reason": self.exempt_reason,
            "max_void_in": self.max_void_in,
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


def score_occupancy(
    page: PageDesignEntry,
    *,
    measured_boxes: Mapping[str, TextMeasurement] | None = None,
    cell_inputs: Sequence[CellInput] = (),
) -> PageOccupancyResult:
    """Measure contiguous vertical whitespace inside the slide content band."""
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

    merged = _merge_intervals(rects, band_top, band_bottom)
    max_void_in, void_pos = _max_void(merged, band_top, band_bottom)
    cell_res = score_cell_occupancy(cell_inputs) if cell_inputs else CellOccupancyResult(filled=True, failures=[])

    if lid in EXEMPT_LAYOUT_IDS:
        reason = EXEMPT_LAYOUT_IDS[lid]
        return PageOccupancyResult(
            page_id=page.page_id,
            layout_id=lid,
            passed=True,
            exempt=True,
            exempt_reason=reason,
            max_void_in=max_void_in,
            void_position=void_pos,
            content_band=(band_top, band_bottom),
            detail=f"{page.page_id} ({lid}): exempt ({reason}; max_void={max_void_in:.2f}in)",
            cell_failures=[],
            used_live_measurements=used_live,
        )

    void_fail = bool(merged and max_void_in >= VOID_MAX_IN)
    passed = (not void_fail) and cell_res.filled
    parts: list[str] = []
    if void_fail:
        parts.append(f"{max_void_in:.2f}in void at {void_pos or 'bottom'} (>= {VOID_MAX_IN:.2f}in)")
    if not cell_res.filled:
        parts.extend(f.detail for f in cell_res.failures)
    if not parts:
        parts.append(f"ok (max_void={max_void_in:.2f}in < {VOID_MAX_IN:.2f}in)")

    return PageOccupancyResult(
        page_id=page.page_id,
        layout_id=lid or "unknown",
        passed=passed,
        exempt=False,
        exempt_reason="",
        max_void_in=max_void_in,
        void_position=void_pos,
        content_band=(band_top, band_bottom),
        detail=f"{page.page_id} ({lid or 'unknown'}): {'; '.join(parts)}",
        cell_failures=cell_res.failures,
        used_live_measurements=used_live,
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
                cell_inputs=(cell_inputs_by_page or {}).get(p.page_id, ()),
            )
        )

    flagged = [r for r in pages if not r.exempt and not r.passed]
    exempt = [r for r in pages if r.exempt]
    exempt_desc = ", ".join(f"{r.page_id} ({r.exempt_reason})" for r in exempt)
    if flagged:
        flag_desc = "; ".join(
            f"{r.page_id} ({r.layout_id}: {r.max_void_in:.2f}in {r.void_position or 'bottom'}"
            + (f", {len(r.cell_failures)} cell issue(s)" if r.cell_failures else "")
            + ")"
            for r in flagged
        )
        detail = f"{len(flagged)} underfilled page(s): {flag_desc}"
        if exempt_desc:
            detail += f" | exempt: {exempt_desc}"
    else:
        worst = max((r.max_void_in for r in pages if not r.exempt), default=0.0)
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
