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

from .capacity import fit_text_to_box
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
DEFAULT_FONT = "Noto Sans CJK SC"


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
        img_h = max(1.0, h - cap_h - (0.08 if cap_h else 0.0))
        dims = (item.width_px, item.height_px) if (item.width_px and item.height_px) else None
        nx, ny, nw, nh, adapted = _fit_rect_aspect(x, y, w, img_h, dims)
        if adapted and dims:
            page_notes.append(f"vis_{idx}: adapted image box to aspect ratio {dims[0]}x{dims[1]}")
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
            out.append(_make_fitted_text(
                f"cap_{idx}", ElementKind.caption, x, y + img_h + 0.06, w, cap_h,
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
) -> None:
    if body_text:
        split_h = round(h * 0.58, 3)
        sum_h = max(0.9, h - split_h - 0.38)
        b_fit = fit_text_to_box(bullet_text, w, split_h, body_start_pt, min_font_pt=MIN_FONT_PT[ElementKind.bullet])
        s_fit = fit_text_to_box(body_text, w, sum_h, 14.5, min_font_pt=MIN_FONT_PT[ElementKind.body])
        if not b_fit.still_overflows and not s_fit.still_overflows:
            elements.append(_make_fitted_text(
                "bullets", ElementKind.bullet, x, y + 0.16, w, split_h,
                bullet_text, body_start_pt, z_index=2, role="key_points", page_notes=notes,
            ))
            elements.append(_make_fitted_text(
                "summary", ElementKind.body, x, y + 0.24 + split_h, w, sum_h,
                body_text, 14.5, z_index=2, color_hex=C_MUTED, role="summary_body", page_notes=notes,
            ))
            return
        notes.append("omitted summary box to give key_points full panel height without overflow")
    elements.append(_make_fitted_text(
        "bullets", ElementKind.bullet, x, y + 0.20, w, h - 0.40,
        bullet_text, body_start_pt + 1.0, z_index=2, role="key_points", page_notes=notes,
    ))


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

    # Subtle top accent bar & header background
    elements.append(DesignElement(
        element_id="bg_header",
        kind=ElementKind.shape,
        x_in=0.0,
        y_in=0.0,
        w_in=CANVAS_W_IN,
        h_in=1.25,
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

    # Title
    elements.append(_make_fitted_text(
        "title", ElementKind.title, 0.65, 0.22, 12.0, 0.88,
        page.title, 28.0, z_index=2, bold=True, color_hex=C_NAVY, role="header_title",
        page_notes=notes,
    ))

    # Prepare main text content (honoring concise-text constraint when present)
    if concise_budget is not None:
        bullet_text, condensed = _condense_text_for_budget(page.key_points, concise_budget)
        if condensed:
            notes.append(f"condensed text to <= {concise_budget} chars per user concise-text constraint")
        body_text = None
        body_start_pt = 20.0
    else:
        bullet_text = "\n".join(f"• {p}" for p in page.key_points)
        body_text = page.content_summary.strip() if page.content_summary.strip() and page.content_summary.strip() not in bullet_text else None
        body_start_pt = 18.0

    # Reserve footer strip if citations exist
    footer_lines = list(citations)
    has_footer = bool(footer_lines)
    content_bottom = 6.72 if has_footer else 7.12
    content_top = 1.50
    content_h = content_bottom - content_top

    if len(visuals) == 0:
        layout_name = "structured_text_panel"
        elements.append(DesignElement(
            element_id="card_main",
            kind=ElementKind.shape,
            x_in=0.65,
            y_in=content_top,
            w_in=12.03,
            h_in=content_h,
            z_index=0,
            fill_hex=C_BG_CARD,
            line_hex=C_BORDER,
            role="background_card",
        ))
        _place_text_panel(elements, 0.90, content_top, 11.5, content_h, bullet_text, body_text, body_start_pt + 1.0, notes)
    elif len(visuals) == 1:
        # Alternate visual left/right on odd/even slides for visual rhythm
        visual_right = (page_idx % 2 == 0)
        layout_name = "split_text_left" if visual_right else "split_visual_left"
        text_x = 0.65 if visual_right else 6.75
        vis_x = 6.65 if visual_right else 0.65
        text_w = 5.75
        vis_w = 6.00

        elements.append(DesignElement(
            element_id="card_text",
            kind=ElementKind.shape,
            x_in=text_x,
            y_in=content_top,
            w_in=text_w,
            h_in=content_h,
            z_index=0,
            fill_hex=C_BG_CARD,
            line_hex=C_BORDER,
            role="background_card",
        ))
        _place_text_panel(elements, text_x + 0.20, content_top, text_w - 0.40, content_h, bullet_text, body_text, body_start_pt, notes)
        elements.extend(_design_visual_element(visuals[0], 1, vis_x, content_top, vis_w, content_h, notes))
    else:
        layout_name = "top_points_multi_visual"
        text_h = 1.65
        b_fit = fit_text_to_box(bullet_text, 12.03, text_h, 16.0, min_font_pt=MIN_FONT_PT[ElementKind.bullet])
        if b_fit.still_overflows:
            text_h = 2.35
            notes.append("expanded top bullet area to 2.35in to avoid text overflow")
        elements.append(_make_fitted_text(
            "bullets", ElementKind.bullet, 0.65, content_top, 12.03, text_h,
            bullet_text, 16.0, z_index=2, role="key_points", page_notes=notes,
        ))
        vis_y = content_top + text_h + 0.18
        vis_h = max(1.6, content_bottom - vis_y)
        gap = 0.22
        n_vis = len(visuals)
        if n_vis <= 3:
            col_w = (12.03 - gap * (n_vis - 1)) / n_vis
            for i, item in enumerate(visuals):
                vx = 0.65 + i * (col_w + gap)
                elements.extend(_design_visual_element(item, i + 1, vx, vis_y, col_w, vis_h, notes))
        else:
            import math
            rows = 2
            cols = math.ceil(n_vis / rows)
            cell_w = (12.03 - gap * (cols - 1)) / cols
            cell_h = (vis_h - gap) / rows
            for i, item in enumerate(visuals):
                r_idx, c_idx = divmod(i, cols)
                vx = 0.65 + c_idx * (cell_w + gap)
                vy = vis_y + r_idx * (cell_h + gap)
                elements.extend(_design_visual_element(item, i + 1, vx, vy, cell_w, cell_h, notes))

    if has_footer:
        elements.append(_make_fitted_text(
            "footer", ElementKind.footer, 0.65, 6.84, 12.03, 0.52,
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
