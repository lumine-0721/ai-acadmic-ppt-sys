"""Canonical diverse slide layout registry, semantic selector, and geometry builders (Stage F).

Fuses layout taxonomy and structural patterns from verified open-source projects (see
`v01/docs/stage-f-layout-diversity.md` and `v01/NOTICE.md`):
  - slidevjs/slidev (MIT): `cover`, `section`, `fact` (`big-number`), `quote`, `image-left`,
    `image-right`, `two-cols` (`comparison`), `image` (`full-bleed`).
  - presenton/presenton (Apache-2.0): named layout registry and primary-slot vs utility-tray separation.
  - icip-cas/PPTAgent (MIT): content-to-layout matching rules (deterministic Python scorer, no runtime copied).
  - gitbrent/PptxGenJS (MIT): per-layout visual contrast & structural hierarchy ("one page, one structure").
  - hugohe3/ppt-master (MIT): calibrated CJK/Latin `slot_capacity` text fitting.

Strict product & QA invariants maintained by every layout:
  1. Every visible non-placeholder, non-citation text line comes verbatim from `page.title` or `page.key_points` (B2).
  2. Placeholder elements (`PLACEHOLDER_U###`) are ALWAYS rendered as compact bottom utility strips
     (<= 15% of slide content area, strictly smaller than the primary content slot) — NEVER the hero visual.
  3. Real visuals enter the primary hero slot; attribution captions anchor directly beneath the image slot,
     and reference citations anchor directly below the content block (never floating in empty margins).
  4. Zero `card_*` shapes; only rectangle/rounded_rectangle structural shapes (B9/B6 safe);
     strict z-order (`bg_*` at z=0, `accent_*` at z=1, foreground text/visuals at z=2+).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Sequence

from pptv01.contracts import (
    CANVAS_H_IN,
    CANVAS_W_IN,
    DesignElement,
    ElementKind,
    ProductionPlan,
    SlidePage,
)
from pptv01.theme import ACADEMIC_DEFAULT_THEME, ThemeTokens

from .capacity import (
    LAYOUT_CAPACITY_SPECS,
    LayoutCapacitySpec,
    estimate_slot_capacity,
    estimate_text_height_in,
    fit_text_to_box,
    get_layout_capacity,
    wrap_mixed_text,
)
from .validate import MIN_FONT_PT

# ------------------------------------------------------------------ Canonical layout catalog
CANONICAL_LAYOUT_IDS: tuple[str, ...] = (
    "cover",
    "section",
    "comparison",
    "big-number",
    "timeline",
    "image-left",
    "image-right",
    "quote",
    "full-bleed",
    "needs-input-list",
)

# Aliases accepted by gates/compilers and normalized to canonical IDs
LAYOUT_ALIASES: dict[str, str] = {
    "two-cols": "comparison",
    "two-cols-header": "comparison",
    "fact": "big-number",
    "statement": "quote",
    "image": "full-bleed",
    "full": "full-bleed",
    # Registered legacy layout family ("top pale-blue header bar + left bullets + right big box")
    "header-bullets-box": "header-bullets-box",
    "split_text_left": "header-bullets-box",
    "split_visual_left": "header-bullets-box",
    "top_points_multi_visual": "header-bullets-box",
    "clean_text_focus": "header-bullets-box",
}

LEGACY_LAYOUT_ID = "header-bullets-box"

TEXT_CAPABLE_LAYOUTS: tuple[str, ...] = (
    "cover",
    "section",
    "quote",
    "timeline",
    "comparison",
    "big-number",
    "needs-input-list",
)

IMAGE_REQUIRING_LAYOUTS: frozenset[str] = frozenset({"image-left", "image-right", "full-bleed"})

# ------------------------------------------------------------------ Palette defaults resolved from theme tokens
C_NAVY_DEEP = ACADEMIC_DEFAULT_THEME.primary_deep
C_NAVY = ACADEMIC_DEFAULT_THEME.primary
C_ACCENT = ACADEMIC_DEFAULT_THEME.accent
C_SKY = ACADEMIC_DEFAULT_THEME.accent_light
C_TEAL = ACADEMIC_DEFAULT_THEME.teal
C_TEXT = ACADEMIC_DEFAULT_THEME.background_text
C_TEXT_SOFT = ACADEMIC_DEFAULT_THEME.text_secondary
C_MUTED = ACADEMIC_DEFAULT_THEME.text_muted
C_WHITE = ACADEMIC_DEFAULT_THEME.primary_text
C_ICE = ACADEMIC_DEFAULT_THEME.text_on_dark_soft
C_SLATE_LIGHT = ACADEMIC_DEFAULT_THEME.surface_light
C_WARM_BG = ACADEMIC_DEFAULT_THEME.surface_warm
C_FACT_BG = ACADEMIC_DEFAULT_THEME.surface_fact
C_BORDER = ACADEMIC_DEFAULT_THEME.stroke
C_PH_FILL = ACADEMIC_DEFAULT_THEME.placeholder_fill
C_PH_LINE = ACADEMIC_DEFAULT_THEME.placeholder_line
C_PH_TEXT = ACADEMIC_DEFAULT_THEME.placeholder_text
DEFAULT_FONT = ACADEMIC_DEFAULT_THEME.font_family

# Standard 16:9 slide grid (13.333 x 7.5 in, 0.65 in side margins -> 12.03 in usable width)
GRID_MARGIN_X = 0.65
GRID_USABLE_W = 12.03
GRID_RIGHT_X = round(GRID_MARGIN_X + GRID_USABLE_W, 3)  # 12.68
GRID_GUTTER = 0.43
SPLIT_VIS_W = 6.00
SPLIT_TEXT_W = round(GRID_USABLE_W - SPLIT_VIS_W - GRID_GUTTER, 3)  # 5.60 in
CROP_FILL_MIN_AR = 0.75
CROP_FILL_MAX_AR = 2.40

MAX_PLACEHOLDER_AREA_RATIO = 0.15


def normalize_layout_id(raw: str | None) -> str:
    val = (raw or "").strip().lower()
    if not val:
        return ""
    if val in LAYOUT_ALIASES:
        return LAYOUT_ALIASES[val]
    return val


def slide_content_area_in2(canvas_w_in: float = CANVAS_W_IN, canvas_h_in: float = CANVAS_H_IN) -> float:
    """Usable slide content area in sq in (shared by Stage F, Stage H QA, and hard gates)."""
    usable_w = max(1.0, float(canvas_w_in) - 2.0 * GRID_MARGIN_X)
    usable_h = max(1.0, float(canvas_h_in) - 1.50)
    return usable_w * usable_h


@dataclass
class SlotItem:
    slot_type: str  # "image" | "chart" | "table" | "diagram_json" | "placeholder"
    asset_id: str | None
    request_id: str | None
    asset_path: str | None
    label: str
    width_px: int | None = None
    height_px: int | None = None
    attribution: str = ""

    @property
    def aspect_ratio(self) -> float | None:
        if self.width_px and self.height_px and self.width_px > 0 and self.height_px > 0:
            return self.width_px / self.height_px
        return None


# ------------------------------------------------------------------ Semantic layout selector & eligibility
# Ported/adapted from Presenton generate_presentation_structure.py (Apache-2.0),
# SlideSpeak layout-fit-model.md (MIT), and PPTAgent v0.2.0 layout_selector.yaml (MIT).
_TIMELINE_RE = re.compile(
    r"流程|步骤|阶段|演变|历史|发展历程|时间线|演进|过程|周期|推进|训练|"
    r"workflow|process|timeline|phase|stage|step|history",
    re.I,
)
_STEP_MARKER_RE = re.compile(
    r"^(?:\d+[.)、]|第[一二三四五六七八九十\d]+[步阶条项]|步骤|阶段|首先|其次|最后|step\s*\d+)",
    re.I,
)
_YEAR_TOKEN_RE = re.compile(r"\b(?:1[5-9]\d{2}|20\d{2})\b")
_NUMBER_FACT_RE = re.compile(r"(?:^|[^\w])(\d+(?:\.\d+)?)\s*(?:[%％℃°]|倍|万|亿|百万|千|元|年|个月|天|小时|m²|km²|px|ms|gb|tb|kg|mw|gw|kw)?", re.I)
_SOURCE_REF_INLINE_RE = re.compile(r"\[[RA]\d{3}\]|DOI:\S+", re.I)
_COMPARISON_RE = re.compile(
    r"对比|比较|差异|优劣|局限|做不到|定位|竞品|两类|两种|挑战|风险|vs\.?|versus|compare|contrast|limit|challenge|pros|cons|trade.?off",
    re.I,
)
_FACT_RE = re.compile(
    r"\d+\s*[%％℃°]|数据|统计|成本|收入|收益|规模|比例|指标|核心|事实|趋势|market|data|stat|metric|cost|revenue|fact|number|trend",
    re.I,
)
_QUOTE_RE = re.compile(
    r"看待|展望|总结|结论|思考|意义|价值|启示|未来|愿景|建议|是什么|summary|conclusion|outlook|takeaway|vision|what is|philosophy",
    re.I,
)
_SECTION_RE = re.compile(
    r"概述|背景|客群|是谁|引言|定义|框架|结构|目录|主题|overview|section|audience|who\b|intro|framework",
    re.I,
)
_NEEDS_INPUT_RE = re.compile(r"待补充|待确认|补充清单|附录|appendix|needs.?input|missing.?data", re.I)


def has_sourced_numeric_fact(
    page: SlidePage,
    citations: Sequence[str] = (),
    real_visuals: Sequence[SlotItem] = (),
    research_claims: Sequence[Any] = (),
) -> bool:
    """Return True only when the slide has a chart/table OR a numeric fact backed by source_refs/citations."""
    if any(v.slot_type in ("chart", "table") for v in real_visuals):
        return True

    pts = _format_points_lines(page.key_points)
    numeric_pts: list[str] = []
    for p in pts:
        # Ignore bare leading item numbering like "1. " when checking for numeric data
        body = re.sub(r"^\d+[.)、]\s*", "", p).strip()
        if re.search(r"\d", body):
            numeric_pts.append(body)
    if not numeric_pts:
        return False

    # Backed by acquired reference citation on this page or explicit inline [R###]/[A###]/DOI marker
    if citations or any(_SOURCE_REF_INLINE_RE.search(p) for p in numeric_pts):
        return True

    # Or backed by a ResearchBrief claim that carries verified source_refs and shares numbers/terms
    for claim in research_claims:
        refs = getattr(claim, "source_refs", None) if not isinstance(claim, dict) else claim.get("source_refs")
        if not refs:
            continue
        ctext = (getattr(claim, "claim", None) if not isinstance(claim, dict) else claim.get("claim")) or ""
        claim_nums = set(re.findall(r"\d+(?:\.\d+)?", ctext))
        for np_txt in numeric_pts:
            pt_nums = set(re.findall(r"\d+(?:\.\d+)?", np_txt))
            if claim_nums and (claim_nums & pt_nums):
                return True
    return False


def is_comparison_eligible(page: SlidePage, real_visuals: Sequence[SlotItem] = ()) -> bool:
    """Comparison requires >= 2 real visuals OR 2 sides with >= 2 points per side (>= 4 points)."""
    if len(real_visuals) >= 2:
        return True
    pts = _format_points_lines(page.key_points)
    return len(pts) >= 4


def is_timeline_eligible(page: SlidePage, real_visuals: Sequence[SlotItem] = ()) -> bool:
    """Timeline requires a bound diagram OR >= 3 points showing explicit ordered steps or years."""
    if any(v.slot_type == "diagram_json" for v in real_visuals):
        return True
    pts = _format_points_lines(page.key_points)
    if len(pts) < 3:
        return False
    pts_text = " ".join(pts)
    year_hits = len(_YEAR_TOKEN_RE.findall(f"{page.title} {pts_text}"))
    if year_hits >= 2:
        return True
    step_markers = sum(1 for p in pts if _STEP_MARKER_RE.search(p))
    if step_markers >= 2:
        return True
    content_head = f"{page.title} {page.goal} {pts_text}"
    return bool(_TIMELINE_RE.search(content_head))


def _score_layouts_for_page(
    page: SlidePage,
    idx: int,
    n_pages: int,
    real_visuals: Sequence[SlotItem],
    placeholders: Sequence[SlotItem],
    *,
    concise: bool = False,
    citations: Sequence[str] = (),
    research_claims: Sequence[Any] = (),
) -> dict[str, float]:
    """Score every CONTENT-ELIGIBLE canonical layout for a single page (never forces mismatched layouts)."""
    text_blob = f"{page.title} {page.goal} {' '.join(page.key_points)} {page.visual_intent}"
    n_vis = len(real_visuals)
    has_img = any(v.slot_type == "image" for v in real_visuals)
    first_img = next((v for v in real_visuals if v.slot_type == "image"), None)
    img_ar = first_img.aspect_ratio if first_img else None
    wide_landscape_img = bool(first_img and (img_ar is None or img_ar >= 1.45))
    portrait_img = bool(first_img and img_ar is not None and img_ar < 0.85)

    allow_comparison = is_comparison_eligible(page, real_visuals)
    allow_big_number = has_sourced_numeric_fact(page, citations, real_visuals, research_claims)
    allow_timeline = is_timeline_eligible(page, real_visuals)

    scores: dict[str, float] = {}

    # 1. Cover: best for Slide 1 of multi-page decks (>= 3 pages) or clean 2-slide non-visual opener
    if idx == 0 and n_pages >= 3:
        scores["cover"] = 10.0 + (1.0 if wide_landscape_img else 0.0)
    elif idx == 0 and n_pages <= 2 and n_vis == 0:
        scores["cover"] = 8.0

    # 2. Image-driven layouts (only eligible when page actually has real visuals!)
    if n_vis >= 1:
        if n_vis == 1:
            scores["image-right"] = 7.5 + (0.6 if idx % 2 == 0 else 0.0)
            scores["image-left"] = 7.5 + (0.6 if idx % 2 == 1 else 0.0)
            if wide_landscape_img and n_pages >= 3:
                scores["full-bleed"] = 8.4 if idx > 0 else 6.8
            if allow_big_number:
                scores["big-number"] = 8.2
            if allow_timeline:
                scores["timeline"] = 7.8
        else:
            if allow_comparison:
                scores["comparison"] = 8.8
            scores["image-right"] = 7.0
            scores["image-left"] = 7.0

    # 3. Dedicated appendix / needs-input-list
    if _NEEDS_INPUT_RE.search(page.title) and n_vis == 0 and placeholders:
        scores["needs-input-list"] = 11.0

    # 4. Content-driven editorial layouts gated strictly by content-shape eligibility
    if n_vis <= 1 and not has_img:
        scores.setdefault("section", 5.2 + (2.4 if _SECTION_RE.search(text_blob) else 0.0) + (0.8 if idx == 1 else 0.0))
        if allow_timeline:
            scores.setdefault("timeline", 6.2 + (2.2 if _TIMELINE_RE.search(text_blob) else 0.0))
        if allow_comparison:
            scores.setdefault("comparison", 6.2 + (2.2 if _COMPARISON_RE.search(text_blob) else 0.0))
        if allow_big_number:
            scores.setdefault("big-number", 6.4 + (2.2 if _FACT_RE.search(text_blob) else 0.0))
        scores.setdefault(
            "quote",
            5.0
            + (3.0 if _QUOTE_RE.search(text_blob) else 0.0)
            + (1.2 if idx == n_pages - 1 and n_pages >= 3 else 0.0)
            + (1.0 if concise else 0.0),
        )
    elif n_vis == 1 and has_img and not portrait_img:
        scores.setdefault("section", 4.5 + (1.5 if _SECTION_RE.search(text_blob) else 0.0))
        if allow_big_number:
            scores.setdefault("big-number", 4.6 + (1.5 if _FACT_RE.search(text_blob) else 0.0))
        if allow_comparison:
            scores.setdefault("comparison", 4.4 + (1.5 if _COMPARISON_RE.search(text_blob) else 0.0))
        scores.setdefault("quote", 3.5 + (1.5 if _QUOTE_RE.search(text_blob) else 0.0))
    elif n_vis == 1 and portrait_img:
        if allow_comparison:
            scores.setdefault("comparison", 4.4 + (1.2 if _COMPARISON_RE.search(text_blob) else 0.0))
        scores.setdefault("quote", 4.0 + (1.2 if _QUOTE_RE.search(text_blob) else 0.0))

    # Minimal universal safe fallback: only breathing/statement layouts (never force comparison/big-number/timeline)
    if not scores:
        scores = {"section": 5.0, "quote": 4.8}
    return scores


def eligible_layouts_for_page(
    page: SlidePage,
    idx: int,
    n_pages: int,
    real_visuals: Sequence[SlotItem],
    placeholders: Sequence[SlotItem],
    *,
    concise: bool = False,
    citations: Sequence[str] = (),
    research_claims: Sequence[Any] = (),
) -> dict[str, float]:
    """Public wrapper returning all eligible canonical layouts and their affinity scores for a page."""
    return _score_layouts_for_page(
        page,
        idx,
        n_pages,
        real_visuals,
        placeholders,
        concise=concise,
        citations=citations,
        research_claims=research_claims,
    )


def max_realizable_distinct_layouts(score_tables: Sequence[Mapping[str, float]]) -> int:
    """Compute the exact maximum number of distinct eligible layouts simultaneously assignable across slides."""
    # Maximum bipartite matching (slides -> unique layout_ids)
    match_to_slide: dict[str, int] = {}

    def _dfs(slide_idx: int, seen_lids: set[str]) -> bool:
        for lid in score_tables[slide_idx]:
            if lid in seen_lids:
                continue
            seen_lids.add(lid)
            if lid not in match_to_slide or _dfs(match_to_slide[lid], seen_lids):
                match_to_slide[lid] = slide_idx
                return True
        return False

    for i in range(len(score_tables)):
        _dfs(i, set())
    return len(match_to_slide)


def select_deck_layouts(
    pages: Sequence[SlidePage],
    page_real_visuals: Sequence[Sequence[SlotItem]],
    page_placeholders: Sequence[Sequence[SlotItem]],
    production: ProductionPlan,
    *,
    concise: bool = False,
    page_citations: Sequence[Sequence[str]] | None = None,
    research_claims: Sequence[Any] = (),
) -> list[str]:
    """Deterministically assign canonical `layout_id`s across all slides in a deck.

    Enforces:
      - Only content-eligible layouts are considered in Pass 1, Pass 2, and Pass 3.
      - No adjacent duplicate `layout_id` whenever >= 2 eligible layouts exist on a slide.
      - Maximize distinct layout families up to ``min(5, len(pages), realizable_eligible)``.
      - Never selects the legacy `header-bullets-box` layout.
      - Image-requiring layouts (`image-left`, `image-right`, `full-bleed`) are only
        assigned to slides that have at least 1 real visual.
    """
    n = len(pages)
    if n == 0:
        return []

    score_tables = [
        _score_layouts_for_page(
            pages[i],
            i,
            n,
            page_real_visuals[i],
            page_placeholders[i],
            concise=concise,
            citations=page_citations[i] if page_citations is not None and i < len(page_citations) else (),
            research_claims=research_claims,
        )
        for i in range(n)
    ]

    # Pass 1: greedy selection with no-adjacent-repeat and diversity bonus for unused eligible layouts
    chosen: list[str] = []
    used_counts: dict[str, int] = {}
    for i in range(n):
        prev = chosen[i - 1] if i > 0 else None
        best_lid = ""
        best_val = -1e9
        for lid, base_score in sorted(score_tables[i].items()):
            if lid == prev:
                continue
            unused_bonus = 2.6 if used_counts.get(lid, 0) == 0 else -1.5 * used_counts[lid]
            val = base_score + unused_bonus
            if val > best_val:
                best_val = val
                best_lid = lid
        if not best_lid:
            best_lid = max(score_tables[i].items(), key=lambda kv: kv[1])[0]
        chosen.append(best_lid)
        used_counts[best_lid] = used_counts.get(best_lid, 0) + 1

    # Pass 2: upgrade duplicated slides using ONLY unused ELIGIBLE layouts (no forced fallback!)
    target_distinct = min(5 if n >= 4 else min(2, n), max_realizable_distinct_layouts(score_tables))
    guard = 0
    while len(set(chosen)) < target_distinct and guard < 20:
        guard += 1
        improved = False
        best_swap: tuple[float, int, str] | None = None
        for i in range(n):
            if chosen.count(chosen[i]) <= 1:
                continue
            prev = chosen[i - 1] if i > 0 else None
            nxt = chosen[i + 1] if i + 1 < n else None
            for lid, base_score in sorted(score_tables[i].items()):
                if lid in chosen or lid == prev or lid == nxt:
                    continue
                if best_swap is None or base_score > best_swap[0]:
                    best_swap = (base_score, i, lid)
        if best_swap is not None:
            _, idx_swap, new_lid = best_swap
            chosen[idx_swap] = new_lid
            improved = True
        if not improved:
            break

    # Pass 3: remove adjacent duplicates using ONLY eligible layouts in score_tables[i]
    for i in range(1, n):
        if chosen[i] == chosen[i - 1]:
            nxt = chosen[i + 1] if i + 1 < n else None
            for lid in sorted(score_tables[i], key=lambda k: score_tables[i][k], reverse=True):
                if lid == "full-bleed" and len(page_real_visuals[i]) != 1:
                    continue
                if lid == "needs-input-list" and page_real_visuals[i]:
                    continue
                if lid != chosen[i - 1] and lid != nxt:
                    chosen[i] = lid
                    break

    return chosen


# ------------------------------------------------------------------ Shared low-level element helpers
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
    font: str = DEFAULT_FONT,
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
        font=font,
        font_size_pt=fit.font_size_pt,
        bold=bold,
        color_hex=color_hex,
        role=role,
    )


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


def _require_placeholder_request_id(item: SlotItem) -> str:
    req_id = (item.request_id or "").strip()
    if not req_id:
        raise ValueError(
            f"placeholder SlotItem ({item.label!r}) missing request_id; never fabricate U001/PLACEHOLDER_None"
        )
    return req_id


def _format_compact_placeholder_text(item: SlotItem, box_w: float, box_h: float, font_pt: float) -> tuple[str, float]:
    """Fit ``待补充：<need>（U###）`` cleanly inside a compact strip without overflowing."""
    req_id = _require_placeholder_request_id(item)
    full = item.label.strip()
    floor = MIN_FONT_PT[ElementKind.placeholder]
    fit = fit_text_to_box(full, box_w, box_h, font_pt, min_font_pt=floor)
    if not fit.still_overflows:
        return fit.text, fit.font_size_pt

    # Condense the middle description while strictly preserving "待补充：" and "（U###）"
    suffix = f"（{req_id}）"
    prefix = "待补充："
    inner = full
    if inner.startswith(prefix):
        inner = inner[len(prefix):]
    if inner.endswith(suffix):
        inner = inner[: -len(suffix)]
    cap = estimate_slot_capacity(box_w, box_h, floor)
    max_chars = max(8, int(cap.cjk_chars_per_line * max(1, cap.max_lines)) - len(prefix) - len(suffix) - 2)
    short = inner[:max_chars].rstrip("，,；;。 ")
    candidate = f"{prefix}{short}…{suffix}" if len(short) < len(inner) else f"{prefix}{short}{suffix}"
    # Double-check line wrap at floor font size
    while len(wrap_mixed_text(candidate, box_w, floor)) > cap.max_lines and len(short) > 6:
        short = short[:-2].rstrip("，,；;。 ")
        candidate = f"{prefix}{short}…{suffix}"
    return candidate, floor


