"""Deck renderers (protocol + real Office COM implementations; never mocks)."""

from .protocol import (
    RENDERER_UNAVAILABLE_EXIT_CODE,
    BuildResult,
    DeckDump,
    DeckRenderer,
    ExportResult,
    MeasureResult,
    RenderExecutionError,
    RendererInfo,
    RendererUnavailableError,
    ShapeDumpEntry,
    SlideDumpEntry,
    TextMeasurement,
)

__all__ = [
    "BuildResult",
    "DeckDump",
    "DeckRenderer",
    "ExportResult",
    "MeasureResult",
    "RENDERER_UNAVAILABLE_EXIT_CODE",
    "RenderExecutionError",
    "RendererInfo",
    "RendererUnavailableError",
    "ShapeDumpEntry",
    "SlideDumpEntry",
    "TextMeasurement",
]
