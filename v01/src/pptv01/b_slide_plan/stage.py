from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field

from pptv01.contracts import (
    AssetNeed,
    ResearchNote,
    ResearchRecord,
    ResearchStatus,
    SCHEMA_VERSION,
    NonEmptyStr,
    ProductionPlan,
    SlidePage,
    SlidePlan,
    SlideStatus,
    SourceRef,
    write_contract,
)
from pptv01.llm import ChatClient, complete_json

from .prompt import NO_BRIEF, SYSTEM_PROMPT, USER_TEMPLATE
from pptv01.retrieval import DisabledResearch, ResearchProvider
from .validate import SlidePlanRuleError, validate_slide_plan

ARTIFACT = "slide_plan.json"
RESEARCH_ARTIFACT = "research_brief.json"


class DraftPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: NonEmptyStr
    goal: NonEmptyStr
    key_points: list[NonEmptyStr] = Field(min_length=1)
    content_summary: NonEmptyStr
    visual_intent: NonEmptyStr
    asset_needs: list[AssetNeed] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)


class DraftSlidePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    narrative: NonEmptyStr
    pages: list[DraftPage] = Field(min_length=1)


def build_slide_plan(raw: Any, production: ProductionPlan, notes: Mapping[str, ResearchNote] | None = None) -> SlidePlan:
    draft = DraftSlidePlan.model_validate(raw)
    # IDs and status are assigned by code, never by the model: S001.. + provisional.
    pages = [
        SlidePage(page_id=f"S{i:03d}", status=SlideStatus.provisional, **p.model_dump())
        for i, p in enumerate(draft.pages, start=1)
    ]
    plan = SlidePlan(
        schema_version=SCHEMA_VERSION,
        run_id=production.run_id,
        status=SlideStatus.provisional,
        narrative=draft.narrative,
        pages=pages,
        revision_log=[],
    )
    problems = validate_slide_plan(plan, production, notes)
    if problems:
        raise SlidePlanRuleError("slide plan rejected:\n- " + "\n- ".join(problems))
    return plan


class ResearchFailed(RuntimeError):
    """Research was enabled but every query failed (e.g. no network). B does not
    silently continue without research: disable it explicitly (PPTV01_RESEARCH=none)
    if you want a research-free run, which is then recorded as such."""

    def __init__(self, record: ResearchRecord):
        super().__init__(f"research failed: {record.summary}. Set PPTV01_RESEARCH=none to run without research (recorded).")
        self.record = record


def _brief_text(record: ResearchRecord) -> str:
    if not record.notes:
        return NO_BRIEF
    lines = []
    for n in record.notes:
        text = (n.text[:400] + "…") if len(n.text) > 400 else n.text
        lines.append(f"[{n.note_id}] {n.title} — {n.source_name} <{n.source_url}>\n    {text}")
    return "\n".join(lines)


def run_stage_b(
    production: ProductionPlan,
    client: ChatClient,
    run_dir: Path | None = None,
    research: ResearchProvider | None = None,
) -> tuple[SlidePlan, list[dict[str, Any]], ResearchRecord]:
    """Research (per A's retrieval_brief) -> provisional slide plan. Always writes research_brief.json."""
    research = research or DisabledResearch("no research provider supplied")
    record = research.research(production)
    if run_dir is not None:
        write_contract(record, run_dir / RESEARCH_ARTIFACT)
    if record.status is ResearchStatus.failed:
        raise ResearchFailed(record)
    notes = {n.note_id: n for n in record.notes}
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": USER_TEMPLATE.format(
                plan_json=production.model_dump_json(indent=2), brief=_brief_text(record),
            ),
        },
    ]
    res = complete_json(client, messages, lambda raw: build_slide_plan(raw, production, notes))
    plan: SlidePlan = res.value
    if run_dir is not None:
        write_contract(plan, run_dir / ARTIFACT)
    return plan, res.attempts, record
