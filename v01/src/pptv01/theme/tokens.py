# Ported/adapted from presenton/presenton (Apache-2.0): servers/fastapi/models/theme_data.py and servers/fastapi/templates/v2/theme.py.
"""Theme tokens and WCAG contrast helpers for Stage F layout and Stage G DeckOps compilation.

Round 6 (R6-1) provides a single theme (`academic-default`) preserving the exact colors and fonts
originally hardcoded in `f_layout/layouts.py` and `g_build/compile_ops.py`, plus the Presenton
graph_0..graph_9 color series and pure color/contrast utilities.
"""

from __future__ import annotations

import colorsys
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


def _normalize_hex(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lstrip("#")
    if len(normalized) == 3:
        normalized = "".join(character * 2 for character in normalized)
    if len(normalized) == 8:
        normalized = normalized[:6]
    if not re.fullmatch(r"[0-9a-fA-F]{6}", normalized):
        return None
    return f"#{normalized.upper()}"


def _rgb(color: str) -> tuple[int, int, int]:
    normalized = _normalize_hex(color)
    if not normalized:
        raise ValueError(f"invalid color: {color!r}")
    return (
        int(normalized[1:3], 16),
        int(normalized[3:5], 16),
        int(normalized[5:7], 16),
    )


def _hex_from_rgb_float(rgb: tuple[float, float, float]) -> str:
    return "#{:02X}{:02X}{:02X}".format(
        *(round(max(0.0, min(1.0, channel)) * 255) for channel in rgb)
    )


def _relative_luminance(color: str) -> float:
    """Compute WCAG relative luminance of a #RRGGBB color (ported from presenton)."""
    channels = []
    for channel in _rgb(color):
        value = channel / 255
        channels.append(
            value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
        )
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast_ratio(first: str, second: str) -> float:
    """Compute WCAG 2.x contrast ratio between two hex colors (1.0 .. 21.0, ported from presenton)."""
    first_luminance = _relative_luminance(first)
    second_luminance = _relative_luminance(second)
    lighter = max(first_luminance, second_luminance)
    darker = min(first_luminance, second_luminance)
    return (lighter + 0.05) / (darker + 0.05)


def _repair_text_color(
    selected: str,
    surface: str,
    candidates: Sequence[Any] = (),
    *,
    min_ratio: float = 4.5,
    fallback_dark: str = "#000000",
    fallback_light: str = "#FFFFFF",
) -> str:
    """Ensure text color meets `min_ratio` contrast on `surface` (ported from presenton)."""
    if contrast_ratio(selected, surface) >= min_ratio:
        return _normalize_hex(selected) or selected

    scored_candidates: list[tuple[int, str]] = []
    for item in candidates:
        if isinstance(item, str):
            hex_val = _normalize_hex(item)
            weight = 0
        else:
            hex_val = _normalize_hex(getattr(item, "hex", None))
            weight = int(getattr(item, "text_count", 0))
        if hex_val:
            scored_candidates.append((weight, hex_val))

    scored_candidates.sort(
        key=lambda entry: (-entry[0], -contrast_ratio(entry[1], surface))
    )
    for _, cand_hex in scored_candidates:
        if contrast_ratio(cand_hex, surface) >= min_ratio:
            return cand_hex

    return max(
        (_normalize_hex(fallback_dark) or "#000000", _normalize_hex(fallback_light) or "#FFFFFF"),
        key=lambda color: contrast_ratio(color, surface),
    )


def _complete_graph_colors(
    selected: Sequence[str],
    primary: str,
    *,
    count: int = 10,
) -> tuple[str, ...]:
    """Complete a deterministic `count`-color chart/diagram series (ported from presenton)."""
    colors: list[str] = []
    for color in [*selected, primary]:
        normalized = _normalize_hex(color)
        if normalized and normalized not in colors:
            colors.append(normalized)

    hue, lightness, saturation = colorsys.rgb_to_hls(
        *(channel / 255 for channel in _rgb(primary))
    )
    for index in range(1, 25):
        generated = _hex_from_rgb_float(
            colorsys.hls_to_rgb(
                (hue + index * 0.137) % 1.0,
                max(0.25, min(0.78, lightness + (0.10 if index % 2 else -0.08))),
                max(0.45, saturation),
            )
        )
        if generated not in colors:
            colors.append(generated)
        if len(colors) >= count:
            break
    return tuple(colors[:count])


def _surface_variant(color: str, distance: float = 0.08) -> str:
    """Derive a subtle lighter/darker surface variant in HLS space (ported from presenton)."""
    red, green, blue = _rgb(color)
    hue, lightness, saturation = colorsys.rgb_to_hls(red / 255, green / 255, blue / 255)
    target_lightness = (
        max(0.0, lightness - distance)
        if lightness > 0.55
        else min(1.0, lightness + distance)
    )
    return _hex_from_rgb_float(colorsys.hls_to_rgb(hue, target_lightness, saturation))


@dataclass(frozen=True)
class ThemeTokens:
    """Visual token bundle shared by Stage F layout and Stage G compilation."""

    theme_id: str
    # Brand & structural accents (cf. Presenton PresentationThemeColors)
    primary_deep: str
    primary: str
    accent: str
    accent_light: str
    teal: str
    # Surfaces & strokes
    background: str
    surface_light: str
    surface_warm: str
    surface_fact: str
    stroke: str
    # Text hierarchy
    background_text: str
    text_secondary: str
    text_muted: str
    primary_text: str
    text_on_dark_soft: str
    # Captions & citations (separate light/dark tokens so R6-L #5 can tune contrast independently)
    caption_on_light: str
    caption_on_dark: str
    citation_on_light: str
    citation_on_dark: str
    # Placeholders (light & dark themes)
    placeholder_fill: str
    placeholder_line: str
    placeholder_text: str
    dark_placeholder_fill: str
    dark_placeholder_line: str
    dark_placeholder_text: str
    # Stage G compile fallbacks & diagram styling
    diagram_fill: str
    diagram_line: str
    title_fallback: str
    body_fallback: str
    # Typography
    font_family: str
    # Presenton-style graph_0 .. graph_9 categorical series
    graph_0: str
    graph_1: str
    graph_2: str
    graph_3: str
    graph_4: str
    graph_5: str
    graph_6: str
    graph_7: str
    graph_8: str
    graph_9: str
    title_font: str = "Microsoft YaHei"

    @property
    def has_tinted_canvas(self) -> bool:
        """True when slides require an explicit non-white canvas fill shape (leaves academic-default unchanged)."""
        return (_normalize_hex(self.background) or "#FFFFFF") != "#FFFFFF"

    @property
    def graph_colors(self) -> tuple[str, ...]:
        return (
            self.graph_0,
            self.graph_1,
            self.graph_2,
            self.graph_3,
            self.graph_4,
            self.graph_5,
            self.graph_6,
            self.graph_7,
            self.graph_8,
            self.graph_9,
        )


DEFAULT_THEME_ID = "academic-default"

_DEFAULT_GRAPH_SERIES = _complete_graph_colors(
    ("#1B365D", "#0284C7", "#0D9488", "#2563EB"),
    "#1B365D",
    count=10,
)

ACADEMIC_DEFAULT_THEME = ThemeTokens(
    theme_id=DEFAULT_THEME_ID,
    primary_deep="#0F2544",
    primary="#1B365D",
    accent="#0284C7",
    accent_light="#38BDF8",
    teal="#0D9488",
    background="#FFFFFF",
    surface_light="#F8FAFC",
    surface_warm="#F1F5F9",
    surface_fact="#EFF6FF",
    stroke="#CBD5E1",
    background_text="#0F172A",
    text_secondary="#334155",
    text_muted="#64748B",
    primary_text="#FFFFFF",
    text_on_dark_soft="#E2E8F0",
    caption_on_light="#475569",
    caption_on_dark="#CBD5E1",
    citation_on_light="#475569",
    citation_on_dark="#CBD5E1",
    placeholder_fill="#FFFBEB",
    placeholder_line="#D97706",
    placeholder_text="#92400E",
    dark_placeholder_fill="#172A45",
    dark_placeholder_line="#F59E0B",
    dark_placeholder_text="#FDE68A",
    diagram_fill="#EFF6FF",
    diagram_line="#1E3A8A",
    title_fallback="#1E3A8A",
    body_fallback="#1E293B",
    font_family="Microsoft YaHei",
    graph_0=_DEFAULT_GRAPH_SERIES[0],
    graph_1=_DEFAULT_GRAPH_SERIES[1],
    graph_2=_DEFAULT_GRAPH_SERIES[2],
    graph_3=_DEFAULT_GRAPH_SERIES[3],
    graph_4=_DEFAULT_GRAPH_SERIES[4],
    graph_5=_DEFAULT_GRAPH_SERIES[5],
    graph_6=_DEFAULT_GRAPH_SERIES[6],
    graph_7=_DEFAULT_GRAPH_SERIES[7],
    graph_8=_DEFAULT_GRAPH_SERIES[8],
    graph_9=_DEFAULT_GRAPH_SERIES[9],
    title_font="Microsoft YaHei",
)

LIBRARY_DIR = Path(__file__).resolve().parent / "library"


def _theme_from_dict(data: dict[str, Any]) -> ThemeTokens:
    """Construct a ThemeTokens instance from a theme library JSON mapping."""
    seeds = data.get("graph_seeds") or [
        data["primary"],
        data["accent"],
        data["teal"],
        data["accent_light"],
    ]
    series = _complete_graph_colors(
        tuple(str(c) for c in seeds),
        str(data.get("background", "#FFFFFF")),
        count=10,
    )
    return ThemeTokens(
        theme_id=str(data["theme_id"]),
        primary_deep=str(data["primary_deep"]),
        primary=str(data["primary"]),
        accent=str(data["accent"]),
        accent_light=str(data["accent_light"]),
        teal=str(data["teal"]),
        background=str(data["background"]),
        surface_light=str(data["surface_light"]),
        surface_warm=str(data["surface_warm"]),
        surface_fact=str(data["surface_fact"]),
        stroke=str(data["stroke"]),
        background_text=str(data["background_text"]),
        text_secondary=str(data["text_secondary"]),
        text_muted=str(data["text_muted"]),
        primary_text=str(data["primary_text"]),
        text_on_dark_soft=str(data["text_on_dark_soft"]),
        caption_on_light=str(data["caption_on_light"]),
        caption_on_dark=str(data["caption_on_dark"]),
        citation_on_light=str(data["citation_on_light"]),
        citation_on_dark=str(data["citation_on_dark"]),
        placeholder_fill=str(data["placeholder_fill"]),
        placeholder_line=str(data["placeholder_line"]),
        placeholder_text=str(data["placeholder_text"]),
        dark_placeholder_fill=str(data["dark_placeholder_fill"]),
        dark_placeholder_line=str(data["dark_placeholder_line"]),
        dark_placeholder_text=str(data["dark_placeholder_text"]),
        diagram_fill=str(data["diagram_fill"]),
        diagram_line=str(data["diagram_line"]),
        title_fallback=str(data["title_fallback"]),
        body_fallback=str(data["body_fallback"]),
        font_family=str(data["font_family"]),
        title_font=str(data.get("title_font") or data["font_family"]),
        graph_0=str(data.get("graph_0") or series[0]),
        graph_1=str(data.get("graph_1") or series[1]),
        graph_2=str(data.get("graph_2") or series[2]),
        graph_3=str(data.get("graph_3") or series[3]),
        graph_4=str(data.get("graph_4") or series[4]),
        graph_5=str(data.get("graph_5") or series[5]),
        graph_6=str(data.get("graph_6") or series[6]),
        graph_7=str(data.get("graph_7") or series[7]),
        graph_8=str(data.get("graph_8") or series[8]),
        graph_9=str(data.get("graph_9") or series[9]),
    )


def load_theme_library(library_dir: Path | None = None) -> dict[str, ThemeTokens]:
    """Load all theme token bundles from `theme/library/*.json`, anchored by `academic-default`."""
    themes: dict[str, ThemeTokens] = {DEFAULT_THEME_ID: ACADEMIC_DEFAULT_THEME}
    root = library_dir or LIBRARY_DIR
    if root.is_dir():
        for path in sorted(root.glob("*.json")):
            if path.name == "selection-index.json":
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            tokens = _theme_from_dict(payload)
            if tokens.theme_id == DEFAULT_THEME_ID:
                # Preserve bit-for-bit invariant constant for academic-default
                continue
            themes[tokens.theme_id] = tokens
    return themes


def load_selection_index(library_dir: Path | None = None) -> dict[str, Any]:
    """Load theme selection metadata (`mood/tone/formality/density/scheme/best_for/avoid_for`, adapted from frontend-slides)."""
    root = library_dir or LIBRARY_DIR
    idx_path = root / "selection-index.json"
    if not idx_path.is_file():
        return {"version": 1, "presets": {}}
    return json.loads(idx_path.read_text(encoding="utf-8"))


THEMES: dict[str, ThemeTokens] = load_theme_library()


def list_themes() -> list[str]:
    """Return sorted registered theme IDs."""
    return sorted(THEMES)


def get_theme(theme_id: str | None = None) -> ThemeTokens:
    """Look up a registered theme by ID, falling back to `academic-default` if unset."""
    key = (theme_id or "").strip() or DEFAULT_THEME_ID
    if key in THEMES:
        return THEMES[key]
    raise KeyError(f"unknown theme_id {key!r}; available: {sorted(THEMES)}")
