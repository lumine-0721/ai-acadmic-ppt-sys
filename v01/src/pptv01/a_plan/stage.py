from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from pptv01.contracts import (
    SCHEMA_VERSION,
    DefaultApplied,
    LangTag,
    NonEmptyStr,
    ProductionPlan,
    Token,
    write_contract,
)
from pptv01.llm import ChatClient, complete_json

from .prompt import SYSTEM_PROMPT, USER_TEMPLATE
from .validate import check_plan

ARTIFACT = "production_plan.json"


class DraftPlan(BaseModel):
    """What the model is allowed to write; run_id / source_utterance are injected by code."""

    model_config = ConfigDict(extra="forbid")
    topic: NonEmptyStr
    purpose: Token
    audience: Token
    language: LangTag
    style: Token
    constraints: list[NonEmptyStr] = []
    defaults_applied: list[DefaultApplied] = []
    content_direction: NonEmptyStr
    open_questions: list[NonEmptyStr] = []


def build_plan(raw: Any, *, run_id: str, utterance: str) -> ProductionPlan:
    draft = DraftPlan.model_validate(raw)
    plan = ProductionPlan(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        source_utterance=utterance,
        **draft.model_dump(),
    )
    return check_plan(plan)


def run_stage_a(
    utterance: str, run_id: str, client: ChatClient, run_dir: Path | None = None
) -> tuple[ProductionPlan, list[dict[str, Any]]]:
    """Utterance -> ProductionPlan (validated, one repair retry). Writes the file if run_dir given."""
    utterance = utterance.strip()
    if not utterance:
        raise ValueError("empty utterance")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_TEMPLATE.format(utterance=utterance)},
    ]
    res = complete_json(client, messages, lambda raw: build_plan(raw, run_id=run_id, utterance=utterance))
    plan: ProductionPlan = res.value
    if run_dir is not None:
        write_contract(plan, run_dir / ARTIFACT)
    return plan, res.attempts
