"""PowerPoint & Office COM numeric constants and conversion helpers.

Adapted from Microsoft Office 16.0 (Office 2021) object model documentation,
thomas-villani/pptlive (MIT, 4b33e69217ba), and ykuwai/ppt-mcp (MIT, 2155ecc9050e).
"""

from __future__ import annotations

import re

PT_PER_IN = 72.0

# MsoTriState
MSO_TRUE = -1
MSO_FALSE = 0

# PpAlertLevel
PP_ALERTS_NONE = 1

# MsoAutomationSecurity
MSO_AUTOMATION_SECURITY_FORCE_DISABLE = 3

# PpWindowState
PP_WINDOW_NORMAL = 1
PP_WINDOW_MINIMIZED = 2

# PpSlideLayout
PP_LAYOUT_TITLE = 1
PP_LAYOUT_TEXT = 2
PP_LAYOUT_TITLE_ONLY = 11
PP_LAYOUT_BLANK = 12

SLIDE_LAYOUT_MAP: dict[str, int] = {
    "title": PP_LAYOUT_TITLE,
    "title_and_content": PP_LAYOUT_TEXT,
    "title_only": PP_LAYOUT_TITLE_ONLY,
    "blank": PP_LAYOUT_BLANK,
}

# PpPlaceholderType
PP_PLACEHOLDER_TITLE = 1
PP_PLACEHOLDER_BODY = 2
PP_PLACEHOLDER_CENTER_TITLE = 3
PP_PLACEHOLDER_SUBTITLE = 4
PP_PLACEHOLDER_OBJECT = 7

# MsoTextOrientation
MSO_TEXT_ORIENTATION_HORIZONTAL = 1

# MsoShapeType
MSO_SHAPE_TYPE_AUTO_SHAPE = 1
MSO_SHAPE_TYPE_CHART = 3
MSO_SHAPE_TYPE_GROUP = 6
MSO_SHAPE_TYPE_LINE = 9
MSO_SHAPE_TYPE_PICTURE = 13
MSO_SHAPE_TYPE_PLACEHOLDER = 14
MSO_SHAPE_TYPE_TEXT_BOX = 17
MSO_SHAPE_TYPE_TABLE = 19

# MsoAutoShapeType
SHAPE_TYPE_MAP: dict[str, int] = {
    "rectangle": 1,           # msoShapeRectangle
    "rounded_rectangle": 5,   # msoShapeRoundedRectangle
    "oval": 9,                # msoShapeOval
    "diamond": 4,             # msoShapeDiamond
}

# MsoConnectorType (from ykuwai/ppt-mcp connectors.py, MIT)
CONNECTOR_TYPE_MAP: dict[str, int] = {
    "straight": 1,  # msoConnectorStraight
    "elbow": 2,     # msoConnectorElbow
    "curve": 3,     # msoConnectorCurve
}

# MsoArrowheadStyle (from ykuwai/ppt-mcp connectors.py, MIT)
ARROW_STYLE_MAP: dict[str, int] = {
    "none": 1,      # msoArrowheadNone
    "triangle": 2,  # msoArrowheadTriangle
    "open": 3,      # msoArrowheadOpen
    "stealth": 4,   # msoArrowheadStealth
    "diamond": 5,   # msoArrowheadDiamond
    "oval": 6,      # msoArrowheadOval
}

# MsoLineDashStyle
DASH_STYLE_MAP: dict[str, int] = {
    "solid": 1,     # msoLineSolid
    "dash": 4,      # msoLineDash
    "round_dot": 3, # msoLineRoundDot
}

# XlChartType (from ykuwai/ppt-mcp charts.py, MIT)
CHART_TYPE_MAP: dict[str, int] = {
    "column": 51,          # xlColumnClustered
    "column_stacked": 52,  # xlColumnStacked
    "bar": 57,             # xlBarClustered
    "line": 4,             # xlLine
    "line_markers": 65,    # xlLineMarkers
    "pie": 5,              # xlPie
    "area": 1,             # xlArea
    "scatter": -4169,      # xlXYScatter
}

# PpParagraphAlignment / MsoParagraphAlignment
ALIGN_MAP: dict[str, int] = {
    "left": 1,     # ppAlignLeft
    "center": 2,   # ppAlignCenter
    "right": 3,    # ppAlignRight
    "justify": 4,  # ppAlignJustify
}

# MsoVerticalAnchor
MSO_ANCHOR_TOP = 1
MSO_ANCHOR_MIDDLE = 3

# PpFixedFormatType / PpSaveAsFileType
PP_FIXED_FORMAT_TYPE_PDF = 2
PP_SAVE_AS_PDF = 32

BOUND_ID_RE = re.compile(r"(?:ASSET|INPUT|CHART|TABLE|DIAGRAM|PLACEHOLDER)(?:_INPUT)?_([AU]\d{3})")


def in_to_pt(inches: float) -> float:
    return round(float(inches) * PT_PER_IN, 3)


def hex_to_bgr(hex_str: str | None, default: tuple[int, int, int] = (30, 41, 59)) -> int:
    """Convert '#RRGGBB' to Office COM BGR integer: R | (G << 8) | (B << 16)."""
    r, g, b = default
    if hex_str:
        h = hex_str.strip().lstrip("#")
        if len(h) == 6:
            try:
                r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            except ValueError:
                pass
    return r | (g << 8) | (b << 16)


def excel_col_letter(col_idx: int) -> str:
    """Convert 1-based column index to Excel column letters (1->A, 27->AA)."""
    out = []
    n = int(col_idx)
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out.append(chr(ord("A") + rem))
    return "".join(reversed(out)) or "A"


def extract_bound_id(shape_name: str) -> str | None:
    m = BOUND_ID_RE.search(shape_name or "")
    return m.group(1) if m else None
