"""Geometry, font-floor, text-capacity, provenance, and constraint checks for Stage F (PageDesign)."""

from __future__ import annotations

from pathlib import Path

from pptv01 import textcheck
from pptv01.contracts import (
    AssetRegistry,
    AssetStatus,
    BOUND_VISUAL_KINDS,
    DesignElement,
    ElementKind,
    PageDesign,
    PageDesignEntry,
    ProductionPlan,
    SlideAssetMapping,
    SlidePlan,
    TEXT_KINDS,
    UserInputRequests,
    UserRequestFallback,
    UserRequestStatus,
)

from .capacity import estimate_slot_capacity, wrap_mixed_text

MIN_FONT_PT: dict[ElementKind, float] = {
    ElementKind.title: 20.0,
    ElementKind.body: 12.0,
    ElementKind.bullet: 12.0,
    ElementKind.caption: 9.0,
    ElementKind.footer: 9.0,
    ElementKind.placeholder: 11.0,
}

_EPS = 0.01


def _overlap_area(a: DesignElement, b: DesignElement) -> float:
    x1 = max(a.x_in, b.x_in)
    y1 = max(a.y_in, b.y_in)
    x2 = min(a.x_in + a.w_in, b.x_in + b.w_in)
    y2 = min(a.y_in + a.h_in, b.y_in + b.h_in)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    return (x2 - x1) * (y2 - y1)


def validate_page_geometry(page: PageDesignEntry) -> list[str]:
    """Check canvas bounds, font-size floors, pairwise foreground overlap, and text capacity."""
    problems: list[str] = []
    for e in page.elements:
        if (
            e.x_in < -_EPS
            or e.y_in < -_EPS
            or e.x_in + e.w_in > page.canvas_width_in + _EPS
            or e.y_in + e.h_in > page.canvas_height_in + _EPS
        ):
            problems.append(f"{page.page_id}.{e.element_id}: outside canvas ({page.canvas_width_in}x{page.canvas_height_in})")

        floor = MIN_FONT_PT.get(e.kind)
        if floor is not None and e.font_size_pt is not None and e.font_size_pt < floor - 1e-6:
            problems.append(f"{page.page_id}.{e.element_id}: font_size_pt {e.font_size_pt:g} < floor {floor:g}")

        if (e.kind in TEXT_KINDS or e.kind is ElementKind.placeholder) and e.text and e.font_size_pt:
            cap = estimate_slot_capacity(e.w_in, e.h_in, e.font_size_pt)
            lines = wrap_mixed_text(e.text, e.w_in, e.font_size_pt)
            if len(lines) > cap.max_lines:
                problems.append(
                    f"{page.page_id}.{e.element_id}: text overflows box ({len(lines)} lines > capacity {cap.max_lines} at {e.font_size_pt:g}pt)"
                )

    fg = [e for e in page.elements if e.kind is not ElementKind.shape]
    for i in range(len(fg)):
        for j in range(i + 1, len(fg)):
            a, b = fg[i], fg[j]
            area = _overlap_area(a, b)
            if area <= 0:
                continue
            min_area = min(a.w_in * a.h_in, b.w_in * b.h_in)
            if area / max(min_area, 1e-6) > 0.05:
                problems.append(f"{page.page_id}: foreground overlap between {a.element_id} and {b.element_id} ({area:.2f} sq in)")
    return problems


def validate_page_design(
    design: PageDesign,
    slide_plan: SlidePlan,
    production: ProductionPlan,
    mapping: SlideAssetMapping,
    reg: AssetRegistry,
    uir: UserInputRequests | None,
    run_dir: Path,
) -> list[str]:
    problems: list[str] = []
    run_dir = Path(run_dir)

    want_pages = [p.page_id for p in slide_plan.pages]
    got_pages = [p.page_id for p in design.pages]
    if got_pages != want_pages:
        problems.append(f"design pages {got_pages} do not match slide_plan pages {want_pages}")

    lim = textcheck.page_limit(production.source_utterance)
    if lim is not None:
        kind, val = lim
        n = len(design.pages)
        ok = n <= val if kind == "max" else n == val
        if not ok:
            problems.append(f"user page limit violated: {kind} {val}, design has {n}")

    valid_assets = {
        a.asset_id: a
        for a in reg.assets
        if a.status in (AssetStatus.acquired, AssetStatus.drawn, AssetStatus.substituted) and a.file_path
    }
    valid_inputs = {
        r.request_id: r
        for r in (uir.requests if uir else [])
        if r.status is UserRequestStatus.provided and r.provided and (run_dir / r.provided.file).is_file()
    }

    page_by_id = {p.page_id: p for p in design.pages}

    # Check geometry & bound visuals on each page
    for page in design.pages:
        problems.extend(validate_page_geometry(page))
        for e in page.elements:
            if e.kind in BOUND_VISUAL_KINDS:
                ok_asset = e.asset_id in valid_assets and valid_assets[e.asset_id].file_path == e.asset_path
                ok_input = e.request_id in valid_inputs and valid_inputs[e.request_id].provided.file == e.asset_path
                if not (ok_asset or ok_input):
                    problems.append(
                        f"{page.page_id}.{e.element_id}: unbound or mismatched visual asset "
                        f"(asset_id={e.asset_id}, request_id={e.request_id}, path={e.asset_path})"
                    )
                if e.asset_path and not (run_dir / e.asset_path).is_file():
                    problems.append(f"{page.page_id}.{e.element_id}: bound file {e.asset_path} missing on disk")

    # Check all open/placeholder_rendered requests appear as PLACEHOLDER_U### on their target pages
    if uir is not None:
        for r in uir.requests:
            if r.status in (UserRequestStatus.open, UserRequestStatus.placeholder_rendered) and r.fallback is not UserRequestFallback.omit_element:
                for pid in r.page_ids:
                    p = page_by_id.get(pid)
                    if p is None:
                        continue
                    has_ph = any(
                        e.kind is ElementKind.placeholder and e.request_id == r.request_id
                        for e in p.elements
                    )
                    if not has_ph:
                        problems.append(f"{pid}: missing PLACEHOLDER_{r.request_id} element for open/placeholder request")

    # Check all accepted mappings appear on their mapped pages (as visual element or citation footer)
    for m in mapping.mappings:
        if not m.accepted:
            continue
        p = page_by_id.get(m.page_id)
        if p is None:
            continue
        represented = any(
            (m.asset_id and e.asset_id == m.asset_id)
            or (m.request_id and e.request_id == m.request_id)
            or (m.asset_id and m.asset_id in (e.text or ""))
            for e in p.elements
        )
        if not represented:
            problems.append(f"{m.page_id}: accepted mapping ({m.asset_id or m.request_id}) is not placed on the slide")

    return problems
