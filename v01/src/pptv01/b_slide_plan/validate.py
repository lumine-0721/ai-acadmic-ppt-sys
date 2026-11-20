from __future__ import annotations

from typing import Mapping

from pptv01 import textcheck
from pptv01.contracts import ProductionPlan, ResearchNote, SlidePlan, SlideStatus, Verification

MAX_PAGES = 60


class SlidePlanRuleError(ValueError):
    pass


def _cited_text(notes: Mapping[str, ResearchNote], refs: list[str]) -> str:
    return "\n".join(f"{notes[r].title}\n{notes[r].text}" for r in refs if r in notes)


def validate_slide_plan(
    plan: SlidePlan, production: ProductionPlan, notes: Mapping[str, ResearchNote] | None = None
) -> list[str]:
    """Rule violations of a stage-B output (empty list = acceptable).

    ``notes`` are the retrieved research notes (id -> note); empty/None when no research exists."""
    notes = notes or {}
    problems: list[str] = []
    if plan.status is not SlideStatus.provisional:
        problems.append("stage B may only output status=provisional")
    n = len(plan.pages)
    if n > MAX_PAGES:
        problems.append(f"{n} pages is unreasonable (max {MAX_PAGES})")

    utter = production.source_utterance
    limit = textcheck.page_limit(utter)
    if limit:
        kind, k = limit
        if kind == "max" and n > k:
            problems.append(f"user limited the deck to at most {k} pages, plan has {n}")
        if kind == "exact" and n != k:
            problems.append(f"user asked for {k} pages, plan has {n}")

    for pr in textcheck.ungrounded_counts(plan.narrative, utter):
        problems.append(f"narrative: {pr}")
    for pr in textcheck.unsourced_claims(plan.narrative, utter):
        problems.append(f"narrative: {pr}; the narrative may not state facts")

    for p in plan.pages:
        unknown = [r for r in p.source_refs if r not in notes]
        if unknown:
            problems.append(
                f"{p.page_id}.source_refs {unknown}: no such research notes"
                + ("" if notes else " (no research was performed)")
            )
        page_refs = list(p.source_refs)
        for i, a in enumerate(p.asset_needs):
            for j, tg in enumerate(a.targets):
                where = f"{p.page_id}.asset_needs[{i}].targets[{j}]"
                bad = [r for r in tg.source_refs if r not in notes]
                if bad:
                    problems.append(f"{where} {tg.name!r}: source_refs {bad} are not retrieved notes"
                                    + ("" if notes else " (no research was performed)"))
                elif tg.verification is Verification.verified:
                    cited = _cited_text(notes, tg.source_refs).casefold()
                    if tg.name.casefold() not in cited:
                        problems.append(
                            f"{where} {tg.name!r}: marked verified but its name does not appear in the cited "
                            "source notes; keep it to_verify or cite the right note"
                        )
                page_refs += [r for r in tg.source_refs if r in notes]
        cited_all = _cited_text(notes, page_refs)

        texts: dict[str, str] = {
            f"{p.page_id}.title": p.title, f"{p.page_id}.goal": p.goal,
            f"{p.page_id}.content_summary": p.content_summary, f"{p.page_id}.visual_intent": p.visual_intent,
        }
        for i, k_ in enumerate(p.key_points):
            texts[f"{p.page_id}.key_points[{i}]"] = k_
        for i, a in enumerate(p.asset_needs):
            texts[f"{p.page_id}.asset_needs[{i}].description"] = a.description
            for j, tg in enumerate(a.targets):
                texts[f"{p.page_id}.asset_needs[{i}].targets[{j}]"] = tg.name
        for where, text in texts.items():
            for pr in textcheck.ungrounded_counts(text, utter):
                problems.append(f"{where}: {pr}")
            for pr in textcheck.unsourced_claims(text, utter, cited_all):
                problems.append(
                    f"{where}: {pr}; state it only if a cited research note (source_refs) literally contains it"
                )

    titles = [p.title for p in plan.pages]
    if len(set(titles)) != len(titles):
        problems.append("duplicate page titles")
    return problems
