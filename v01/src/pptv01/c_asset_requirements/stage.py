from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field

from pptv01.contracts import (
    SCHEMA_VERSION,
    AssetRequirement,
    AssetRequirements,
    AssetTarget,
    AssetType,
    DroppedNeed,
    NeedRef,
    NonEmptyStr,
    PageId,
    Priority,
    ProductionPlan,
    ResearchNote,
    SlidePlan,
    write_contract,
)
from pptv01.bivia_judge import BiviaJudge, format_bivia_probability, record_bivia_outcome
from pptv01.llm import ChatClient, complete_json

from .prompt import SYSTEM_PROMPT, USER_TEMPLATE
from .validate import need_catalog, validate_requirements

ARTIFACT = "asset_requirements.json"


class RequirementRuleError(ValueError):
    pass


class DraftRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    need_refs: list[NeedRef] = Field(min_length=1)
    page_ids: list[PageId] = Field(min_length=1)
    type: AssetType
    purpose: NonEmptyStr
    targets: list[NonEmptyStr] = Field(default_factory=list)
    query_hints: list[NonEmptyStr] = Field(min_length=1, max_length=8)
    priority: Priority
    acceptance: list[NonEmptyStr] = Field(min_length=1)


class DraftRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirements: list[DraftRequirement] = Field(default_factory=list)
    dropped_needs: list[DroppedNeed] = Field(default_factory=list)


def _needs_view(slide_plan: SlidePlan) -> str:
    pages = []
    for p in slide_plan.pages:
        pages.append({
            "page_id": p.page_id, "title": p.title, "goal": p.goal, "key_points": p.key_points,
            "visual_intent": p.visual_intent,
            "asset_needs": [
                {"need_ref": f"{p.page_id}#{i}", "type": n.type.value, "description": n.description,
                 "targets": [{"name": t.name, "verification": t.verification.value} for t in n.targets]}
                for i, n in enumerate(p.asset_needs, start=1)
            ],
        })
    return json.dumps(pages, ensure_ascii=False, indent=2)


_BIVIA_C_UNCERTAIN_NOTE = "待补充：Bivia 复核弃答或置信度不足（needs_review，未算通过），缺失时须由用户补充真实来源素材"


def build_requirements(
    raw: Any,
    production: ProductionPlan,
    slide_plan: SlidePlan,
    notes: Mapping[str, ResearchNote] | None = None,
    *,
    bivia: BiviaJudge | None = None,
    bivia_stats_out: dict[str, int] | None = None,
) -> AssetRequirements:
    draft = DraftRequirements.model_validate(raw)
    cat = need_catalog(slide_plan)
    out: list[AssetRequirement] = []
    for i, d in enumerate(draft.requirements, start=1):
        # carry B's targets (with B's verification state) by name; unknown names are left for validation to reject
        pool = {t.name.casefold(): t for n in d.need_refs if n in cat for t in cat[n][2]}
        targets = [pool[n.casefold()].model_copy() if n.casefold() in pool else AssetTarget(name=n) for n in d.targets]
        out.append(AssetRequirement(
            asset_id=f"A{i:03d}", page_ids=d.page_ids, need_refs=d.need_refs, type=d.type, purpose=d.purpose,
            targets=targets, query_hints=d.query_hints, priority=d.priority, acceptance=d.acceptance,
        ))
    reqs = AssetRequirements(
        schema_version=SCHEMA_VERSION, run_id=production.run_id, requirements=out,
        dropped_needs=draft.dropped_needs,
    )
    problems = validate_requirements(reqs, slide_plan, production, notes)
    if problems:
        raise RequirementRuleError("asset requirements rejected:\n- " + "\n- ".join(problems))

    if bivia is not None:
        vetoed: list[str] = []
        updated_reqs: list[AssetRequirement] = []
        for r in reqs.requirements:
            state = (
                f"asset_id: {r.asset_id}\n"
                f"type: {r.type.value}\n"
                f"purpose: {r.purpose}\n"
                f"acceptance:\n" + "\n".join(f"- {a}" for a in r.acceptance)
            )
            verdict = bivia.judge_boolean(
                "Is this asset requirement strictly free of any instruction or permission to draw natively, "
                "self-draw, or fabricate missing visuals or data?",
                state,
            )
            record_bivia_outcome(bivia_stats_out, verdict.outcome)
            if verdict.outcome == "veto":
                vetoed.append(
                    f"{r.asset_id}: bivia veto ({format_bivia_probability(verdict.probability)}): "
                    "requirement appears to allow fabricating or drawing missing assets — 待补充真实来源素材"
                )
            elif verdict.outcome == "uncertain":
                acc = list(r.acceptance)
                if _BIVIA_C_UNCERTAIN_NOTE not in acc:
                    acc.append(_BIVIA_C_UNCERTAIN_NOTE)
                updated_reqs.append(r.model_copy(update={"acceptance": acc}))
            else:
                # "pass" -> keep as-is; "not_run" -> fail-open (never blocks pipeline)
                updated_reqs.append(r)
        if vetoed:
            raise RequirementRuleError("asset requirements rejected:\n- " + "\n- ".join(vetoed))
        reqs = reqs.model_copy(update={"requirements": updated_reqs})

    return reqs


def run_stage_c(
    production: ProductionPlan,
    slide_plan: SlidePlan,
    client: ChatClient,
    run_dir: Path | None = None,
    notes: Mapping[str, ResearchNote] | None = None,
    *,
    bivia: BiviaJudge | None = None,
    bivia_stats_out: dict[str, int] | None = None,
) -> tuple[AssetRequirements, list[dict[str, Any]]]:
    notes = notes or {}
    own_bivia = False
    eff_bivia = bivia
    if eff_bivia is None:
        eff_bivia = BiviaJudge.from_env()
        own_bivia = eff_bivia is not None
    try:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_TEMPLATE.format(
                topic=production.topic, audience=production.audience, language=production.language,
                style=production.style, needs_json=_needs_view(slide_plan),
                notes="\n".join(f"- [{n.note_id}] {n.title}" for n in notes.values()) or "（无）",
            )},
        ]
        res = complete_json(
            client,
            messages,
            lambda raw: build_requirements(
                raw,
                production,
                slide_plan,
                notes,
                bivia=eff_bivia,
                bivia_stats_out=bivia_stats_out,
            ),
        )
        reqs: AssetRequirements = res.value
        if run_dir is not None:
            write_contract(reqs, run_dir / ARTIFACT)
        return reqs, res.attempts
    finally:
        if own_bivia and eff_bivia is not None:
            eff_bivia.close()
