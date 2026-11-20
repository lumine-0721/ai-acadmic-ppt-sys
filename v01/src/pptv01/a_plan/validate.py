"""Deterministic post-validation for stage A (plan section 2.1 / 6 step 2).

Rejects: invented page counts / durations, page-structure locks, conclusion
style claims, and inconsistent ``defaults_applied``. Everything that the user
did state (e.g. "不超过5页", "10分钟") is allowed to appear.
"""

from __future__ import annotations

from pptv01 import textcheck
from pptv01.contracts import ProductionPlan

# Fields that may be defaulted; page count / duration are NEVER defaultable.
DEFAULTABLE_FIELDS = {"purpose", "audience", "language", "style", "topic", "content_direction"}
FORBIDDEN_DEFAULT_FIELDS = {
    "page_count", "pages", "slide_count", "num_slides", "duration", "duration_minutes",
    "length", "页数", "时长", "时间", "slides",
}


class PlanRuleError(ValueError):
    """The production plan violates a stage-A rule."""


def _free_text(plan: ProductionPlan) -> dict[str, str]:
    out = {
        "topic": plan.topic,
        "content_direction": plan.content_direction,
        "purpose": plan.purpose,
        "audience": plan.audience,
        "style": plan.style,
    }
    for i, c in enumerate(plan.constraints):
        out[f"constraints[{i}]"] = c
    for i, q in enumerate(plan.open_questions):
        out[f"open_questions[{i}]"] = q
    for i, d in enumerate(plan.defaults_applied):
        out[f"defaults_applied[{i}].value"] = d.value
        out[f"defaults_applied[{i}].rationale"] = d.rationale
    return out


def validate_plan(plan: ProductionPlan) -> list[str]:
    """Return a list of rule violations (empty list = acceptable)."""
    utter = plan.source_utterance
    problems: list[str] = []

    for where, text in _free_text(plan).items():
        for p in textcheck.ungrounded_counts(text, utter):
            problems.append(f"{where}: {p}")
        for p in textcheck.structure_locks(text, utter):
            problems.append(f"{where}: {p}")
    for p in textcheck.unsourced_claims(plan.content_direction, utter):
        problems.append(f"content_direction: {p}; stage A may only give directions, not conclusions")
    for i, q in enumerate(plan.open_questions):
        for p in textcheck.unsourced_claims(q, utter):
            problems.append(f"open_questions[{i}]: {p}")

    for i, d in enumerate(plan.defaults_applied):
        if d.field.strip().lower() in FORBIDDEN_DEFAULT_FIELDS:
            problems.append(
                f"defaults_applied[{i}]: {d.field!r} must not be defaulted; page count and "
                "duration are only allowed when the user stated them"
            )
        elif d.field not in DEFAULTABLE_FIELDS:
            problems.append(
                f"defaults_applied[{i}]: unknown field {d.field!r}; use one of {sorted(DEFAULTABLE_FIELDS)}"
            )
    return problems


def check_plan(plan: ProductionPlan) -> ProductionPlan:
    problems = validate_plan(plan)
    if problems:
        raise PlanRuleError("production plan rejected:\n- " + "\n- ".join(problems))
    return plan
