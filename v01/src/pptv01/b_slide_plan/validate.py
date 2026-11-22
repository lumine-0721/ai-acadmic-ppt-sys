from __future__ import annotations

import re
from typing import Mapping

from pptv01 import textcheck
from pptv01.contracts import ProductionPlan, ResearchNote, SlidePlan, SlideStatus, Verification

MAX_PAGES = 60

# Conclusion-headline & ghost-deck concept adapted from Gabberflast/academic-pptx-skill (MIT);
# structural role & grouping vocabulary inspired by allweonedev/presentation-ai (MIT) & hugohe3/ppt-master (MIT).
_BARE_CATEGORY_PREFIX_RE = re.compile(
    r"^(?:关于|简介|介绍|概述|概览|目录|引言|背景介绍|内容介绍|整体介绍|总体概述|"
    r"(?:overview|introduction|agenda|background)(?:\s+of)?\b)\s*[:：\-]?",
    re.I,
)
_BARE_CATEGORY_SUFFIX_RE = re.compile(r"^[\u4e00-\u9fffA-Za-z0-9\s\-]{1,12}(?:介绍|概述|简介|目录)$")
_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?]+|(?<=\S)\.(?:\s+|$)")
_PUNCT_STRIP_RE = re.compile(r"[\s，,。\.；;：:！!？?\-\n\r•]+")


def _norm_compare(text: str) -> str:
    return _PUNCT_STRIP_RE.sub("", (text or "").casefold())


def _sentence_count(text: str) -> int:
    return sum(1 for seg in _SENTENCE_SPLIT_RE.split((text or "").strip()) if seg.strip())


def validate_ghost_deck(plan: SlidePlan) -> list[str]:
    """Verify every page has a non-duplicate, assertive sentence headline suitable for standalone reading."""
    problems: list[str] = []
    seen_headlines: dict[str, str] = {}
    for p in plan.pages:
        hl = (p.headline or "").strip()
        if not hl:
            problems.append(f"{p.page_id}.headline: missing required conclusion-sentence headline")
            continue
        core = hl.strip("。！？.!? \t\n")
        if len(core) < 6:
            problems.append(f"{p.page_id}.headline {hl!r}: too short to state a full slide takeaway")
        if _BARE_CATEGORY_PREFIX_RE.match(core) or _BARE_CATEGORY_SUFFIX_RE.match(core):
            problems.append(
                f"{p.page_id}.headline {hl!r}: write a full declarative conclusion sentence, not a bare category label"
            )
        norm_hl = _norm_compare(hl)
        if norm_hl in seen_headlines:
            problems.append(f"{p.page_id}.headline duplicates {seen_headlines[norm_hl]} ({hl!r})")
        else:
            seen_headlines[norm_hl] = p.page_id
    return problems


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
        if p.page_role is None:
            problems.append(f"{p.page_id}.page_role: required in stage B output")
        hl = (p.headline or "").strip()
        if not hl:
            problems.append(f"{p.page_id}.headline: required in stage B output")
        sp_notes = (p.speaker_notes or "").strip()
        if not sp_notes:
            problems.append(f"{p.page_id}.speaker_notes: required in stage B output")
        else:
            n_sent = _sentence_count(sp_notes)
            if not (2 <= n_sent <= 5):
                problems.append(
                    f"{p.page_id}.speaker_notes: must contain 2-5 spoken presenter sentences (got {n_sent})"
                )
            if _norm_compare(sp_notes) == _norm_compare(p.content_summary):
                problems.append(
                    f"{p.page_id}.speaker_notes: must be spoken presenter notes, not equal to planning content_summary"
                )
            kp_concat = _norm_compare("".join(p.key_points))
            if kp_concat and _norm_compare(sp_notes) == kp_concat:
                problems.append(
                    f"{p.page_id}.speaker_notes: must expand on the slide naturally, not concatenate key_points verbatim"
                )

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
            f"{p.page_id}.title": p.title,
            f"{p.page_id}.goal": p.goal,
            f"{p.page_id}.content_summary": p.content_summary,
            f"{p.page_id}.visual_intent": p.visual_intent,
        }
        if hl:
            texts[f"{p.page_id}.headline"] = hl
        if sp_notes:
            texts[f"{p.page_id}.speaker_notes"] = sp_notes
        for i, k_ in enumerate(p.key_points):
            texts[f"{p.page_id}.key_points[{i}]"] = k_
        for gi, g in enumerate(p.groups):
            texts[f"{p.page_id}.groups[{gi}].heading"] = g.heading
            for pi, gpt in enumerate(g.points):
                texts[f"{p.page_id}.groups[{gi}].points[{pi}]"] = gpt
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

    problems.extend(validate_ghost_deck(plan))
    titles = [p.title for p in plan.pages]
    if len(set(titles)) != len(titles):
        problems.append("duplicate page titles")
    return problems