def _place_compact_placeholder_tray(
    elements: list[DesignElement],
    placeholders: Sequence[SlotItem],
    *,
    x: float = GRID_MARGIN_X,
    y: float = 6.56,
    max_w: float = GRID_USABLE_W,
    tray_h: float = 0.60,
    page_notes: list[str],
    dark_theme: bool = False,
    theme: ThemeTokens = ACADEMIC_DEFAULT_THEME,
) -> None:
    """Place all missing U### requests as compact dashed corner badges/pills (<= 15% content area total, never hero)."""
    n = len(placeholders)
    if n == 0:
        return
    content_area = slide_content_area_in2()
    max_each_area = content_area * 0.125  # safely under the 15% hard cap
    ph_fill = theme.dark_placeholder_fill if dark_theme else theme.placeholder_fill
    ph_line = theme.dark_placeholder_line if dark_theme else theme.placeholder_line
    ph_text = theme.dark_placeholder_text if dark_theme else theme.placeholder_text

    if n == 1:
        item = placeholders[0]
        req_id = _require_placeholder_request_id(item)
        # Restrained bottom-right corner badge (4.90 x 0.56 = 2.74 sq in ~ 3.8% of content area)
        # snapped cleanly to right edge (x + max_w = 12.68) so it stays non-dominant and drift-free.
        ph = round(min(tray_h, 0.56), 3)
        pw = round(min(max_w, 4.90, max_each_area / max(ph, 0.45)), 3)
        px = round(x + max_w - pw, 3)
        txt, pt = _format_compact_placeholder_text(item, pw, ph, 11.0)
        if txt != item.label:
            page_notes.append(f"PLACEHOLDER_{req_id}: compact tray label shortened (full need in notes)")
        elements.append(
            DesignElement(
                element_id=f"PLACEHOLDER_{req_id}",
                kind=ElementKind.placeholder,
                x_in=px,
                y_in=round(y, 3),
                w_in=pw,
                h_in=ph,
                z_index=3,
                align="left",
                text=txt,
                font=theme.font_family,
                font_size_pt=pt,
                color_hex=ph_text,
                fill_hex=ph_fill,
                line_hex=ph_line,
                role="compact_placeholder",
                asset_id=item.asset_id,
                request_id=req_id,
            )
        )
        return

    # 2..N compact pills side-by-side in the bottom tray (total area <= 13% of content area)
    gap = 0.22
    col_w = round((max_w - gap * (n - 1)) / n, 3)
    # Enforce per-box and total area strictly below 15% of content area
    ph = round(min(tray_h, (content_area * 0.13) / max(max_w, 1.0)), 3)
    ph = max(0.54, ph)
    for i, item in enumerate(placeholders):
        req_id = _require_placeholder_request_id(item)
        px = round(x + i * (col_w + gap), 3)
        cw = round(x + max_w - px, 3) if i == n - 1 else col_w
        txt, pt = _format_compact_placeholder_text(item, cw, ph, 11.0)
        if txt != item.label:
            page_notes.append(f"PLACEHOLDER_{req_id}: compact tray label shortened (full need in notes)")
        elements.append(
            DesignElement(
                element_id=f"PLACEHOLDER_{req_id}",
                kind=ElementKind.placeholder,
                x_in=px,
                y_in=round(y, 3),
                w_in=cw,
                h_in=ph,
                z_index=3,
                align="left",
                text=txt,
                font=theme.font_family,
                font_size_pt=pt,
                color_hex=ph_text,
                fill_hex=ph_fill,
                line_hex=ph_line,
                role="compact_placeholder",
                asset_id=item.asset_id,
                request_id=req_id,
            )
        )


