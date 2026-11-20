"""Stage G compilation layer: PageDesign -> backend-agnostic DeckOps (deck_ops.json)."""

from .compile_ops import (
    ARTIFACT_OPS,
    AttributionMissingError,
    DiagramOverflowError,
    OpsCompileError,
    UnboundVisualError,
    compile_deck_ops,
)

__all__ = [
    "ARTIFACT_OPS",
    "AttributionMissingError",
    "DiagramOverflowError",
    "OpsCompileError",
    "UnboundVisualError",
    "compile_deck_ops",
]
