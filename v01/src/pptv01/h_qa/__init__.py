from .inspect import inspect_deck
from .occupancy import (
    CELL_VOID_MAX_FRACTION,
    CELL_VOID_MAX_IN,
    ROLE_EXEMPT_REASONS,
    SPARSE_TEXT_COVERAGE,
    VOID_MAX_IN,
    CellInput,
    check_deck_occupancy,
    infer_page_role,
    score_cell_occupancy,
    score_occupancy,
)
from .render import (
    RenderExecutionError,
    RendererUnavailableError,
    find_render_tools,
    has_render_tools,
    render_pptx_to_pngs,
)
from .repair import apply_repairs
from .stage import ARTIFACT, MAX_REPAIR_ROUNDS, RENDER_DIR, run_stage_h

__all__ = [
    "ARTIFACT",
    "CELL_VOID_MAX_FRACTION",
    "CELL_VOID_MAX_IN",
    "CellInput",
    "MAX_REPAIR_ROUNDS",
    "RENDER_DIR",
    "ROLE_EXEMPT_REASONS",
    "RenderExecutionError",
    "RendererUnavailableError",
    "SPARSE_TEXT_COVERAGE",
    "VOID_MAX_IN",
    "apply_repairs",
    "check_deck_occupancy",
    "find_render_tools",
    "has_render_tools",
    "infer_page_role",
    "inspect_deck",
    "render_pptx_to_pngs",
    "run_stage_h",
    "score_cell_occupancy",
    "score_occupancy",
]
