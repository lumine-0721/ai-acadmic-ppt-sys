"""Stage F: Diverse Page Design + Pillow schematic previews (page_design.json, previews/).

Produces a deterministic, layout-diverse, geometry-checked 13.333 x 7.5 in layout for every page
in SlidePlan:
  - Separates real bound visuals (image/chart/table/diagram_json) from missing-asset placeholders
    so placeholders NEVER hijack hero slots and always remain compact (<= 15% of content area).
  - Selects a canonical `layout_id` per slide (`cover`, `section`, `comparison`, `big-number`,
    `timeline`, `image-left`, `image-right`, `quote`, `full-bleed`, `needs-input-list`) with
    zero adjacent duplicates and deck-wide diversity (>= min(5, n_pages) for n >= 4).
  - Fits text using calibrated CJK/Latin capacity estimation (adapted from ppt-master slot_capacity, MIT).
  - Respects user-stated page-limit and concise-text constraints without leaking content_summary.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from PIL import Image

from pptv01.contracts import (
    AssetRecord,
    AssetRegistry,
    AssetStatus,
    CANVAS_H_IN,
    CANVAS_W_IN,
    PageDesign,
    PageDesignEntry,
    ProductionPlan,
    ResearchRecord,
    SCHEMA_VERSION,
    SlideAssetMapping,
    SlidePage,
    SlidePlan,
    UserInputRequest,
    UserInputRequests,
    UserRequestFallback,
    UserRequestKind,
    UserRequestStatus,
    load_contract,
    write_contract,
)
from pptv01.theme import ACADEMIC_DEFAULT_THEME, DEFAULT_THEME_ID, ThemeTokens, get_theme

from .layouts import (
    CANONICAL_LAYOUT_IDS,
    GRID_GUTTER,
    GRID_MARGIN_X,
    GRID_USABLE_W,
    LEGACY_LAYOUT_ID,
    MAX_PLACEHOLDER_AREA_RATIO,
    SPLIT_TEXT_W,
    SPLIT_VIS_W,
    SlotItem,
    build_layout_elements,
    eligible_layouts_for_page,
    select_deck_layouts,
)
from .preview import render_schematic_preview
from .validate import validate_page_design

ARTIFACT = "page_design.json"

_CONCISE_RE = re.compile(
    r"少字|字少|文字精简|少放文字|少写字|少文字|简短|精炼|言简意赅|less text|minimal text|fewer words|concise",
    re.I,
)
_CHAR_LIMIT_RE = re.compile(r"(?:每页)?(?:不超过|最多|至多)\s*(\d+)\s*(?:个)?字")


class LayoutRuleError(ValueError):
    pass


def _concise_budget(production: ProductionPlan) -> int | None:
    texts = [production.source_utterance, *production.constraints]
    joined = "\n".join(texts)
    m = _CHAR_LIMIT_RE.search(joined)
    if m:
        return max(20, int(m.group(1)))
    if _CONCISE_RE.search(joined):
        return 75
    return None


def _condense_points_for_budget(points: list[str], budget: int) -> tuple[list[str], bool]:
    """Condense bullet points to respect a user-stated concise-text budget without mid-word clipping."""
    cleaned = [p.strip().rstrip("。；;") for p in points if p.strip()]
    joined = "\n".join(f"• {p}" for p in cleaned)
    if len(joined) <= budget:
        return cleaned, False
    out: list[str] = []
    used = 0
    for p in cleaned:
        clause = re.split(r"[，,；;。]", p)[0].strip() or p
        line_len = len(clause) + 2
        if out and used + line_len + 1 > budget:
            break
        out.append(clause)
        used += line_len + 1
    if not out and cleaned:
        out = [cleaned[0][: max(12, budget - 2)]]
    return out, True


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
) -> tuple[list[SlotItem], list[SlotItem], list[str]]:
    """Return ``(real_visuals, placeholders, citations)`` with strict separation."""
    real_visuals: list[SlotItem] = []
    placeholders: list[SlotItem] = []
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
            kind = (
                UserRequestKind.chart
                if kind_str == "chart_data"
                else UserRequestKind(kind_str)
                if kind_str in UserRequestKind._value2member_map_
                else UserRequestKind.other
            )
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
        if kind in (UserRequestKind.image, UserRequestKind.icon) or (
            kind is UserRequestKind.diagram and ext in (".png", ".jpg", ".jpeg", ".webp")
        ):
            dims = _image_dims(run_dir, fpath, asset)
            real_visuals.append(
                SlotItem(
                    slot_type="image",
                    asset_id=m.asset_id,
                    request_id=m.request_id,
                    asset_path=fpath,
                    label=m.usage,
                    width_px=dims[0] if dims else None,
                    height_px=dims[1] if dims else None,
                    attribution=attr,
                )
            )
        elif kind in (UserRequestKind.chart, UserRequestKind.statistic):
            real_visuals.append(
                SlotItem(
                    slot_type="chart",
                    asset_id=m.asset_id,
                    request_id=m.request_id,
                    asset_path=fpath,
                    label=m.usage,
                    attribution=attr,
                )
            )
        elif kind is UserRequestKind.table:
            real_visuals.append(
                SlotItem(
                    slot_type="table",
                    asset_id=m.asset_id,
                    request_id=m.request_id,
                    asset_path=fpath,
                    label=m.usage,
                    attribution=attr,
                )
            )
        elif kind is UserRequestKind.diagram:
            real_visuals.append(
                SlotItem(
                    slot_type="diagram_json",
                    asset_id=m.asset_id,
                    request_id=m.request_id,
                    asset_path=fpath,
                    label=m.usage,
                    attribution=attr,
                )
            )

    # Open / placeholder requests targeting this page -> compact placeholder tray ONLY
    for req in uir_by_id.values():
        if page.page_id not in req.page_ids:
            continue
        if (
            req.status in (UserRequestStatus.open, UserRequestStatus.placeholder_rendered)
            and req.fallback is not UserRequestFallback.omit_element
        ):
            placeholders.append(
                SlotItem(
                    slot_type="placeholder",
                    asset_id=req.asset_id,
                    request_id=req.request_id,
                    asset_path=None,
                    label=f"待补充：{req.need}（{req.request_id}）",
                )
            )

    return real_visuals, placeholders, citations


def _design_single_page(
    page: SlidePage,
    layout_id: str,
    real_visuals: list[SlotItem],
    placeholders: list[SlotItem],
    citations: list[str],
    run_dir: Path,
    concise_budget: int | None,
    *,
    theme: ThemeTokens = ACADEMIC_DEFAULT_THEME,
) -> PageDesignEntry:
    notes: list[str] = []

    # B2 rule: page.content_summary is a planning note for speakers/designers, NEVER slide body text.
    if page.content_summary.strip():
        notes.append(f"planning summary (notes only): {page.content_summary.strip()}")

    if concise_budget is not None:
        points, condensed = _condense_points_for_budget(page.key_points, concise_budget)
        if condensed:
            notes.append(f"condensed text to <= {concise_budget} chars per user concise-text constraint")
    else:
        points = list(page.key_points)

    elements = build_layout_elements(
        layout_id,
        page,
        points,
        real_visuals,
        placeholders,
        citations,
        notes,
        concise=(concise_budget is not None),
        theme=theme,
    )

    preview_rel = f"previews/{page.page_id}_schematic.png"
    entry = PageDesignEntry(
        page_id=page.page_id,
        layout_id=layout_id,
        layout_name=layout_id,
        canvas_width_in=CANVAS_W_IN,
        canvas_height_in=CANVAS_H_IN,
        elements=elements,
        preview_path=preview_rel,
        notes="; ".join(notes),
    )
    render_schematic_preview(entry, run_dir, preview_rel)
    return entry


def _load_research_claims(run_dir: Path) -> list:
    rb_path = run_dir / "research_brief.json"
    if not rb_path.is_file():
        return []
    try:
        return list(load_contract(ResearchRecord, rb_path).claims)
    except Exception:
        return []


def compute_deck_eligible_layouts(
    slide_plan: SlidePlan,
    reg: AssetRegistry | None,
    uir: UserInputRequests | None,
    mapping: SlideAssetMapping | None,
    run_dir: Path,
    *,
    concise: bool = False,
) -> list[dict[str, float]]:
    """Compute per-slide eligible layouts using the exact Stage F inputs."""
    reg_by_id = {a.asset_id: a for a in reg.assets} if reg else {}
    uir_by_id = {r.request_id: r for r in uir.requests} if uir else {}
    empty_map = SlideAssetMapping(run_id=slide_plan.run_id, mappings=[], unused_assets=[])
    eff_map = mapping or empty_map
    claims = _load_research_claims(run_dir)
    n = len(slide_plan.pages)
    out: list[dict[str, float]] = []
    for idx, p in enumerate(slide_plan.pages):
        rv, ph, cits = _build_page_items(p, eff_map, reg_by_id, uir_by_id, run_dir)
        out.append(
            eligible_layouts_for_page(
                p,
                idx,
                n,
                rv,
                ph,
                concise=concise,
                citations=cits,
                research_claims=claims,
            )
        )
    return out


def run_stage_f(
    production: ProductionPlan,
    slide_plan: SlidePlan,
    reg: AssetRegistry,
    uir: UserInputRequests | None,
    mapping: SlideAssetMapping,
    run_dir: Path,
    *,
    theme_id: str | None = None,
) -> PageDesign:
    run_dir = Path(run_dir)
    theme = get_theme(theme_id or DEFAULT_THEME_ID)
    reg_by_id = {a.asset_id: a for a in reg.assets}
    uir_by_id = {r.request_id: r for r in uir.requests} if uir else {}
    concise_budget = _concise_budget(production)
    claims = _load_research_claims(run_dir)

    per_page_items = [
        _build_page_items(p, mapping, reg_by_id, uir_by_id, run_dir) for p in slide_plan.pages
    ]
    real_visuals_all = [x[0] for x in per_page_items]
    placeholders_all = [x[1] for x in per_page_items]
    citations_all = [x[2] for x in per_page_items]

    layout_ids = select_deck_layouts(
        slide_plan.pages,
        real_visuals_all,
        placeholders_all,
        production,
        concise=(concise_budget is not None),
        page_citations=citations_all,
        research_claims=claims,
    )

    pages = [
        _design_single_page(
            p,
            layout_ids[idx],
            real_visuals_all[idx],
            placeholders_all[idx],
            citations_all[idx],
            run_dir,
            concise_budget,
            theme=theme,
        )
        for idx, p in enumerate(slide_plan.pages)
    ]

    design = PageDesign(
        schema_version=SCHEMA_VERSION,
        run_id=production.run_id,
        theme_id=theme.theme_id,
        pages=pages,
    )
    problems = validate_page_design(design, slide_plan, production, mapping, reg, uir, run_dir)
    if problems:
        raise LayoutRuleError("page design validation failed:\n- " + "\n- ".join(problems))
    write_contract(design, run_dir / ARTIFACT)
    return design


__all__ = [
    "ARTIFACT",
    "CANONICAL_LAYOUT_IDS",
    "GRID_GUTTER",
    "GRID_MARGIN_X",
    "GRID_USABLE_W",
    "LEGACY_LAYOUT_ID",
    "MAX_PLACEHOLDER_AREA_RATIO",
    "SPLIT_TEXT_W",
    "SPLIT_VIS_W",
    "LayoutRuleError",
    "run_stage_f",
]
