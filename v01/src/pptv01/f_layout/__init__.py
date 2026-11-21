from .capacity import (
    FitResult,
    SlotCapacity,
    estimate_slot_capacity,
    estimate_text_height_in,
    fit_text_to_box,
    wrap_mixed_text,
)
from .layouts import (
    CANONICAL_LAYOUT_IDS,
    LEGACY_LAYOUT_ID,
    MAX_PLACEHOLDER_AREA_RATIO,
    normalize_layout_id,
    select_deck_layouts,
    slide_content_area_in2,
)
from .preview import WATERMARK_TEXT, render_schematic_preview
from .stage import ARTIFACT, LayoutRuleError, run_stage_f
from .validate import MIN_FONT_PT, validate_page_design, validate_page_geometry

__all__ = [
    "ARTIFACT",
    "CANONICAL_LAYOUT_IDS",
    "FitResult",
    "LEGACY_LAYOUT_ID",
    "LayoutRuleError",
    "MAX_PLACEHOLDER_AREA_RATIO",
    "MIN_FONT_PT",
    "SlotCapacity",
    "WATERMARK_TEXT",
    "estimate_slot_capacity",
    "estimate_text_height_in",
    "fit_text_to_box",
    "normalize_layout_id",
    "render_schematic_preview",
    "run_stage_f",
    "select_deck_layouts",
    "slide_content_area_in2",
    "validate_page_design",
    "validate_page_geometry",
    "wrap_mixed_text",
]
