"""Backend-agnostic DeckRenderer protocol and structured result types.

Hard rules:
  - Package code contains ONLY this protocol and real renderer implementations
    (e.g., PowerPointComRenderer on Windows).
  - NO mock/fake renderer is permitted in package code; fake COM fixtures live
    exclusively under ``tests/``.
  - Missing PowerPoint/Office raises ``RendererUnavailableError`` loudly — never
    silently falls back to fake success or python-pptx writing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from pptv01.contracts import DeckOps

RENDERER_UNAVAILABLE_EXIT_CODE = 5


class RendererUnavailableError(RuntimeError):
    """Raised when the required Office/PowerPoint runtime is unavailable."""


class RenderExecutionError(RuntimeError):
    """Raised when the Office renderer fails during build/export/measure."""


class _ProtoModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RendererInfo(_ProtoModel):
    name: str
    available: bool
    platform: str
    version: str = ""
    build: str = ""
    excel_available: bool = False
    excel_version: str = ""
    excel_build: str = ""
    fonts: list[str] = Field(default_factory=list)
    reason: str = ""


class ShapeDumpEntry(_ProtoModel):
    shape_id: int
    name: str
    shape_type: int
    x_pt: float
    y_pt: float
    w_pt: float
    h_pt: float
    text: str = ""
    has_chart: bool = False
    has_table: bool = False
    is_placeholder: bool = False
    placeholder_type: int | None = None
    is_connector: bool = False
    begin_connected: bool = False
    end_connected: bool = False
    alt_text: str = ""
    bound_id: str | None = None


class SlideDumpEntry(_ProtoModel):
    slide_index: int
    page_id: str
    notes: str = ""
    shapes: list[ShapeDumpEntry] = Field(default_factory=list)


class DeckDump(_ProtoModel):
    schema_version: Literal["0.1"] = "0.1"
    run_id: str
    renderer_name: str
    renderer_version: str
    renderer_build: str = ""
    slide_width_pt: float
    slide_height_pt: float
    slides: list[SlideDumpEntry] = Field(default_factory=list)


class BuildResult(_ProtoModel):
    pptx_path: Path
    dump_path: Path
    slide_count: int
    renderer_name: str
    renderer_version: str
    renderer_build: str = ""
    build_notes: list[str] = Field(default_factory=list)


class ExportResult(_ProtoModel):
    png_paths: list[Path]
    pdf_path: Path | None = None
    renderer_name: str
    renderer_version: str
    renderer_build: str = ""


class TextMeasurement(_ProtoModel):
    slide_index: int
    page_id: str
    shape_id: int
    shape_name: str
    allocated_w_pt: float
    allocated_h_pt: float
    bound_w_pt: float
    bound_h_pt: float
    overflow_h_pt: float = 0.0
    overflow_w_pt: float = 0.0
    font_name: str = ""
    font_name_fareast: str = ""
    font_size_pt: float | None = None
    auto_size: int | None = None
    text_length: int = 0


class MeasureResult(_ProtoModel):
    renderer_name: str
    renderer_version: str
    renderer_build: str = ""
    measurements: list[TextMeasurement] = Field(default_factory=list)
    presentation_fonts: list[str] = Field(default_factory=list)


@runtime_checkable
class DeckRenderer(Protocol):
    name: str

    def preflight(self) -> RendererInfo:
        """Probe the renderer runtime; raise RendererUnavailableError if unusable."""
        ...

    def build(self, ops: DeckOps, run_dir: Path) -> BuildResult:
        """Execute DeckOps to produce deck.pptx and deck_dump.json."""
        ...

    def export(self, pptx: Path, out_dir: Path, width_px: int = 1920) -> ExportResult:
        """Export slides to render/slide_001.png... and render/deck.pdf."""
        ...

    def measure(self, pptx: Path, page_ids: list[str] | None = None) -> MeasureResult:
        """Measure live rendered text bounds, fonts, and autosize states."""
        ...