def _design_real_visual(
    item: SlotItem,
    idx: int,
    x: float,
    y: float,
    w: float,
    h: float,
    page_notes: list[str],
    *,
    caption_color: str | None = None,
    suppress_caption: bool = False,
    theme: ThemeTokens = ACADEMIC_DEFAULT_THEME,
) -> list[DesignElement]:
    out: list[DesignElement] = []
    cap_color = caption_color if caption_color is not None else theme.caption_on_light
    if item.slot_type == "image":
        cap_text = "" if suppress_caption else item.attribution.strip()
        cap_h = 0.0
        if cap_text:
            cap_fit = fit_text_to_box(cap_text, w, 0.56, 9.5, min_font_pt=MIN_FONT_PT[ElementKind.caption])
            if cap_fit.still_overflows:
                floor_pt = MIN_FONT_PT[ElementKind.caption]
                need_h = estimate_text_height_in(wrap_mixed_text(cap_text, w, floor_pt), floor_pt) + 0.14
                cap_h = round(min(1.15, max(0.76, need_h)), 3)
            elif len(cap_fit.lines) > 1:
                cap_h = 0.54
            else:
                cap_h = 0.36
        if cap_h > 0 and h >= 1.55:
            cap_h = round(min(cap_h, max(0.32, h - 1.16)), 3)
        elif cap_h > 0:
            cap_h = round(min(cap_h, max(0.26, h * 0.28)), 3)
        img_h = max(0.80, round(h - cap_h - (0.06 if cap_h else 0.0), 3))
        dims = (item.width_px, item.height_px) if (item.width_px and item.height_px) else None
        img_ar = item.aspect_ratio
        if img_ar is not None and (img_ar < CROP_FILL_MIN_AR or img_ar > CROP_FILL_MAX_AR):
            nx, ny, nw, nh, adapted = _fit_rect_aspect(x, y, w, img_h, dims)
            if adapted and dims:
                page_notes.append(f"vis_{idx}: adapted image box to aspect ratio {dims[0]}x{dims[1]}")
        else:
            nx, ny, nw, nh = round(x, 3), round(y, 3), round(w, 3), round(img_h, 3)
            if dims:
                page_notes.append(f"vis_{idx}: uniform crop_fill slot {nw:.2f}x{nh:.2f}in for {dims[0]}x{dims[1]}")
        out.append(
            DesignElement(
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
            )
        )
        if cap_h > 0:
            cap_y = round(ny + nh + 0.06, 3)
            out.append(
                _make_fitted_text(
                    f"cap_{idx}",
                    ElementKind.caption,
                    x,
                    cap_y,
                    w,
                    cap_h,
                    cap_text,
                    9.5,
                    z_index=2,
                    color_hex=cap_color,
                    font=theme.font_family,
                    role="attribution_caption",
                    page_notes=page_notes,
                )
            )
        return out

    if item.slot_type in ("chart", "table"):
        kind = ElementKind.chart if item.slot_type == "chart" else ElementKind.table
        out.append(
            DesignElement(
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
            )
        )
        return out

    # diagram_json rendered as structured native diagram container
    out.append(
        DesignElement(
            element_id=f"diag_{idx}",
            kind=ElementKind.shape,
            x_in=round(x, 3),
            y_in=round(y, 3),
            w_in=round(w, 3),
            h_in=round(h, 3),
            z_index=1,
            fill_hex=theme.surface_light,
            line_hex=theme.accent,
            role="diagram_json",
            asset_id=item.asset_id,
            request_id=item.request_id,
            asset_path=item.asset_path,
        )
    )
    return out


