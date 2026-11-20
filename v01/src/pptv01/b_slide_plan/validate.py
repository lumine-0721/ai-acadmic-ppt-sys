from __future__ import annotations

from pptv01 import textcheck
from pptv01.contracts import ProductionPlan, SlidePlan, SlideStatus, Verification

MAX_PAGES = 60


class SlidePlanRuleError(ValueError):
    pass


def validate_slide_plan(plan: SlidePlan, production: ProductionPlan, *, has_research: bool = False) -> list[str]:
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

    texts: dict[str, str] = {"narrative": plan.narrative}
    for p in plan.pages:
        for f in ("title", "goal", "content_summary", "visual_intent"):
            texts[f"{p.page_id}.{f}"] = getattr(p, f)
        for i, k_ in enumerate(p.key_points):
            texts[f"{p.page_id}.key_points[{i}]"] = k_
        for i, a in enumerate(p.asset_needs):
            texts[f"{p.page_id}.asset_needs[{i}].description"] = a.description
            for j, tg in enumerate(a.targets):
                texts[f"{p.page_id}.asset_needs[{i}].targets[{j}]"] = tg.name
                if not has_research and (tg.verification is Verification.verified or tg.source_refs):
                    problems.append(
                        f"{p.page_id}.asset_needs[{i}].targets[{j}] {tg.name!r}: cannot be 'verified' / cite "
                        "sources when no research was performed; candidates stay to_verify"
                    )

    for where, text in texts.items():
        for pr in textcheck.ungrounded_counts(text, utter):
            problems.append(f"{where}: {pr}")
        if not has_research:
            for pr in textcheck.unsourced_claims(text, utter):
                problems.append(f"{where}: {pr}; no research brief exists, so this cannot be stated as fact")

    titles = [p.title for p in plan.pages]
    if len(set(titles)) != len(titles):
        problems.append("duplicate page titles")
    return problems
