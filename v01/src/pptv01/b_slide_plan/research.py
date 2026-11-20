"""Research hook for stage B.

*** STUB INTERFACE — no research is performed in this batch. ***

The plan (section 2.3) wants B to do "necessary retrieval and research". That
needs a real search provider (e.g. Crossref / web search) and is a later step.
Until then ``NullResearch`` returns ``None`` and the slide plan is made from the
model's background knowledge ONLY, so its key_points stay questions/things to
explain, never proven claims. Nothing here fabricates a research brief.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from pptv01.contracts import ProductionPlan


@dataclass
class ResearchNote:
    text: str
    source_url: str
    source_name: str


@dataclass
class ResearchBrief:
    notes: list[ResearchNote] = field(default_factory=list)

    def as_prompt_text(self) -> str:
        return "\n".join(f"- {n.text} (来源: {n.source_name} {n.source_url})" for n in self.notes)


class ResearchProvider(Protocol):
    def brief(self, plan: ProductionPlan) -> ResearchBrief | None: ...


class NullResearch:
    """STUB: performs no research."""

    def brief(self, plan: ProductionPlan) -> ResearchBrief | None:
        return None