def _place_real_visuals_group(
    elements: list[DesignElement],
    real_visuals: Sequence[SlotItem],
    x: float,
    y: float,
    w: float,
    h: float,
    page_notes: list[str],
    *,
    caption_color: str | None = None,
    start_idx: int = 1,
    theme: ThemeTokens = ACADEMIC_DEFAULT_THEME,
) -> None:
    """Place ALL real visuals inside ``(x, y, w, h)`` without silently dropping any."""
    n_vis = len(real_visuals)
    if n_vis == 0:
        return
    if n_vis == 1:
        elements.extend(
            _design_real_visual(
                real_visuals[0], start_idx, x, y, w, h, page_notes, caption_color=caption_color, theme=theme
            )
        )
        return
    if n_vis <= 3:
        if w >= h * 1.35:
            gap = 0.20
            cw = round((w - gap * (n_vis - 1)) / n_vis, 3)
            for i, item in enumerate(real_visuals):
                vx = round(x + i * (cw + gap), 3)
                vw = round(x + w - vx, 3) if i == n_vis - 1 else cw
                elements.extend(
                    _design_real_visual(
                        item, start_idx + i, vx, y, vw, h, page_notes, caption_color=caption_color, theme=theme
                    )
                )
        else:
            gap = 0.20
            ch = round((h - gap * (n_vis - 1)) / n_vis, 3)
            for i, item in enumerate(real_visuals):
                vy = round(y + i * (ch + gap), 3)
                vh = round(y + h - vy, 3) if i == n_vis - 1 else ch
                elements.extend(
                    _design_real_visual(
                        item, start_idx + i, x, vy, w, vh, page_notes, caption_color=caption_color, theme=theme
                    )
                )
        return

    cols = math.ceil(n_vis / 2)
    rows = 2
    gap_x = 0.20
    gap_y = 0.18
    cw = round((w - gap_x * (cols - 1)) / cols, 3)
    ch = round((h - gap_y * (rows - 1)) / rows, 3)
    for i, item in enumerate(real_visuals):
        r = i // cols
        c = i % cols
        vx = round(x + c * (cw + gap_x), 3)
        vy = round(y + r * (ch + gap_y), 3)
        vw = round(x + w - vx, 3) if c == cols - 1 else cw
        vh = round(y + h - vy, 3) if r == rows - 1 else ch
        elements.extend(
            _design_real_visual(
                item, start_idx + i, vx, vy, vw, vh, page_notes, caption_color=caption_color, theme=theme
            )
        )


def _place_anchored_citation(
    elements: list[DesignElement],
    citations: Sequence[str],
    *,
    x: float,
    y: float,
    w: float,
    max_bottom: float,
    color_hex: str | None = None,
    page_notes: list[str],
    theme: ThemeTokens = ACADEMIC_DEFAULT_THEME,
) -> None:
    """Anchor reference citations tightly below the content block (never floating in empty whitespace)."""
    if not citations:
        return
    cite_color = color_hex if color_hex is not None else theme.citation_on_light
    cite_text = " | ".join(citations)
    avail_h = round(max_bottom - y, 3)
    if avail_h < 0.34:
        y = round(max_bottom - 0.38, 3)
        avail_h = 0.38
    h = round(min(0.46, avail_h), 3)
    elements.append(
        _make_fitted_text(
            "footer",
            ElementKind.footer,
            x,
            y,
            w,
            h,
            cite_text,
            9.5,
            z_index=2,
            color_hex=cite_color,
            font=theme.font_family,
            role="anchored_citation",
            page_notes=page_notes,
        )
    )


def _place_content_fitted_bullets(
    elements: list[DesignElement],
    x: float,
    y: float,
    w: float,
    max_h: float,
    bullet_text: str,
    start_pt: float,
    notes: list[str],
    *,
    element_id: str = "bullets",
    color_hex: str | None = None,
    rail_color: str | None = None,
    with_accent_rail: bool = True,
    rail_id: str = "accent_rail",
    min_box_h: float = 0.80,
    theme: ThemeTokens = ACADEMIC_DEFAULT_THEME,
) -> float:
    """Place content-fitted bullets with an optional matching-height left accent rail."""
    txt_color = color_hex if color_hex is not None else theme.background_text
    r_color = rail_color if rail_color is not None else theme.accent
    rail_offset = 0.20 if with_accent_rail else 0.0
    txt_x = round(x + rail_offset, 3)
    txt_w = round(max(1.5, w - rail_offset), 3)
    floor = MIN_FONT_PT[ElementKind.bullet]

    fit = fit_text_to_box(bullet_text, txt_w, max_h, start_pt, min_font_pt=floor)
    fitted_h = estimate_text_height_in(fit.lines, fit.font_size_pt)
    box_h = round(min(max_h, max(min_box_h, fitted_h + 0.24)), 3)
    if box_h < max_h - 0.5:
        notes.append(f"{element_id}: shrunk text box height to {box_h:.2f}in (content-fitted, no empty card)")

    if with_accent_rail:
        elements.append(
            DesignElement(
                element_id=rail_id,
                kind=ElementKind.shape,
                x_in=round(x, 3),
                y_in=round(y, 3),
                w_in=0.06,
                h_in=box_h,
                z_index=1,
                fill_hex=r_color,
                line_hex=r_color,
                role="accent_rail",
            )
        )

    if fit.adjusted:
        notes.append(f"{element_id}: {fit.note}")
    elements.append(
        DesignElement(
            element_id=element_id,
            kind=ElementKind.bullet,
            x_in=txt_x,
            y_in=round(y, 3),
            w_in=txt_w,
            h_in=box_h,
            z_index=2,
            align="left",
            text=fit.text,
            font=theme.font_family,
            font_size_pt=fit.font_size_pt,
            bold=False,
            color_hex=txt_color,
            role="key_points",
        )
    )
    return box_h


def _format_points_lines(points: Sequence[str]) -> list[str]:
    return [p.strip() for p in points if p.strip()]


