"""CJK and Latin text capacity estimation, per-layout capacity contracts, and overflow fitting (Stage F).

Adapted from:
  - hugohe3/ppt-master (scripts/svg_quality/slot_capacity.py, MIT License, Copyright (c) 2025-2026 Hugo He)
  - SlideSpeak/slide-design-skill (docs/specs/2026-06-04-layout-fit-model.md, MIT License, Copyright (c) 2026 SlideSpeak)
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

# Average glyph advance ratios relative to font_size_pt (calibrated on desktop PowerPoint 16.0, Microsoft YaHei)
LATIN_AVG_ADVANCE_FACTOR = 0.54
CJK_FULLWIDTH_ADVANCE_FACTOR = 1.02
# Empirically calibrated on desktop Microsoft PowerPoint 16.0 TextFrame2.BoundHeight (+8.3% -> 0.0% error; Office 2021 baseline API)
DEFAULT_LINE_HEIGHT_FACTOR = 1.20
DEFAULT_PADDING_PT = 7.2

_FULLWIDTH_RE = re.compile(r"[\u2e80-\u9fff\uf900-\ufaff\ufe30-\ufe4f\uff00-\uffef\u3000-\u303f]")


@dataclass(frozen=True)
class SlotCapacity:
    font_size_pt: float
    max_lines: int
    cjk_chars_per_line: int
    latin_chars_per_line: int
    cjk_recommended_max_chars: int
    latin_recommended_max_chars: int


@dataclass(frozen=True)
class FitResult:
    text: str
    font_size_pt: float
    original_font_size_pt: float
    lines: list[str]
    max_lines: int
    adjusted: bool
    still_overflows: bool
    note: str = ""


def _char_advance_pt(ch: str, font_size_pt: float) -> float:
    if ch == "\t":
        return font_size_pt * LATIN_AVG_ADVANCE_FACTOR * 4.0
    if _FULLWIDTH_RE.match(ch):
        return font_size_pt * CJK_FULLWIDTH_ADVANCE_FACTOR
    if ch.isspace():
        return font_size_pt * 0.30
    return font_size_pt * LATIN_AVG_ADVANCE_FACTOR


def estimate_slot_capacity(
    w_in: float,
    h_in: float,
    font_size_pt: float,
    *,
    line_height_factor: float = DEFAULT_LINE_HEIGHT_FACTOR,
    padding_pt: float = DEFAULT_PADDING_PT,
) -> SlotCapacity:
    """Binary-search per-line CJK and Latin capacity (adapted from ppt-master slot_capacity.py, MIT)."""
    width_pt = max(16.0, w_in * 72.0)
    height_pt = max(font_size_pt + 4.0, h_in * 72.0)
    usable_w = max(8.0, width_pt - 2.0 * padding_pt)
    usable_h = max(font_size_pt, height_pt - 2.0 * (padding_pt * 0.6))
    line_pitch = max(font_size_pt * 1.05, font_size_pt * line_height_factor)
    max_lines = max(1, 1 + math.floor((usable_h - font_size_pt) / line_pitch))

    def binary_search_chars(advance_per_char: float) -> int:
        low, high = 0, 1
        while high < 10000 and (high * advance_per_char) <= usable_w:
            low, high = high, high * 2
        while low + 1 < high:
            mid = (low + high) // 2
            if (mid * advance_per_char) <= usable_w:
                low = mid
            else:
                high = mid
        return max(1, low)

    cjk_cpl = binary_search_chars(font_size_pt * CJK_FULLWIDTH_ADVANCE_FACTOR)
    latin_cpl = binary_search_chars(font_size_pt * LATIN_AVG_ADVANCE_FACTOR)
    return SlotCapacity(
        font_size_pt=round(font_size_pt, 1),
        max_lines=int(max_lines),
        cjk_chars_per_line=int(cjk_cpl),
        latin_chars_per_line=int(latin_cpl),
        cjk_recommended_max_chars=int(cjk_cpl * max_lines),
        latin_recommended_max_chars=int(latin_cpl * max_lines),
    )


def wrap_mixed_text(
    text: str,
    w_in: float,
    font_size_pt: float,
    *,
    padding_pt: float = DEFAULT_PADDING_PT,
) -> list[str]:
    """Wrap mixed CJK + Latin text to fit ``w_in`` at ``font_size_pt`` without silent truncation."""
    usable_w = max(8.0, w_in * 72.0 - 2.0 * padding_pt)
    lines: list[str] = []
    for raw_line in text.splitlines() or [""]:
        if not raw_line:
            lines.append("")
            continue
        cur: list[str] = []
        cur_w = 0.0
        last_space_idx: int | None = None
        i = 0
        while i < len(raw_line):
            ch = raw_line[i]
            cw = _char_advance_pt(ch, font_size_pt)
            if cur and cur_w + cw > usable_w:
                if last_space_idx is not None and not _FULLWIDTH_RE.match(ch):
                    lines.append("".join(cur[:last_space_idx]).rstrip())
                    rem = "".join(cur[last_space_idx + 1 :])
                    cur = list(rem)
                    cur_w = sum(_char_advance_pt(c, font_size_pt) for c in cur)
                    last_space_idx = None
                    continue
                lines.append("".join(cur).rstrip())
                cur = []
                cur_w = 0.0
                last_space_idx = None
                if ch == " ":
                    i += 1
                    continue
            cur.append(ch)
            cur_w += cw
            if ch == " ":
                last_space_idx = len(cur) - 1
            i += 1
        lines.append("".join(cur).rstrip())
    return lines


def _split_long_clauses(text: str) -> str:
    """Break overly dense single-line clauses at punctuation boundaries to improve wrapping."""
    parts = [p.strip() for p in re.split(r"[；;]\s*", text) if p.strip()]
    if len(parts) > 1:
        return "\n".join(parts)
    return text


def estimate_text_height_in(
    lines: list[str],
    font_size_pt: float,
    *,
    line_height_factor: float = DEFAULT_LINE_HEIGHT_FACTOR,
    padding_pt: float = DEFAULT_PADDING_PT,
) -> float:
    """Estimate rendered text box height in inches calibrated to PowerPoint BoundHeight."""
    n = max(1, len(lines))
    h_pt = n * font_size_pt * line_height_factor + 2.0 * padding_pt
    return round(h_pt / 72.0, 3)


def fit_text_to_box(
    text: str,
    w_in: float,
    h_in: float,
    font_size_pt: float,
    *,
    min_font_pt: float = 12.0,
    line_height_factor: float = DEFAULT_LINE_HEIGHT_FACTOR,
    padding_pt: float = DEFAULT_PADDING_PT,
) -> FitResult:
    """Ensure text fits in the box by stepping down font size (to ``min_font_pt``) and wrapping lines.

    Never silently truncates text. If overflow remains even at ``min_font_pt``, sets
    ``still_overflows=True`` and explains in ``note`` so the caller can expand the box or note it.
    """
    orig_pt = float(font_size_pt)
    cur_pt = float(font_size_pt)
    cur_text = text.strip()
    notes: list[str] = []

    cap = estimate_slot_capacity(w_in, h_in, cur_pt, line_height_factor=line_height_factor, padding_pt=padding_pt)
    lines = wrap_mixed_text(cur_text, w_in, cur_pt, padding_pt=padding_pt)

    if len(lines) > cap.max_lines:
        split_candidate = _split_long_clauses(cur_text)
        if split_candidate != cur_text:
            split_lines = wrap_mixed_text(split_candidate, w_in, cur_pt, padding_pt=padding_pt)
            if len(split_lines) <= cap.max_lines:
                cur_text = split_candidate
                lines = split_lines
                notes.append("split clauses across lines to fit box width")

    while len(lines) > cap.max_lines and cur_pt - 1.0 >= min_font_pt:
        cur_pt -= 1.0
        cap = estimate_slot_capacity(w_in, h_in, cur_pt, line_height_factor=line_height_factor, padding_pt=padding_pt)
        lines = wrap_mixed_text(cur_text, w_in, cur_pt, padding_pt=padding_pt)

    if cur_pt < orig_pt:
        notes.append(f"font reduced {orig_pt:g}pt->{cur_pt:g}pt to prevent overflow ({len(lines)}/{cap.max_lines} lines)")

    still_overflows = len(lines) > cap.max_lines
    if still_overflows:
        notes.append(f"still overflows at min font {cur_pt:g}pt ({len(lines)} lines > max {cap.max_lines})")

    return FitResult(
        text=cur_text,
        font_size_pt=cur_pt,
        original_font_size_pt=orig_pt,
        lines=lines,
        max_lines=cap.max_lines,
        adjusted=bool(notes),
        still_overflows=still_overflows,
        note="; ".join(notes),
    )


# ------------------------------------------------------------------ Layout Capacity Contracts
@dataclass(frozen=True)
class LayoutCapacitySpec:
    """Declarative slot capacity, layout family, eligibility constraints, and fallback policy."""

    layout_id: str
    family: str
    min_points: int
    ideal_points: int
    max_points: int
    min_visuals: int = 0
    max_visuals: int = 4
    requires_sourced_number: bool = False
    requires_two_sided_points: int = 0
    requires_ordered_steps: int = 0
    supports_sparse: bool = False
    fallback_layout: str = "quote"


LAYOUT_CAPACITY_SPECS: dict[str, LayoutCapacitySpec] = {
    "cover": LayoutCapacitySpec(
        layout_id="cover",
        family="anchor",
        min_points=1,
        ideal_points=2,
        max_points=4,
        min_visuals=0,
        max_visuals=2,
        fallback_layout="section",
    ),
    "section": LayoutCapacitySpec(
        layout_id="section",
        family="breathing",
        min_points=1,
        ideal_points=3,
        max_points=6,
        min_visuals=0,
        max_visuals=2,
        fallback_layout="quote",
    ),
    "quote": LayoutCapacitySpec(
        layout_id="quote",
        family="statement",
        min_points=1,
        ideal_points=3,
        max_points=5,
        min_visuals=0,
        max_visuals=1,
        fallback_layout="section",
    ),
    "comparison": LayoutCapacitySpec(
        layout_id="comparison",
        family="comparison",
        min_points=4,
        ideal_points=4,
        max_points=8,
        min_visuals=0,
        max_visuals=4,
        requires_two_sided_points=2,
        supports_sparse=True,
        fallback_layout="quote",
    ),
    "big-number": LayoutCapacitySpec(
        layout_id="big-number",
        family="data",
        min_points=2,
        ideal_points=3,
        max_points=5,
        min_visuals=0,
        max_visuals=1,
        requires_sourced_number=True,
        supports_sparse=True,
        fallback_layout="quote",
    ),
    "timeline": LayoutCapacitySpec(
        layout_id="timeline",
        family="sequence",
        min_points=3,
        ideal_points=4,
        max_points=6,
        min_visuals=0,
        max_visuals=2,
        requires_ordered_steps=3,
        supports_sparse=True,
        fallback_layout="section",
    ),
    "image-left": LayoutCapacitySpec(
        layout_id="image-left",
        family="visual-split",
        min_points=1,
        ideal_points=3,
        max_points=6,
        min_visuals=1,
        max_visuals=4,
        fallback_layout="section",
    ),
    "image-right": LayoutCapacitySpec(
        layout_id="image-right",
        family="visual-split",
        min_points=1,
        ideal_points=3,
        max_points=6,
        min_visuals=1,
        max_visuals=4,
        fallback_layout="section",
    ),
    "full-bleed": LayoutCapacitySpec(
        layout_id="full-bleed",
        family="visual-hero",
        min_points=1,
        ideal_points=3,
        max_points=4,
        min_visuals=1,
        max_visuals=1,
        fallback_layout="image-right",
    ),
    "needs-input-list": LayoutCapacitySpec(
        layout_id="needs-input-list",
        family="appendix",
        min_points=1,
        ideal_points=3,
        max_points=6,
        min_visuals=0,
        max_visuals=0,
        fallback_layout="section",
    ),
    "two-content": LayoutCapacitySpec(
        layout_id="two-content",
        family="two-column",
        min_points=2,
        ideal_points=4,
        max_points=8,
        min_visuals=0,
        max_visuals=2,
        supports_sparse=True,
        fallback_layout="hero-statement",
    ),
    "three-card": LayoutCapacitySpec(
        layout_id="three-card",
        family="cards",
        min_points=3,
        ideal_points=3,
        max_points=7,
        min_visuals=0,
        max_visuals=0,
        supports_sparse=True,
        fallback_layout="two-content",
    ),
    "hero-statement": LayoutCapacitySpec(
        layout_id="hero-statement",
        family="hero-text",
        min_points=2,
        ideal_points=3,
        max_points=5,
        min_visuals=0,
        max_visuals=1,
        supports_sparse=True,
        fallback_layout="two-content",
    ),
}


def get_layout_capacity(layout_id: str) -> LayoutCapacitySpec:
    """Return the declared slot capacity spec for a canonical layout ID."""
    key = (layout_id or "").strip().lower()
    if key in LAYOUT_CAPACITY_SPECS:
        return LAYOUT_CAPACITY_SPECS[key]
    return LAYOUT_CAPACITY_SPECS["section"]


def classify_density(
    n_points: int,
    spec: LayoutCapacitySpec,
    *,
    total_chars: int = 0,
) -> str:
    """Classify slide density into ``sparse``, ``normal``, or ``dense`` against its ``LayoutCapacitySpec``."""
    if spec.supports_sparse and (
        n_points < spec.ideal_points
        or (n_points <= spec.ideal_points and n_points <= 3 and 0 < total_chars <= 84)
    ):
        return "sparse"
    if n_points >= max(spec.ideal_points + 2, spec.max_points - 1):
        return "dense"
    return "normal"
