"""Stage F: Page Design + Pillow schematic previews (page_design.json, previews/).

Produces a deterministic, geometry-checked 13.333 x 7.5 in layout for every page in SlidePlan:
  - Adapts visual slots to real image aspect ratios and records adjustments in page.notes.
  - Fits text using CJK/Latin capacity estimation (adapted from ppt-master slot_capacity, MIT),
    reducing font size (down to floor) or wrapping/expanding instead of silent truncation.
  - Places bound assets (acquired/drawn/substituted or provided U###) and explicit
    PLACEHOLDER_U### elements for open/placeholder requests.
  - Respects user-stated page-limit and concise-text constraints.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from pptv01.contracts import (
    AssetRecord,
    AssetRegistry,
    AssetStatus,
    CANVAS_H_IN,
    CANVAS_W_IN,
    DesignElement,
    ElementKind,
    PageDesign,
    PageDesignEntry,
    ProductionPlan,
    SCHEMA_VERSION,
    SlideAssetMapping,
    SlidePage,
    SlidePlan,
    UserInputRequest,
    UserInputRequests,
    UserRequestFallback,
    UserRequestKind,
    UserRequestStatus,
    write_contract,
)

from .capacity import estimate_text_height_in, fit_text_to_box
from .preview import render_schematic_preview
from .validate import MIN_FONT_PT, validate_page_design

ARTIFACT = "page_design.json"

_CONCISE_RE = re.compile(
    r"少字|字少|文字精简|少放文字|少写字|少文字|简短|精炼|言简意赅|less text|minimal text|fewer words|concise",
    re.I,
)
_CHAR_LIMIT_RE = re.compile(r"(?:每页)?(?:不超过|最多|至多)\s*(\d+)\s*(?:个)?字")

# Palette (academic clean slate & navy)
C_NAVY = "#1B365D"
C_ACCENT = "#2B6CB0"
C_TEXT = "#1E293B"
C_MUTED = "#475569"
C_BG_CARD = "#F8FAFC"
C_BORDER = "#CBD5E1"
C_HEADER_BG = "#EFF6FF"
C_PH_FILL = "#FEF3C7"
C_PH_LINE = "#D97706"
C_PH_TEXT = "#92400E"
DEFAULT_FONT = "Microsoft YaHei"

# Standard academic slide grid (13.333 x 7.5 in, 0.65 in side margins -> 12.03 in usable width)
GRID_MARGIN_X = 0.65
GRID_USABLE_W = 12.03
GRID_GUTTER = 0.43
SPLIT_VIS_W = 6.00
SPLIT_TEXT_W = round(GRID_USABLE_W - SPLIT_VIS_W - GRID_GUTTER, 3)  # 5.60 in
CROP_FILL_MIN_AR = 0.75
CROP_FILL_MAX_AR = 2.40


class LayoutRuleError(ValueError):
    pass


@dataclass
class _SlotItem:
    slot_type: str             # "image" | "chart" | "table" | "diagram_json" | "placeholder"
    asset_id: str | None
    request_id: str | None
    asset_path: str | None
    label: str
    width_px: int | None = None
    height_px: int | None = None
    attribution: str = ""


def _concise_budget(production: ProductionPlan) -> int | None:
    texts = [production.source_utterance, *production.constraints]
    joined = "\n".join(texts)
    m = _CHAR_LIMIT_RE.search(joined)
    if m:
        return max(20, int(m.group(1)))
    if _CONCISE_RE.search(joined):
        return 75
    return None


def _condense_text_for_budget(points: list[str], budget: int) -> tuple[str, bool]:
    """Condense bullet points to respect a user-stated concise-text budget without mid-word clipping."""
    cleaned = [p.strip().rstrip("。；;") for p in points if p.strip()]
    joined = "\n".join(f"• {p}" for p in cleaned)
    if len(joined) <= budget:
        return joined, False
    out: list[str] = []
    used = 0
    for p in cleaned:
        # Take first clause if point is long
        clause = re.split(r"[，,；;。]", p)[0].strip() or p
        line = f"• {clause}"
        if out and used + len(line) + 1 > budget:
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out) if out else f"• {cleaned[0][:max(12, budget - 2)]}", True


def _image_dims(run_dir: Path, rel_path: str, asset: AssetRecord | None) -> tuple[int, int] | None:
    if asset and asset.width and asset.height:
        return asset.width, asset.height
    p = run_dir / rel_path
    if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
        try:
            with Image.open(p) as im:
                return im.size
        except OSError:
            return None
    return None


def _fit_rect_aspect(
    x: float, y: float, w: float, h: float, dims: tuple[int, int] | None
) -> tuple[float, float, float, float, bool]:
    if not dims or dims[0] <= 0 or dims[1] <= 0:
        return x, y, w, h, False
    iw, ih = dims
    box_ar = w / h
    img_ar = iw / ih
    if abs(box_ar - img_ar) / max(img_ar, 1e-6) < 0.08:
        return x, y, w, h, False
    if img_ar > box_ar:
        new_w = w
        new_h = max(0.8, w / img_ar)
    else:
        new_h = h
        new_w = max(0.8, h * img_ar)
    nx = x + (w - new_w) / 2.0
    ny = y + (h - new_h) / 2.0
    return round(nx, 3), round(ny, 3), round(new_w, 3), round(new_h, 3), True


def _format_ref_citation(run_dir: Path, rel_path: str, label_id: str) -> str:
    p = run_dir / rel_path
    if p.is_file() and p.suffix.lower() == ".json":
        try:
            d = json.loads(p.read_text("utf-8"))
            title = str(d.get("title", "")).strip()
            year = d.get("year")
            doi = str(d.get("doi", "")).strip()
            yr = f" ({year})" if year else ""
            doi_s = f" DOI:{doi}" if doi else ""
            if title:
                return f"[{label_id}] {title[:70]}{yr}{doi_s}"
        except (OSError, json.JSONDecodeError):
            pass
    return f"[{label_id}] {rel_path}"


def _build_page_items(
    page: SlidePage,
    mapping: SlideAssetMapping,
    reg_by_id: dict[str, AssetRecord],
    uir_by_id: dict[str, UserInputRequest],
    run_dir: Path,
) -> tuple[list[_SlotItem], list[str]]:
    visuals: list[_SlotItem] = []
    citations: list[str] = []
    seen_visual_keys: set[tuple[str | None, str | None]] = set()

    for m in mapping.mappings:
        if m.page_id != page.page_id or not m.accepted:
            continue
        asset = reg_by_id.get(m.asset_id) if m.asset_id else None
        req = uir_by_id.get(m.request_id) if m.request_id else None

        if req and req.status is UserRequestStatus.provided and req.provided:
            fpath = req.provided.file
            kind = req.kind
            attr = f"来源: {req.provided.source} ({req.provided.license})"
        elif asset and asset.status in (AssetStatus.acquired, AssetStatus.drawn, AssetStatus.substituted) and asset.file_path:
            fpath = asset.file_path
            kind_str = asset.type.value if asset.type else "image"
            kind = UserRequestKind.chart if kind_str == "chart_data" else UserRequestKind(kind_str) if kind_str in UserRequestKind._value2member_map_ else UserRequestKind.other
            attr = asset.attribution or (f"{asset.source_name}: {asset.license_or_terms}" if asset.source_name else "")
        else:
            continue

        if kind is UserRequestKind.reference:
            citations.append(_format_ref_citation(run_dir, fpath, m.asset_id or m.request_id or "ref"))
            continue

        key = (m.asset_id, m.request_id)
        if key in seen_visual_keys:
            continue
        seen_visual_keys.add(key)

        ext = Path(fpath).suffix.lower()
        if kind in (UserRequestKind.image, UserRequestKind.icon) or (kind is UserRequestKind.diagram and ext in (".png", ".jpg", ".jpeg", ".webp")):
            dims = _image_dims(run_dir, fpath, asset)
            visuals.append(_SlotItem(
                slot_type="image",
                asset_id=m.asset_id,
                request_id=m.request_id,
                asset_path=fpath,
                label=m.usage,
                width_px=dims[0] if dims else None,
                height_px=dims[1] if dims else None,
                attribution=attr,
            ))
        elif kind in (UserRequestKind.chart, UserRequestKind.statistic):
            visuals.append(_SlotItem(
                slot_type="chart",
                asset_id=m.asset_id,
                request_id=m.request_id,
                asset_path=fpath,
                label=m.usage,
                attribution=attr,
            ))
        elif kind is UserRequestKind.table:
            visuals.append(_SlotItem(
                slot_type="table",
                asset_id=m.asset_id,
                request_id=m.request_id,
                asset_path=fpath,
                label=m.usage,
                attribution=attr,
            ))
        elif kind is UserRequestKind.diagram:
            visuals.append(_SlotItem(
                slot_type="diagram_json",
                asset_id=m.asset_id,
                request_id=m.request_id,
                asset_path=fpath,
                label=m.usage,
                attribution=attr,
            ))

    # Open / placeholder requests targeting this page
    for req in uir_by_id.values():
        if page.page_id not in req.page_ids:
            continue
        if req.status in (UserRequestStatus.open, UserRequestStatus.placeholder_rendered) and req.fallback is not UserRequestFallback.omit_element:
            visuals.append(_SlotItem(
                slot_type="placeholder",
                asset_id=req.asset_id,
                request_id=req.request_id,
                asset_path=None,
                label=f"待补充：{req.need}（{req.request_id}）",
            ))

    return visuals, citations


def _make_fitted_text(
    element_id: str,
    kind: ElementKind,
    x: float,
    y: float,
    w: float,
    h: float,
    text: str,
    start_pt: float,
    *,
    z_index: int = 2,
    align: str = "left",
    bold: bool = False,
    color_hex: str = C_TEXT,
    role: str = "",
    page_notes: list[str],
) -> DesignElement:
    floor = MIN_FONT_PT.get(kind, 12.0)
    fit = fit_text_to_box(text, w, h, start_pt, min_font_pt=floor)
    if fit.adjusted:
        page_notes.append(f"{element_id}: {fit.note}")
    return DesignElement(
        element_id=element_id,
        kind=kind,
        x_in=round(x, 3),
        y_in=round(y, 3),
        w_in=round(w, 3),
        h_in=round(h, 3),
        z_index=z_index,
        align=align,  # type: ignore[arg-type]
        text=fit.text,
        font=DEFAULT_FONT,
        font_size_pt=fit.font_size_pt,
        bold=bold,
        color_hex=color_hex,
        role=role,
    )


def _design_visual_element(
    item: _SlotItem,
    idx: int,
    x: float,
    y: float,
    w: float,
    h: float,
    page_notes: list[str],
) -> list[DesignElement]:
    out: list[DesignElement] = []
    if item.slot_type == "placeholder":
        ph_text = item.label
        fit = fit_text_to_box(ph_text, w, h, 14.0, min_font_pt=MIN_FONT_PT[ElementKind.placeholder])
        if fit.adjusted:
            page_notes.append(f"PLACEHOLDER_{item.request_id}: {fit.note}")
        out.append(DesignElement(
            element_id=f"PLACEHOLDER_{item.request_id}",
            kind=ElementKind.placeholder,
            x_in=round(x, 3),
            y_in=round(y, 3),
            w_in=round(w, 3),
            h_in=round(h, 3),
            z_index=2,
            align="center",
            text=fit.text,
            font=DEFAULT_FONT,
            font_size_pt=fit.font_size_pt,
            color_hex=C_PH_TEXT,
            fill_hex=C_PH_FILL,
            line_hex=C_PH_LINE,
            role="placeholder",
            asset_id=item.asset_id,
            request_id=item.request_id,
        ))
        return out

    if item.slot_type == "image":
        cap_text = item.attribution.strip()
        cap_h = 0.0
        if cap_text:
            # Give attribution caption enough height for up to 2 wrapped lines at 9-10pt
            cap_fit = fit_text_to_box(cap_text, w, 0.60, 10.0, min_font_pt=MIN_FONT_PT[ElementKind.caption])
            if cap_fit.still_overflows:
                cap_h = 0.80
            elif len(cap_fit.lines) > 1:
                cap_h = 0.60
            else:
                cap_h = 0.42
        img_h = max(1.2, round(h - cap_h - (0.08 if cap_h else 0.0), 3))
        dims = (item.width_px, item.height_px) if (item.width_px and item.height_px) else None
        img_ar = (dims[0] / dims[1]) if (dims and dims[0] > 0 and dims[1] > 0) else None
        if img_ar is not None and (img_ar < CROP_FILL_MIN_AR or img_ar > CROP_FILL_MAX_AR):
            # Extreme portrait or panorama: adapt box to preserve subject without excessive cropping
            nx, ny, nw, nh, adapted = _fit_rect_aspect(x, y, w, img_h, dims)
            if adapted and dims:
                page_notes.append(f"vis_{idx}: adapted image box to aspect ratio {dims[0]}x{dims[1]}")
        else:
            # Standard landscape/square/moderate-portrait: uniform slot filled cleanly via PowerPoint crop_fill
            nx, ny, nw, nh = round(x, 3), round(y, 3), round(w, 3), round(img_h, 3)
            if dims:
                page_notes.append(f"vis_{idx}: uniform crop_fill slot {nw:.2f}x{nh:.2f}in for {dims[0]}x{dims[1]}")
        out.append(DesignElement(
            element_id=f"vis_{idx}",
            kind=ElementKind.image,
            x_in=nx,
            y_in=ny,
            w_in=nw,
            h_in=nh,
            z_index=2,
            role="primary_visual",
            asset_id=item.asset_id,
            request_id=item.request_id,
            asset_path=item.asset_path,
        ))
        if cap_h > 0:
            cap_y = round(y + img_h + 0.08, 3)
            out.append(_make_fitted_text(
                f"cap_{idx}", ElementKind.caption, x, cap_y, w, cap_h,
                cap_text, 10.0, z_index=2, color_hex=C_MUTED, role="attribution_caption",
                page_notes=page_notes,
            ))
        return out

    if item.slot_type in ("chart", "table"):
        kind = ElementKind.chart if item.slot_type == "chart" else ElementKind.table
        out.append(DesignElement(
            element_id=f"vis_{idx}",
            kind=kind,
            x_in=round(x, 3),
            y_in=round(y, 3),
            w_in=round(w, 3),
            h_in=round(h, 3),
            z_index=2,
            role=f"data_{item.slot_type}",
            asset_id=item.asset_id,
            request_id=item.request_id,
            asset_path=item.asset_path,
        ))
        return out

    # diagram_json rendered as structured native diagram container
    out.append(DesignElement(
        element_id=f"diag_{idx}",
        kind=ElementKind.shape,
        x_in=round(x, 3),
        y_in=round(y, 3),
        w_in=round(w, 3),
        h_in=round(h, 3),
        z_index=1,
        fill_hex=C_BG_CARD,
        line_hex=C_ACCENT,
        role="diagram_json",
        asset_id=item.asset_id,
        request_id=item.request_id,
        asset_path=item.asset_path,
    ))
    return out


def _place_text_panel(
    elements: list[DesignElement],
    x: float,
    y: float,
    w: float,
    h: float,
    bullet_text: str,
    body_text: str | None,
    body_start_pt: float,
    notes: list[str],
    *,
    with_accent_rail: bool = True,
) -> float:
    """Place content-fitted text without an oversized empty background card.

    Shrinks the text box height to calibrated content height (with comfortable breathing room)
    and pairs it with a slim left accent rail matching the actual text block height.
    Returns the final text block height in inches.
    """
    rail_offset = 0.20 if with_accent_rail else 0.0
    txt_x = round(x + rail_offset, 3)
    txt_w = round(max(1.5, w - rail_offset), 3)
    floor = MIN_FONT_PT[ElementKind.bullet]

    fit = fit_text_to_box(bullet_text, txt_w, h, body_start_pt, min_font_pt=floor)
    fitted_h = estimate_text_height_in(fit.lines, fit.font_size_pt)
    # Add modest vertical cushion (~0.24in) so PowerPoint never clips last line descenders,
    # while still shrinking tightly to content instead of leaving an empty full-height box.
    box_h = round(min(h, max(0.75, fitted_h + 0.24)), 3)
    if box_h < h - 0.5:
        notes.append(f"bullets: shrunk text box height to {box_h:.2f}in (content-fitted, no empty card)")

    if with_accent_rail:
        elements.append(
            DesignElement(
                element_id="accent_rail",
                kind=ElementKind.shape,
                x_in=round(x, 3),
                y_in=round(y, 3),
                w_in=0.06,
                h_in=box_h,
                z_index=1,
                fill_hex=C_ACCENT,
                line_hex=C_ACCENT,
                role="accent_rail",
            )
        )

    if fit.adjusted:
        notes.append(f"bullets: {fit.note}")
    elements.append(
        DesignElement(
            element_id="bullets",
            kind=ElementKind.bullet,
            x_in=txt_x,
            y_in=round(y, 3),
            w_in=txt_w,
            h_in=box_h,
            z_index=2,
            align="left",
            text=fit.text,
            font=DEFAULT_FONT,
            font_size_pt=fit.font_size_pt,
            bold=False,
            color_hex=C_TEXT,
            role="key_points",
        )
    )
    return box_h


def _design_single_page(
    page: SlidePage,
    page_idx: int,
    production: ProductionPlan,
    mapping: SlideAssetMapping,
    reg_by_id: dict[str, AssetRecord],
    uir_by_id: dict[str, UserInputRequest],
    run_dir: Path,
    concise_budget: int | None,
) -> PageDesignEntry:
    notes: list[str] = []
    visuals, citations = _build_page_items(page, mapping, reg_by_id, uir_by_id, run_dir)
    elements: list[DesignElement] = []

    # Crisp top accent bar, header band, and hairline divider aligned to page grid
    elements.append(DesignElement(
        element_id="bg_header",
        kind=ElementKind.shape,
        x_in=0.0,
        y_in=0.0,
        w_in=CANVAS_W_IN,
        h_in=1.24,
        z_index=0,
        fill_hex=C_HEADER_BG,
        line_hex=C_HEADER_BG,
        role="header_band",
    ))
    elements.append(DesignElement(
        element_id="accent_bar",
        kind=ElementKind.shape,
        x_in=0.0,
        y_in=0.0,
        w_in=CANVAS_W_IN,
        h_in=0.08,
        z_index=1,
        fill_hex=C_NAVY,
        line_hex=C_NAVY,
        role="top_accent",
    ))
    elements.append(DesignElement(
        element_id="accent_divider",
        kind=ElementKind.shape,
        x_in=GRID_MARGIN_X,
        y_in=1.24,
        w_in=GRID_USABLE_W,
        h_in=0.02,
        z_index=1,
        fill_hex=C_BORDER,
        line_hex=C_BORDER,
        role="header_divider",
    ))

    # Title (aligned exactly to GRID_MARGIN_X .. GRID_MARGIN_X + GRID_USABLE_W)
    elements.append(_make_fitted_text(
        "title", ElementKind.title, GRID_MARGIN_X, 0.22, GRID_USABLE_W, 0.88,
        page.title, 28.0, z_index=2, bold=True, color_hex=C_NAVY, role="header_title",
        page_notes=notes,
    ))

    # Prepare main text content (honoring concise-text constraint when present).
    # B2 rule: page.content_summary is a planning note for speakers/designers, NEVER slide body text.
    if page.content_summary.strip():
        notes.append(f"planning summary (notes only): {page.content_summary.strip()}")
    if concise_budget is not None:
        bullet_text, condensed = _condense_text_for_budget(page.key_points, concise_budget)
        if condensed:
            notes.append(f"condensed text to <= {concise_budget} chars per user concise-text constraint")
        body_text = None
        body_start_pt = 20.0
    else:
        bullet_text = "\n".join(f"• {p}" for p in page.key_points)
        body_text = None
        body_start_pt = 18.0

    # Reserve footer strip if citations exist
    footer_lines = list(citations)
    has_footer = bool(footer_lines)
    content_bottom = 6.72 if has_footer else 7.12
    content_top = 1.50
    content_h = round(content_bottom - content_top, 3)

    if len(visuals) == 0:
        layout_name = "clean_text_focus"
        _place_text_panel(
            elements,
            GRID_MARGIN_X,
            content_top,
            GRID_USABLE_W,
            content_h,
            bullet_text,
            body_text,
            body_start_pt + 2.0,
            notes,
            with_accent_rail=True,
        )
    elif len(visuals) == 1:
        # Alternate visual left/right on odd/even slides for visual rhythm, keeping uniform visual slot width
        visual_right = (page_idx % 2 == 0)
        layout_name = "split_text_left" if visual_right else "split_visual_left"
        if visual_right:
            text_x = GRID_MARGIN_X
            vis_x = round(GRID_MARGIN_X + SPLIT_TEXT_W + GRID_GUTTER, 3)
        else:
            vis_x = GRID_MARGIN_X
            text_x = round(GRID_MARGIN_X + SPLIT_VIS_W + GRID_GUTTER, 3)

        _place_text_panel(
            elements,
            text_x,
            content_top,
            SPLIT_TEXT_W,
            content_h,
            bullet_text,
            body_text,
            body_start_pt,
            notes,
            with_accent_rail=True,
        )
        elements.extend(_design_visual_element(visuals[0], 1, vis_x, content_top, SPLIT_VIS_W, content_h, notes))
    else:
        layout_name = "top_points_multi_visual"
        max_top_h = 2.25
        b_fit = fit_text_to_box(bullet_text, GRID_USABLE_W - 0.20, max_top_h, 16.0, min_font_pt=MIN_FONT_PT[ElementKind.bullet])
        fitted_top_h = estimate_text_height_in(b_fit.lines, b_fit.font_size_pt)
        text_h = round(min(max_top_h, max(0.90, fitted_top_h + 0.18)), 3)
        _place_text_panel(
            elements,
            GRID_MARGIN_X,
            content_top,
            GRID_USABLE_W,
            text_h,
            bullet_text,
            None,
            16.0,
            notes,
            with_accent_rail=True,
        )
        vis_y = round(content_top + text_h + 0.22, 3)
        vis_h = round(max(1.8, content_bottom - vis_y), 3)
        gap = 0.24
        n_vis = len(visuals)
        if n_vis <= 3:
            col_w = round((GRID_USABLE_W - gap * (n_vis - 1)) / n_vis, 3)
            for i, item in enumerate(visuals):
                vx = round(GRID_MARGIN_X + i * (col_w + gap), 3)
                # Snap final column right edge exactly to GRID_MARGIN_X + GRID_USABLE_W
                cw = round(GRID_MARGIN_X + GRID_USABLE_W - vx, 3) if i == n_vis - 1 else col_w
                elements.extend(_design_visual_element(item, i + 1, vx, vis_y, cw, vis_h, notes))
        else:
            import math
            rows = 2
            cols = math.ceil(n_vis / rows)
            cell_w = round((GRID_USABLE_W - gap * (cols - 1)) / cols, 3)
            cell_h = round((vis_h - gap) / rows, 3)
            for i, item in enumerate(visuals):
                r_idx, c_idx = divmod(i, cols)
                vx = round(GRID_MARGIN_X + c_idx * (cell_w + gap), 3)
                vy = round(vis_y + r_idx * (cell_h + gap), 3)
                cw = round(GRID_MARGIN_X + GRID_USABLE_W - vx, 3) if c_idx == cols - 1 else cell_w
                elements.extend(_design_visual_element(item, i + 1, vx, vy, cw, cell_h, notes))

    if has_footer:
        elements.append(_make_fitted_text(
            "footer", ElementKind.footer, GRID_MARGIN_X, 6.84, GRID_USABLE_W, 0.52,
            " | ".join(footer_lines), 10.0, z_index=2, color_hex=C_MUTED, role="citation_footer",
            page_notes=notes,
        ))

    preview_rel = f"previews/{page.page_id}_schematic.png"
    entry = PageDesignEntry(
        page_id=page.page_id,
        layout_name=layout_name,
        canvas_width_in=CANVAS_W_IN,
        canvas_height_in=CANVAS_H_IN,
        elements=elements,
        preview_path=preview_rel,
        notes="; ".join(notes),
    )
    render_schematic_preview(entry, run_dir, preview_rel)
    return entry


def run_stage_f(
    production: ProductionPlan,
    slide_plan: SlidePlan,
    reg: AssetRegistry,
    uir: UserInputRequests | None,
    mapping: SlideAssetMapping,
    run_dir: Path,
) -> PageDesign:
    run_dir = Path(run_dir)
    reg_by_id = {a.asset_id: a for a in reg.assets}
    uir_by_id = {r.request_id: r for r in uir.requests} if uir else {}
    concise_budget = _concise_budget(production)

    pages = [
        _design_single_page(
            p,
            idx,
            production,
            mapping,
            reg_by_id,
            uir_by_id,
            run_dir,
            concise_budget,
        )
        for idx, p in enumerate(slide_plan.pages)
    ]

    design = PageDesign(
        schema_version=SCHEMA_VERSION,
        run_id=production.run_id,
        pages=pages,
    )
    problems = validate_page_design(design, slide_plan, production, mapping, reg, uir, run_dir)
    if problems:
        raise LayoutRuleError("page design validation failed:\n- " + "\n- ".join(problems))
    write_contract(design, run_dir / ARTIFACT)
    return design