# ------------------------------------------------------------------ Per-layout builders
def build_layout_elements(
    layout_id: str,
    page: SlidePage,
    bullet_points: Sequence[str],
    real_visuals: Sequence[SlotItem],
    placeholders: Sequence[SlotItem],
    citations: Sequence[str],
    notes: list[str],
    *,
    concise: bool = False,
    theme: ThemeTokens = ACADEMIC_DEFAULT_THEME,
    allow_sparse: bool = True,
) -> list[DesignElement]:
    """Build all DesignElements for the requested canonical `layout_id`."""
    lid = normalize_layout_id(layout_id) or "section"
    pts = _format_points_lines(bullet_points)
    if not pts:
        pts = _format_points_lines(page.key_points)
    bullet_text = "\n".join(f"• {p}" for p in pts)
    has_ph = len(placeholders) > 0
    tray_y = 6.56
    tray_h = 0.60
    content_bottom = 6.32 if has_ph else 7.04
    base_pt = 20.0 if concise else 18.0

    elements: list[DesignElement] = []

    # ================================================================ 1) COVER (deep navy hero)
    if lid == "cover":
        elements.append(
            DesignElement(
                element_id="bg_canvas",
                kind=ElementKind.shape,
                x_in=0.0,
                y_in=0.0,
                w_in=CANVAS_W_IN,
                h_in=CANVAS_H_IN,
                z_index=0,
                fill_hex=theme.primary_deep,
                line_hex=theme.primary_deep,
                role="canvas_bg",
            )
        )
        elements.append(
            DesignElement(
                element_id="accent_bar",
                kind=ElementKind.shape,
                x_in=0.0,
                y_in=0.0,
                w_in=0.18,
                h_in=CANVAS_H_IN,
                z_index=1,
                fill_hex=theme.accent_light,
                line_hex=theme.accent_light,
                role="left_accent",
            )
        )
        if real_visuals:
            # Split dark hero cover: big white title + points on left, hero photo on right
            left_w = 5.65
            elements.append(
                _make_fitted_text(
                    "title",
                    ElementKind.title,
                    GRID_MARGIN_X,
                    0.85,
                    left_w,
                    1.65,
                    page.title,
                    36.0,
                    z_index=2,
                    bold=True,
                    color_hex=theme.primary_text,
                    font=theme.font_family,
                    role="cover_title",
                    page_notes=notes,
                )
            )
            elements.append(
                DesignElement(
                    element_id="accent_divider",
                    kind=ElementKind.shape,
                    x_in=GRID_MARGIN_X,
                    y_in=2.66,
                    w_in=1.60,
                    h_in=0.07,
                    z_index=1,
                    fill_hex=theme.accent_light,
                    line_hex=theme.accent_light,
                    role="cover_divider",
                )
            )
            bul_y = 2.95
            bul_max_h = round(content_bottom - bul_y - (0.50 if citations else 0.0), 3)
            bh = _place_content_fitted_bullets(
                elements,
                GRID_MARGIN_X,
                bul_y,
                left_w,
                bul_max_h,
                bullet_text,
                base_pt,
                notes,
                color_hex=theme.text_on_dark_soft,
                rail_color=theme.accent_light,
                with_accent_rail=False,
                min_box_h=1.40,
                theme=theme,
            )
            if citations:
                _place_anchored_citation(
                    elements,
                    citations,
                    x=GRID_MARGIN_X,
                    y=round(bul_y + bh + 0.14, 3),
                    w=left_w,
                    max_bottom=content_bottom,
                    color_hex=theme.citation_on_dark,
                    page_notes=notes,
                    theme=theme,
                )
            vis_x = round(GRID_MARGIN_X + left_w + 0.38, 3)
            vis_w = round(GRID_RIGHT_X - vis_x, 3)
            vis_h = round(content_bottom - 0.65, 3)
            _place_real_visuals_group(
                elements,
                real_visuals,
                vis_x,
                0.65,
                vis_w,
                vis_h,
                notes,
                caption_color=theme.caption_on_dark,
                theme=theme,
            )
        else:
            # Full-width dark typographic cover (ideal for sparse decks like T36 slide 1!)
            elements.append(
                _make_fitted_text(
                    "title",
                    ElementKind.title,
                    GRID_MARGIN_X,
                    1.15,
                    GRID_USABLE_W,
                    1.60,
                    page.title,
                    42.0,
                    z_index=2,
                    bold=True,
                    color_hex=theme.primary_text,
                    font=theme.font_family,
                    role="cover_title",
                    page_notes=notes,
                )
            )
            elements.append(
                DesignElement(
                    element_id="accent_divider",
                    kind=ElementKind.shape,
                    x_in=GRID_MARGIN_X,
                    y_in=2.92,
                    w_in=2.20,
                    h_in=0.08,
                    z_index=1,
                    fill_hex=theme.accent_light,
                    line_hex=theme.accent_light,
                    role="cover_divider",
                )
            )
            bul_y = 3.26
            bul_max_h = round(content_bottom - bul_y - (0.50 if citations else 0.0), 3)
            bh = _place_content_fitted_bullets(
                elements,
                GRID_MARGIN_X,
                bul_y,
                GRID_USABLE_W,
                bul_max_h,
                bullet_text,
                22.0 if concise else 20.0,
                notes,
                color_hex=theme.text_on_dark_soft,
                rail_color=theme.accent_light,
                with_accent_rail=False,
                min_box_h=1.50,
                theme=theme,
            )
            if citations:
                _place_anchored_citation(
                    elements,
                    citations,
                    x=GRID_MARGIN_X,
                    y=round(bul_y + bh + 0.16, 3),
                    w=GRID_USABLE_W,
                    max_bottom=content_bottom,
                    color_hex=theme.citation_on_dark,
                    page_notes=notes,
                    theme=theme,
                )
        if has_ph:
            _place_compact_placeholder_tray(
                elements, placeholders, y=tray_y, tray_h=tray_h, page_notes=notes, dark_theme=True, theme=theme
            )
        return elements

    # ================================================================ 2) SECTION (dark left column + bright right stage)
    if lid == "section":
        split_x = 4.95
        elements.append(
            DesignElement(
                element_id="bg_left_panel",
                kind=ElementKind.shape,
                x_in=0.0,
                y_in=0.0,
                w_in=split_x,
                h_in=CANVAS_H_IN,
                z_index=0,
                fill_hex=theme.primary,
                line_hex=theme.primary,
                role="section_bg_left",
            )
        )
        elements.append(
            DesignElement(
                element_id="bg_right_panel",
                kind=ElementKind.shape,
                x_in=split_x,
                y_in=0.0,
                w_in=round(CANVAS_W_IN - split_x, 3),
                h_in=CANVAS_H_IN,
                z_index=0,
                fill_hex=theme.surface_light,
                line_hex=theme.surface_light,
                role="section_bg_right",
            )
        )
        elements.append(
            DesignElement(
                element_id="accent_bar",
                kind=ElementKind.shape,
                x_in=split_x,
                y_in=0.0,
                w_in=0.08,
                h_in=CANVAS_H_IN,
                z_index=1,
                fill_hex=theme.accent,
                line_hex=theme.accent,
                role="section_stripe",
            )
        )
        elements.append(
            _make_fitted_text(
                "title",
                ElementKind.title,
                GRID_MARGIN_X,
                1.60,
                3.75,
                2.20,
                page.title,
                34.0,
                z_index=2,
                bold=True,
                color_hex=theme.primary_text,
                font=theme.font_family,
                role="section_title",
                page_notes=notes,
            )
        )
        elements.append(
            DesignElement(
                element_id="accent_divider",
                kind=ElementKind.shape,
                x_in=GRID_MARGIN_X,
                y_in=3.98,
                w_in=1.20,
                h_in=0.07,
                z_index=1,
                fill_hex=theme.accent_light,
                line_hex=theme.accent_light,
                role="section_divider",
            )
        )

        rx = 5.55
        rw = round(GRID_RIGHT_X - rx, 3)  # 7.13 in
        if real_visuals:
            bh = _place_content_fitted_bullets(
                elements, rx, 0.70, rw, 1.85, bullet_text, 17.0, notes, min_box_h=0.95, theme=theme
            )
            vis_y = round(0.70 + bh + 0.25, 3)
            vis_h = round(content_bottom - vis_y - (0.45 if citations else 0.0), 3)
            _place_real_visuals_group(elements, real_visuals, rx, vis_y, rw, vis_h, notes, theme=theme)
            if citations:
                _place_anchored_citation(
                    elements,
                    citations,
                    x=rx,
                    y=round(vis_y + vis_h + 0.08, 3),
                    w=rw,
                    max_bottom=content_bottom,
                    page_notes=notes,
                    theme=theme,
                )
        else:
            bul_y = 1.60
            bul_max_h = round(content_bottom - bul_y - (0.50 if citations else 0.0), 3)
            bh = _place_content_fitted_bullets(
                elements,
                rx,
                bul_y,
                rw,
                bul_max_h,
                bullet_text,
                22.0 if concise else 20.0,
                notes,
                min_box_h=2.00,
                theme=theme,
            )
            if citations:
                _place_anchored_citation(
                    elements,
                    citations,
                    x=rx,
                    y=round(bul_y + bh + 0.16, 3),
                    w=rw,
                    max_bottom=content_bottom,
                    page_notes=notes,
                    theme=theme,
                )
        if has_ph:
            if len(placeholders) <= 2:
                _place_compact_placeholder_tray(
                    elements, placeholders, x=rx, y=tray_y, max_w=rw, tray_h=tray_h, page_notes=notes, theme=theme
                )
            else:
                _place_compact_placeholder_tray(elements, placeholders, y=tray_y, tray_h=tray_h, page_notes=notes, theme=theme)
        return elements

    # ================================================================ 3) FULL-BLEED (edge-to-edge hero image + dark lower band)
    if lid == "full-bleed" and len(real_visuals) == 1 and real_visuals[0].slot_type == "image":
        band_y = 4.85
        band_h = round(CANVAS_H_IN - band_y, 3)  # 2.65 in
        img_item = real_visuals[0]
        # Edge-to-edge full-bleed hero image across the top 60% (zero margin, crop_fill)
        elements.append(
            DesignElement(
                element_id="vis_1",
                kind=ElementKind.image if img_item.slot_type == "image" else (
                    ElementKind.chart if img_item.slot_type == "chart" else ElementKind.table
                ),
                x_in=0.0,
                y_in=0.0,
                w_in=CANVAS_W_IN,
                h_in=band_y,
                z_index=2,
                role="full_bleed_hero",
                asset_id=img_item.asset_id,
                request_id=img_item.request_id,
                asset_path=img_item.asset_path,
            )
        )
        elements.append(
            DesignElement(
                element_id="bg_bleed_band",
                kind=ElementKind.shape,
                x_in=0.0,
                y_in=band_y,
                w_in=CANVAS_W_IN,
                h_in=band_h,
                z_index=0,
                fill_hex=theme.primary_deep,
                line_hex=theme.primary_deep,
                role="bleed_editorial_band",
            )
        )
        elements.append(
            DesignElement(
                element_id="accent_bar",
                kind=ElementKind.shape,
                x_in=0.0,
                y_in=band_y,
                w_in=CANVAS_W_IN,
                h_in=0.06,
                z_index=1,
                fill_hex=theme.accent_light,
                line_hex=theme.accent_light,
                role="bleed_accent",
            )
        )

        text_top = round(band_y + 0.20, 3)  # 4.70
        band_bottom = 6.46 if has_ph else 7.26
        left_w = 5.40
        right_x = round(GRID_MARGIN_X + left_w + 0.43, 3)
        right_w = round(GRID_RIGHT_X - right_x, 3)

        cap_text = img_item.attribution.strip()
        title_h = 1.05 if cap_text else round(band_bottom - text_top, 3)
        elements.append(
            _make_fitted_text(
                "title",
                ElementKind.title,
                GRID_MARGIN_X,
                text_top,
                left_w,
                title_h,
                page.title,
                28.0,
                z_index=2,
                bold=True,
                color_hex=theme.primary_text,
                font=theme.font_family,
                role="bleed_title",
                page_notes=notes,
            )
        )
        if cap_text:
            elements.append(
                _make_fitted_text(
                    "cap_1",
                    ElementKind.caption,
                    GRID_MARGIN_X,
                    round(text_top + title_h + 0.08, 3),
                    left_w,
                    round(max(0.42, band_bottom - (text_top + title_h + 0.08)), 3),
                    cap_text,
                    9.5,
                    z_index=2,
                    color_hex=theme.caption_on_dark,
                    font=theme.font_family,
                    role="attribution_caption",
                    page_notes=notes,
                )
            )
        bul_max_h = round(band_bottom - text_top - (0.42 if citations else 0.0), 3)
        bh = _place_content_fitted_bullets(
            elements,
            right_x,
            text_top,
            right_w,
            bul_max_h,
            bullet_text,
            16.5,
            notes,
            color_hex=theme.text_on_dark_soft,
            rail_color=theme.accent_light,
            with_accent_rail=False,
            min_box_h=0.90,
            theme=theme,
        )
        if citations:
            _place_anchored_citation(
                elements,
                citations,
                x=right_x,
                y=round(text_top + bh + 0.08, 3),
                w=right_w,
                max_bottom=band_bottom,
                color_hex=theme.citation_on_dark,
                page_notes=notes,
                theme=theme,
            )
        if has_ph:
            _place_compact_placeholder_tray(
                elements, placeholders, y=6.62, tray_h=0.58, page_notes=notes, dark_theme=True, theme=theme
            )
        return elements

    # ================================================================ 4) IMAGE-LEFT / IMAGE-RIGHT (clean asymmetric editorial split)
    if lid in ("image-left", "image-right") and real_visuals:
        vis_on_left = lid == "image-left"
        vis_x = GRID_MARGIN_X if vis_on_left else round(GRID_MARGIN_X + SPLIT_TEXT_W + GRID_GUTTER, 3)
        text_x = round(GRID_MARGIN_X + SPLIT_VIS_W + GRID_GUTTER, 3) if vis_on_left else GRID_MARGIN_X

        # Subtle top edge accent strip (minimalist 0.06in line, NO 2010 pale-blue header box!)
        elements.append(
            DesignElement(
                element_id="accent_bar",
                kind=ElementKind.shape,
                x_in=0.0,
                y_in=0.0,
                w_in=CANVAS_W_IN,
                h_in=0.06,
                z_index=1,
                fill_hex=theme.primary,
                line_hex=theme.primary,
                role="top_accent",
            )
        )

        top_y = 0.65
        slot_h = round(content_bottom - top_y, 3)

        # Title directly in the text column (aligned to top_y = 0.65 -> exact top alignment with hero visual!)
        elements.append(
            _make_fitted_text(
                "title",
                ElementKind.title,
                text_x,
                top_y,
                SPLIT_TEXT_W,
                1.10,
                page.title,
                28.0,
                z_index=2,
                bold=True,
                color_hex=theme.primary_deep,
                font=theme.font_family,
                role="split_title",
                page_notes=notes,
            )
        )
        elements.append(
            DesignElement(
                element_id="accent_divider",
                kind=ElementKind.shape,
                x_in=text_x,
                y_in=1.84,
                w_in=1.10,
                h_in=0.05,
                z_index=1,
                fill_hex=theme.accent,
                line_hex=theme.accent,
                role="title_underline",
            )
        )
        bul_y = 2.08
        bul_max_h = round(content_bottom - bul_y - (0.50 if citations else 0.0), 3)
        bh = _place_content_fitted_bullets(
            elements,
            text_x,
            bul_y,
            SPLIT_TEXT_W,
            bul_max_h,
            bullet_text,
            base_pt,
            notes,
            with_accent_rail=True,
            theme=theme,
        )
        if citations:
            _place_anchored_citation(
                elements,
                citations,
                x=text_x,
                y=round(bul_y + bh + 0.14, 3),
                w=SPLIT_TEXT_W,
                max_bottom=content_bottom,
                page_notes=notes,
                theme=theme,
            )

        # Hero visual(s) in the dedicated visual column
        _place_real_visuals_group(elements, real_visuals, vis_x, top_y, SPLIT_VIS_W, slot_h, notes, theme=theme)

        if has_ph:
            _place_compact_placeholder_tray(elements, placeholders, y=tray_y, tray_h=tray_h, page_notes=notes, theme=theme)
        return elements

    # ================================================================ 5) QUOTE (minimalist warm editorial thesis / statement)
    if lid == "quote":
        elements.append(
            DesignElement(
                element_id="bg_canvas",
                kind=ElementKind.shape,
                x_in=0.0,
                y_in=0.0,
                w_in=CANVAS_W_IN,
                h_in=CANVAS_H_IN,
                z_index=0,
                fill_hex=theme.surface_warm,
                line_hex=theme.surface_warm,
                role="quote_bg",
            )
        )
        elements.append(
            _make_fitted_text(
                "title",
                ElementKind.title,
                GRID_MARGIN_X,
                0.60,
                GRID_USABLE_W,
                0.78,
                page.title,
                24.0,
                z_index=2,
                bold=True,
                color_hex=theme.primary,
                font=theme.font_family,
                role="quote_kicker_title",
                page_notes=notes,
            )
        )
        elements.append(
            DesignElement(
                element_id="accent_divider",
                kind=ElementKind.shape,
                x_in=GRID_MARGIN_X,
                y_in=1.44,
                w_in=1.40,
                h_in=0.05,
                z_index=1,
                fill_hex=theme.accent,
                line_hex=theme.accent,
                role="quote_rule",
            )
        )

        lead_pt = pts[0]
        rest_pts = pts[1:]
        quote_y = 1.95
        quote_x = round(GRID_MARGIN_X + 0.36, 3)
        if real_visuals:
            quote_w = round(SPLIT_TEXT_W - 0.36, 3)
            vis_x = round(GRID_MARGIN_X + SPLIT_TEXT_W + GRID_GUTTER, 3)
            vis_h = round(content_bottom - 1.70, 3)
            _place_real_visuals_group(elements, real_visuals, vis_x, 1.70, SPLIT_VIS_W, vis_h, notes, theme=theme)
        else:
            quote_w = round(GRID_RIGHT_X - quote_x, 3)
        quote_h = 2.05 if rest_pts else 3.20

        elements.append(
            DesignElement(
                element_id="accent_quote_bar",
                kind=ElementKind.shape,
                x_in=GRID_MARGIN_X,
                y_in=quote_y,
                w_in=0.14,
                h_in=quote_h,
                z_index=1,
                fill_hex=theme.accent,
                line_hex=theme.accent,
                role="quote_accent_bar",
            )
        )
        elements.append(
            _make_fitted_text(
                "quote_main",
                ElementKind.body,
                quote_x,
                quote_y,
                quote_w,
                quote_h,
                lead_pt,
                32.0,
                z_index=2,
                bold=True,
                color_hex=theme.primary_deep,
                font=theme.font_family,
                role="hero_statement",
                page_notes=notes,
            )
        )

        cursor_y = round(quote_y + quote_h + 0.26, 3)
        if rest_pts:
            rest_txt = "\n".join(f"• {p}" for p in rest_pts)
            rem_h = round(content_bottom - cursor_y - (0.48 if citations else 0.0), 3)
            bh = _place_content_fitted_bullets(
                elements,
                quote_x,
                cursor_y,
                quote_w,
                max(0.85, rem_h),
                rest_txt,
                19.0,
                notes,
                color_hex=theme.text_secondary,
                with_accent_rail=False,
                theme=theme,
            )
            cursor_y = round(cursor_y + bh + 0.14, 3)

        if citations:
            _place_anchored_citation(
                elements, citations, x=quote_x, y=cursor_y, w=quote_w, max_bottom=content_bottom, page_notes=notes, theme=theme
            )
        if has_ph:
            _place_compact_placeholder_tray(elements, placeholders, y=tray_y, tray_h=tray_h, page_notes=notes, theme=theme)
        return elements

    # ================================================================ 6) BIG-NUMBER / FACT (bold focal callout + supporting takeaways)
    if lid == "big-number":
        elements.append(
            DesignElement(
                element_id="accent_bar",
                kind=ElementKind.shape,
                x_in=0.0,
                y_in=0.0,
                w_in=CANVAS_W_IN,
                h_in=0.08,
                z_index=1,
                fill_hex=theme.accent,
                line_hex=theme.accent,
                role="top_accent",
            )
        )
        elements.append(
            _make_fitted_text(
                "title",
                ElementKind.title,
                GRID_MARGIN_X,
                0.48,
                GRID_USABLE_W,
                0.88,
                page.title,
                30.0,
                z_index=2,
                bold=True,
                color_hex=theme.primary_deep,
                font=theme.font_family,
                role="fact_title",
                page_notes=notes,
            )
        )

        # Pick the point with a real number/percent if present; otherwise pts[0] (NEVER fabricate numbers!)
        focal_idx = next((i for i, p in enumerate(pts) if re.search(r"\d", p)), 0)
        focal_text = pts[focal_idx]
        other_pts = [p for i, p in enumerate(pts) if i != focal_idx]

        panel_y = 1.65
        panel_h = round(content_bottom - panel_y - (0.50 if citations and not other_pts else 0.0), 3)
        left_w = 5.45
        right_x = round(GRID_MARGIN_X + left_w + 0.48, 3)
        right_w = round(GRID_RIGHT_X - right_x, 3)

        # Left bold focal panel (height fitted tightly to focal text so there is no oversized empty block)
        focal_w = round(left_w - 0.56, 3)
        focal_fit = fit_text_to_box(focal_text, focal_w, max(1.4, min(panel_h, 3.60) - 0.68), 26.0, min_font_pt=16.0)
        focal_h_est = estimate_text_height_in(focal_fit.lines, focal_fit.font_size_pt)
        fact_box_h = round(min(panel_h, max(1.85, focal_h_est + 0.80)), 3)
        bullet_min_h = 1.80

        if allow_sparse and not real_visuals:
            band_top = 1.36
            eff_bottom = tray_y if has_ph else 7.00
            avail_h = round(eff_bottom - band_top, 3)
            cite_reserve = 0.62 if citations else 0.0
            other_est_h = (
                estimate_text_height_in(wrap_mixed_text("\n".join(f"• {p}" for p in other_pts), right_w, 20.0), 20.0) + 0.34
                if other_pts
                else 0.0
            )
            natural_h = round(max(fact_box_h, other_est_h + cite_reserve, fact_box_h + (cite_reserve if not other_pts else 0.0)), 3)
            if avail_h - natural_h > 2.75:
                cluster_h = round(max(natural_h, avail_h - 2.75), 3)
                offset = round((avail_h - cluster_h) * 0.45, 3)
                panel_y = round(band_top + max(0.28, offset), 3)
                fact_box_h = round(min(content_bottom - panel_y - cite_reserve, max(fact_box_h, cluster_h - cite_reserve)), 3)
                bullet_min_h = round(max(1.80, fact_box_h - 0.34), 3)
                panel_h = round(content_bottom - panel_y - (0.50 if citations and not other_pts else 0.0), 3)
                notes.append("big-number: sparse centered-cluster variant")

        elements.append(
            DesignElement(
                element_id="bg_fact_panel",
                kind=ElementKind.shape,
                x_in=GRID_MARGIN_X,
                y_in=panel_y,
                w_in=left_w,
                h_in=fact_box_h,
                z_index=0,
                fill_hex=theme.surface_fact,
                line_hex=theme.surface_fact,
                role="fact_highlight_bg",
            )
        )
        elements.append(
            DesignElement(
                element_id="accent_fact_bar",
                kind=ElementKind.shape,
                x_in=GRID_MARGIN_X,
                y_in=panel_y,
                w_in=0.12,
                h_in=fact_box_h,
                z_index=1,
                fill_hex=theme.primary,
                line_hex=theme.primary,
                role="fact_accent_bar",
            )
        )
        elements.append(
            _make_fitted_text(
                "fact_focal",
                ElementKind.body,
                round(GRID_MARGIN_X + 0.34, 3),
                round(panel_y + 0.34, 3),
                focal_w,
                round(max(1.10, fact_box_h - 0.68), 3),
                focal_text,
                26.0,
                z_index=2,
                bold=True,
                color_hex=theme.primary_deep,
                font=theme.font_family,
                role="big_fact",
                page_notes=notes,
            )
        )

        # Right column: real chart/visual(s) if available, else supporting key points
        if real_visuals:
            _place_real_visuals_group(elements, real_visuals, right_x, panel_y, right_w, panel_h, notes, theme=theme)
            if citations:
                _place_anchored_citation(
                    elements,
                    citations,
                    x=GRID_MARGIN_X,
                    y=round(panel_y + fact_box_h + 0.14, 3),
                    w=left_w,
                    max_bottom=content_bottom,
                    page_notes=notes,
                    theme=theme,
                )
        else:
            if other_pts:
                other_txt = "\n".join(f"• {p}" for p in other_pts)
                bh = _place_content_fitted_bullets(
                    elements,
                    right_x,
                    round(panel_y + 0.34, 3),
                    right_w,
                    round(panel_h - 0.38 - (0.48 if citations else 0.0), 3),
                    other_txt,
                    20.0,
                    notes,
                    with_accent_rail=False,
                    min_box_h=bullet_min_h,
                    theme=theme,
                )
                if citations:
                    _place_anchored_citation(
                        elements,
                        citations,
                        x=right_x,
                        y=round(panel_y + 0.34 + bh + 0.16, 3),
                        w=right_w,
                        max_bottom=content_bottom,
                        page_notes=notes,
                        theme=theme,
                    )
            elif citations:
                _place_anchored_citation(
                    elements,
                    citations,
                    x=GRID_MARGIN_X,
                    y=round(panel_y + fact_box_h + 0.14, 3),
                    w=GRID_USABLE_W,
                    max_bottom=content_bottom,
                    page_notes=notes,
                    theme=theme,
                )

        if has_ph:
            _place_compact_placeholder_tray(elements, placeholders, y=tray_y, tray_h=tray_h, page_notes=notes, theme=theme)
        return elements

    # ================================================================ 7) COMPARISON / TWO-COLS (balanced dual-column layout)
    if lid == "comparison":
        elements.append(
            _make_fitted_text(
                "title",
                ElementKind.title,
                GRID_MARGIN_X,
                0.48,
                GRID_USABLE_W,
                0.86,
                page.title,
                30.0,
                z_index=2,
                bold=True,
                color_hex=theme.primary_deep,
                font=theme.font_family,
                role="comparison_title",
                page_notes=notes,
            )
        )
        elements.append(
            DesignElement(
                element_id="accent_divider",
                kind=ElementKind.shape,
                x_in=GRID_MARGIN_X,
                y_in=1.42,
                w_in=GRID_USABLE_W,
                h_in=0.03,
                z_index=1,
                fill_hex=theme.stroke,
                line_hex=theme.stroke,
                role="title_divider",
            )
        )

        col_gap = 0.53
        col_w = round((GRID_USABLE_W - col_gap) / 2.0, 3)  # 5.75 in
        left_x = GRID_MARGIN_X
        right_x = round(GRID_RIGHT_X - col_w, 3)
        div_x = round(left_x + col_w + col_gap / 2.0 - 0.015, 3)

        top_bar_y = 1.72
        col_top = 1.94
        col_bottom = round(content_bottom - (0.50 if citations else 0.0), 3)
        col_h = round(col_bottom - col_top, 3)
        col_min_h = 1.60

        if allow_sparse and not real_visuals:
            mid = max(1, math.ceil(len(pts) / 2))
            left_pts_est = pts[:mid]
            right_pts_est = pts[mid:] if len(pts) > 1 else []
            col_pt_est = 20.0 if concise else 19.0
            l_est = estimate_text_height_in(wrap_mixed_text("\n".join(f"• {p}" for p in left_pts_est), col_w, col_pt_est), col_pt_est) + 0.24
            r_est = (
                estimate_text_height_in(wrap_mixed_text("\n".join(f"• {p}" for p in right_pts_est), col_w, col_pt_est), col_pt_est) + 0.24
                if right_pts_est
                else 0.0
            )
            cite_res = 0.60 if citations else 0.0
            natural_col_h = round(max(l_est, r_est, 1.60), 3)
            band_top = 1.34
            eff_bottom = tray_y if has_ph else 7.00
            avail_h = round(eff_bottom - band_top, 3)
            if avail_h - (natural_col_h + cite_res) > 2.75:
                cluster_total = round(avail_h - 2.70, 3)
                offset = min(1.18, round((avail_h - cluster_total) * 0.44, 3))
                col_top = round(band_top + max(0.38, offset), 3)
                top_bar_y = round(col_top - 0.22, 3)
                col_h = round(col_bottom - col_top, 3)
                col_min_h = round(min(col_h, max(1.60, cluster_total - cite_res)), 3)
                notes.append("comparison: sparse centered-cluster variant")

        # Contrasting top accent bars + slim center vertical divider
        elements.append(
            DesignElement(
                element_id="accent_col_left",
                kind=ElementKind.shape,
                x_in=left_x,
                y_in=top_bar_y,
                w_in=col_w,
                h_in=0.08,
                z_index=1,
                fill_hex=theme.primary,
                line_hex=theme.primary,
                role="col_accent_left",
            )
        )
        elements.append(
            DesignElement(
                element_id="accent_col_right",
                kind=ElementKind.shape,
                x_in=right_x,
                y_in=top_bar_y,
                w_in=col_w,
                h_in=0.08,
                z_index=1,
                fill_hex=theme.accent,
                line_hex=theme.accent,
                role="col_accent_right",
            )
        )

        divider_h = min(col_h, 3.20)
        if len(real_visuals) >= 2:
            # Multiple real visuals distributed cleanly beneath compact points
            bh = _place_content_fitted_bullets(
                elements, left_x, col_top, GRID_USABLE_W, 1.30, bullet_text, 16.0, notes, with_accent_rail=False, min_box_h=0.65, theme=theme
            )
            vis_y = round(col_top + bh + 0.16, 3)
            vis_h = round(max(1.80, col_bottom - vis_y), 3)
            n_vis = len(real_visuals)
            if n_vis == 2:
                elements.extend(_design_real_visual(real_visuals[0], 1, left_x, vis_y, col_w, vis_h, notes, theme=theme))
                elements.extend(_design_real_visual(real_visuals[1], 2, right_x, vis_y, col_w, vis_h, notes, theme=theme))
            elif n_vis == 3:
                vgap = 0.24
                vcw = round((GRID_USABLE_W - vgap * (n_vis - 1)) / n_vis, 3)
                for i, item in enumerate(real_visuals):
                    vx = round(GRID_MARGIN_X + i * (vcw + vgap), 3)
                    cw = round(GRID_RIGHT_X - vx, 3) if i == n_vis - 1 else vcw
                    elements.extend(_design_real_visual(item, i + 1, vx, vis_y, cw, vis_h, notes, theme=theme))
            else:
                _place_real_visuals_group(elements, real_visuals, left_x, vis_y, GRID_USABLE_W, vis_h, notes, theme=theme)
            last_y = round(vis_y + vis_h, 3)
            divider_h = 0.0
        elif len(real_visuals) == 1:
            bh = _place_content_fitted_bullets(
                elements, left_x, col_top, col_w, col_h, bullet_text, base_pt, notes, with_accent_rail=False, min_box_h=1.60, theme=theme
            )
            elements.extend(_design_real_visual(real_visuals[0], 1, right_x, col_top, col_w, col_h, notes, theme=theme))
            last_y = round(col_top + max(bh, col_h), 3)
            divider_h = round(max(bh, min(col_h, 3.20)), 3)
        else:
            mid = max(1, math.ceil(len(pts) / 2))
            left_pts = pts[:mid]
            right_pts = pts[mid:] if len(pts) > 1 else []
            left_txt = "\n".join(f"• {p}" for p in left_pts) if len(left_pts) > 1 else left_pts[0]
            bh_l = _place_content_fitted_bullets(
                elements,
                left_x,
                col_top,
                col_w,
                col_h,
                left_txt,
                20.0 if concise else 19.0,
                notes,
                element_id="col_left",
                with_accent_rail=False,
                min_box_h=col_min_h,
                theme=theme,
            )
            bh_r = 0.0
            if right_pts:
                right_txt = "\n".join(f"• {p}" for p in right_pts) if len(right_pts) > 1 else right_pts[0]
                bh_r = _place_content_fitted_bullets(
                    elements,
                    right_x,
                    col_top,
                    col_w,
                    col_h,
                    right_txt,
                    20.0 if concise else 19.0,
                    notes,
                    element_id="col_right",
                    with_accent_rail=False,
                    min_box_h=col_min_h,
                    theme=theme,
                )
            last_y = round(col_top + max(bh_l, bh_r), 3)
            divider_h = round(max(bh_l, bh_r), 3)

        if divider_h > 0.2:
            elements.append(
                DesignElement(
                    element_id="accent_col_divider",
                    kind=ElementKind.shape,
                    x_in=div_x,
                    y_in=col_top,
                    w_in=0.03,
                    h_in=divider_h,
                    z_index=1,
                    fill_hex=theme.stroke,
                    line_hex=theme.stroke,
                    role="col_divider",
                )
            )

        if citations:
            _place_anchored_citation(
                elements, citations, x=GRID_MARGIN_X, y=round(last_y + 0.14, 3), w=GRID_USABLE_W, max_bottom=content_bottom, page_notes=notes, theme=theme
            )
        if has_ph:
            _place_compact_placeholder_tray(elements, placeholders, y=tray_y, tray_h=tray_h, page_notes=notes, theme=theme)
        return elements

    # ================================================================ 8) TIMELINE (horizontal step progression)
    if lid == "timeline":
        elements.append(
            _make_fitted_text(
                "title",
                ElementKind.title,
                GRID_MARGIN_X,
                0.48,
                GRID_USABLE_W,
                0.86,
                page.title,
                30.0,
                z_index=2,
                bold=True,
                color_hex=theme.primary_deep,
                font=theme.font_family,
                role="timeline_title",
                page_notes=notes,
            )
        )

        # Group points into up to 4 horizontal steps
        n_steps = min(4, max(1, len(pts)))
        grouped: list[list[str]] = [[] for _ in range(n_steps)]
        for i, p in enumerate(pts):
            grouped[min(i, n_steps - 1)].append(p)

        track_y = 1.92
        step_gap = 0.30
        step_w = round((GRID_USABLE_W - step_gap * (n_steps - 1)) / n_steps, 3)
        step_colors = [theme.graph_0, theme.graph_1, theme.graph_2, theme.graph_3]
        step_top = 2.24
        if real_visuals:
            step_h = 1.55
            vis_y = round(step_top + step_h + 0.20, 3)
            vis_h = round(max(1.80, content_bottom - vis_y - (0.45 if citations else 0.0)), 3)
        else:
            step_pt = 19.0 if n_steps <= 3 else 17.0
            step_h = round(min(3.30, content_bottom - step_top - (0.50 if citations else 0.0)), 3)
            if allow_sparse and len(pts) <= 3:
                est_heights = [
                    estimate_text_height_in(wrap_mixed_text("\n".join(f"• {p}" for p in b) if len(b) > 1 else b[0], step_w, step_pt), step_pt) + 0.35
                    for b in grouped
                ]
                nat_h = round(max(est_heights, default=1.40), 3)
                band_top = 1.34
                eff_bottom = tray_y if has_ph else 7.00
                avail_h = round(eff_bottom - band_top, 3)
                cite_res = 0.66 if citations else 0.0
                if avail_h - (nat_h + cite_res) > 2.75:
                    cluster_total = round(avail_h - 2.70, 3)
                    offset = min(1.18, round((avail_h - cluster_total) * 0.44, 3))
                    step_top = round(band_top + max(0.56, offset), 3)
                    track_y = round(step_top - 0.32, 3)
                    step_h = round(min(3.30, content_bottom - step_top - cite_res, max(nat_h, cluster_total - cite_res)), 3)
                    notes.append("timeline: sparse centered-cluster variant")
            vis_y = 0.0
            vis_h = 0.0

        elements.append(
            DesignElement(
                element_id="accent_timeline_bar",
                kind=ElementKind.shape,
                x_in=GRID_MARGIN_X,
                y_in=track_y,
                w_in=GRID_USABLE_W,
                h_in=0.05,
                z_index=1,
                fill_hex=theme.stroke,
                line_hex=theme.stroke,
                role="timeline_track",
            )
        )

        for idx_s, bucket in enumerate(grouped):
            sx = round(GRID_MARGIN_X + idx_s * (step_w + step_gap), 3)
            sw = round(GRID_RIGHT_X - sx, 3) if idx_s == n_steps - 1 else step_w
            scolor = step_colors[idx_s % len(step_colors)]
            elements.append(
                DesignElement(
                    element_id=f"accent_step_{idx_s + 1}",
                    kind=ElementKind.shape,
                    x_in=sx,
                    y_in=round(track_y - 0.06, 3),
                    w_in=min(0.85, sw),
                    h_in=0.16,
                    z_index=1,
                    fill_hex=scolor,
                    line_hex=scolor,
                    role="timeline_step_marker",
                )
            )
            step_text = "\n".join(f"• {p}" for p in bucket) if len(bucket) > 1 else bucket[0]
            elements.append(
                _make_fitted_text(
                    f"step_{idx_s + 1}",
                    ElementKind.body,
                    sx,
                    step_top,
                    sw,
                    step_h,
                    step_text,
                    19.0 if n_steps <= 3 else 17.0,
                    z_index=2,
                    bold=(len(bucket) == 1),
                    color_hex=theme.background_text,
                    font=theme.font_family,
                    role="timeline_step",
                    page_notes=notes,
                )
            )

        if real_visuals:
            _place_real_visuals_group(elements, real_visuals, GRID_MARGIN_X, vis_y, GRID_USABLE_W, vis_h, notes, theme=theme)
            cite_y = round(vis_y + vis_h + 0.10, 3)
        else:
            cite_y = round(step_top + step_h + 0.16, 3)

        if citations:
            _place_anchored_citation(
                elements, citations, x=GRID_MARGIN_X, y=cite_y, w=GRID_USABLE_W, max_bottom=content_bottom, page_notes=notes, theme=theme
            )
        if has_ph:
            _place_compact_placeholder_tray(elements, placeholders, y=tray_y, tray_h=tray_h, page_notes=notes, theme=theme)
        return elements

    # ================================================================ 9) NEEDS-INPUT-LIST (dedicated appendix checklist)
    if lid == "needs-input-list":
        elements.append(
            DesignElement(
                element_id="accent_bar",
                kind=ElementKind.shape,
                x_in=0.0,
                y_in=0.0,
                w_in=CANVAS_W_IN,
                h_in=0.08,
                z_index=1,
                fill_hex=theme.placeholder_line,
                line_hex=theme.placeholder_line,
                role="top_accent",
            )
        )
        elements.append(
            _make_fitted_text(
                "title",
                ElementKind.title,
                GRID_MARGIN_X,
                0.48,
                GRID_USABLE_W,
                0.86,
                page.title,
                28.0,
                z_index=2,
                bold=True,
                color_hex=theme.primary_deep,
                font=theme.font_family,
                role="needs_input_title",
                page_notes=notes,
            )
        )
        bh = _place_content_fitted_bullets(
            elements, GRID_MARGIN_X, 1.50, GRID_USABLE_W, 1.65, bullet_text, 16.0, notes, with_accent_rail=True, theme=theme
        )
        list_y = round(1.50 + bh + 0.24, 3)
        for i, item in enumerate(placeholders):
            req_id = _require_placeholder_request_id(item)
            row_h = 0.60
            ry = round(list_y + i * (row_h + 0.16), 3)
            if ry + row_h > 7.20:
                break
            pw = 10.20  # 10.20 * 0.60 = 6.12 sq in = 8.5% of content area (<= 15%)
            txt, pt = _format_compact_placeholder_text(item, pw, row_h, 11.5)
            elements.append(
                DesignElement(
                    element_id=f"PLACEHOLDER_{req_id}",
                    kind=ElementKind.placeholder,
                    x_in=GRID_MARGIN_X,
                    y_in=ry,
                    w_in=pw,
                    h_in=row_h,
                    z_index=2,
                    align="left",
                    text=txt,
                    font=theme.font_family,
                    font_size_pt=pt,
                    color_hex=theme.placeholder_text,
                    fill_hex=theme.placeholder_fill,
                    line_hex=theme.placeholder_line,
                    role="compact_placeholder",
                    asset_id=item.asset_id,
                    request_id=req_id,
                )
            )
        if citations:
            _place_anchored_citation(
                elements, citations, x=GRID_MARGIN_X, y=6.78, w=GRID_USABLE_W, max_bottom=7.28, page_notes=notes, theme=theme
            )
        return elements

    # ================================================================ Fallback: route to section
    return build_layout_elements(
        "section", page, bullet_points, real_visuals, placeholders, citations, notes, concise=concise, theme=theme, allow_sparse=allow_sparse
    )
