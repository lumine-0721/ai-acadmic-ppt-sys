"""CJK and Latin text capacity estimation and overflow fitting for Stage F.

Glyph-advance binary-search capacity estimation adapted from:
  hugohe3/ppt-master (scripts/svg_quality/slot_capacity.py, MIT License)
  Copyright (c) 2025-2026 Hugo He
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
