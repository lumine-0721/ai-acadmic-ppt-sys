from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from pptv01.contracts import (
    SCHEMA_VERSION,
    NonEmptyStr,
    ProductionPlan,
    SlidePage,
    SlidePlan,
    SlideStatus,
    write_contract,
)
from pptv01.llm import ChatClient, complete_json

from .prompt import NO_BRIEF, SYSTEM_PROMPT, USER_TEMPLATE
from .research import NullResearch, ResearchProvider
from .validate import SlidePlanRuleError, validate_slide_plan

ARTIFACT = "slide_plan.json"


class DraftPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: NonEmptyStr
    goal: NonEmptyStr
    key_points: list[NonEmptyStr] = Field(min_length=1)
    content_summary: NonEmptyStr
    visual_intent: NonEmptyStr
    asset_needs: list[NonEmptyStr] = Field(default_factory=list)


class DraftSlidePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    narrative: NonEmptyStr
    pages: list[DraftPage] = Field(min_length=1)


def build_slide_plan(raw: Any, production: ProductionPlan, *, has_research: bool) -> SlidePlan:
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
    problems = validate_slide_plan(plan, production, has_research=has_research)
    if problems:
        raise SlidePlanRuleError("slide plan rejected:\n- " + "\n- ".join(problems))
    return plan


def run_stage_b(
    production: ProductionPlan,
    client: ChatClient,
    run_dir: Path | None = None,
    research: ResearchProvider | None = None,
) -> tuple[SlidePlan, list[dict[str, Any]]]:
    research = research or NullResearch()
    brief = research.brief(production)
    has_research = brief is not None and bool(brief.notes)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": USER_TEMPLATE.format(
                plan_json=production.model_dump_json(indent=2),
                brief=brief.as_prompt_text() if has_research else NO_BRIEF,
            ),
        },
    ]
    res = complete_json(
        client, messages, lambda raw: build_slide_plan(raw, production, has_research=has_research)
    )
    plan: SlidePlan = res.value
    if run_dir is not None:
        write_contract(plan, run_dir / ARTIFACT)
    return plan, res.attempts
