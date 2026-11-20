"""Deterministic checks for stage C: every requirement must trace to a page and a
B need, say what the asset does on the page, and say what counts as acceptable."""

from __future__ import annotations

import re
from typing import Mapping

from pptv01 import textcheck
from pptv01.contracts import (
    AssetRequirements,
    AssetType,
    Priority,
    ProductionPlan,
    ResearchNote,
    SlidePlan,
    Verification,
)

GENERIC_PURPOSE = re.compile(r"装饰(?!艺术|风格|主义|性|构件|元素|纹)|美化|点缀|增加美观|丰富页面|配几张图|配图|相关图片|相关素材|随便|示意即可|占位")
GENERIC_HINTS = {"图片", "图", "素材", "照片", "相关图片", "相关素材", "image", "images", "picture", "photo", "photos"}
LICENSE_RE = re.compile(r"许可|授权|license|licence|合法|版权|可商用|CC\b|公有领域|public domain|来源明确", re.I)
PROVENANCE_RE = re.compile(r"来源|出处|DOI|可核验|可追溯|稳定链接|source", re.I)
RELEVANCE_RE = re.compile(r"相关|对应|匹配|直接|能(?:看出|说明|支撑|解释)|relevan|match", re.I)
VERIFY_RE = re.compile(r"核实|核验|确认|确实|真实存在|verify|verified", re.I)
VISUAL_TYPES = {AssetType.image, AssetType.icon, AssetType.diagram, AssetType.chart_data, AssetType.table}
MAX_REQUIREMENTS = 80


def need_catalog(slide_plan: SlidePlan) -> dict[str, tuple[str, "AssetType", list]]:
    """need_ref -> (page_id, type, targets), numbering each page's asset_needs from 1."""
    out = {}
    for p in slide_plan.pages:
        for i, n in enumerate(p.asset_needs, start=1):
            out[f"{p.page_id}#{i}"] = (p.page_id, n.type, n.targets)
    return out


def validate_requirements(
    reqs: AssetRequirements,
    slide_plan: SlidePlan,
    production: ProductionPlan,
    notes: Mapping[str, ResearchNote] | None = None,
) -> list[str]:
    problems: list[str] = []
    utter = production.source_utterance
    cat = need_catalog(slide_plan)
    sourced = "\n".join(f"{n.title}\n{n.text}" for n in (notes or {}).values())
    pages = {p.page_id for p in slide_plan.pages}

    if len(reqs.requirements) > MAX_REQUIREMENTS:
        problems.append(f"{len(reqs.requirements)} requirements is unreasonable (max {MAX_REQUIREMENTS})")

    covered: set[str] = set()
    for r in reqs.requirements:
        w = r.asset_id
        unknown = [n for n in r.need_refs if n not in cat]
        if unknown:
            problems.append(f"{w}: need_refs {unknown} do not exist in the slide plan (C may not invent needs)")
        known = [n for n in r.need_refs if n in cat]
        covered.update(known)
        bad_pages = [p for p in r.page_ids if p not in pages]
        if bad_pages:
            problems.append(f"{w}: page_ids {bad_pages} are not in the slide plan")
        if known:
            need_pages = {cat[n][0] for n in known}
            if set(r.page_ids) != need_pages:
                problems.append(f"{w}: page_ids {sorted(r.page_ids)} must equal the pages of its need_refs {sorted(need_pages)}")
            wrong_type = sorted({cat[n][1].value for n in known if cat[n][1] is not r.type})
            if wrong_type:
                problems.append(f"{w}: type {r.type.value} differs from the type of the B need(s) {wrong_type}; "
                                "C refines needs, it does not change their type")
            allowed_targets = {t.name.casefold(): t for n in known for t in cat[n][2]}
            for t in r.targets:
                if t.name.casefold() not in allowed_targets:
                    problems.append(f"{w}: target {t.name!r} is not among the targets of its need_refs (C may not invent targets)")
                else:
                    b = allowed_targets[t.name.casefold()]
                    if (b.verification, b.source_refs) != (t.verification, t.source_refs):
                        problems.append(f"{w}: target {t.name!r} must keep B's verification state ({b.verification.value})")

        if len(r.purpose.strip()) < 8 or GENERIC_PURPOSE.search(r.purpose):
            problems.append(f"{w}: purpose {r.purpose!r} is empty/generic; say what this asset does on the page")
        hints = [h.strip().lower() for h in r.query_hints]
        if len(set(hints)) != len(hints):
            problems.append(f"{w}: duplicate query_hints")
        if all(h in GENERIC_HINTS or len(h) < 2 for h in hints):
            problems.append(f"{w}: query_hints are too generic to search with: {r.query_hints}")
        if len(r.acceptance) < 2:
            problems.append(f"{w}: acceptance needs at least 2 concrete criteria")
        acc = "\n".join(r.acceptance)
        if r.type in VISUAL_TYPES and not LICENSE_RE.search(acc):
            problems.append(f"{w}: acceptance for {r.type.value} must say it has to be legally usable (license/permission)")
        if r.type not in VISUAL_TYPES and not PROVENANCE_RE.search(acc):
            problems.append(f"{w}: acceptance for {r.type.value} must require a verifiable source (DOI/link/citation)")
        if not RELEVANCE_RE.search(acc):
            problems.append(f"{w}: acceptance must include a relevance criterion tied to the page")
        if any(t.verification is Verification.to_verify for t in r.targets) and not VERIFY_RE.search(acc):
            problems.append(f"{w}: has to_verify targets, so acceptance must require verifying them (核实)")

        for where, text in [(f"{w}.purpose", r.purpose), *((f"{w}.query_hints[{i}]", h) for i, h in enumerate(r.query_hints)),
                            *((f"{w}.acceptance[{i}]", a) for i, a in enumerate(r.acceptance))]:
            for pr in textcheck.ungrounded_counts(text, utter):
                problems.append(f"{where}: {pr}")
            for pr in textcheck.unsourced_claims(text, utter, sourced):
                problems.append(f"{where}: {pr}")

    dropped = {d.need_ref for d in reqs.dropped_needs}
    for d in reqs.dropped_needs:
        if d.need_ref not in cat:
            problems.append(f"dropped need {d.need_ref} does not exist in the slide plan")
    missing = sorted(set(cat) - covered - dropped)
    if missing:
        problems.append(f"B asset_needs without a requirement and not dropped (with reason): {missing}")
    if reqs.requirements and not any(r.priority is Priority.required for r in reqs.requirements):
        problems.append("at least one requirement must be priority=required")
    if cat and not reqs.requirements:
        problems.append("slide plan has asset_needs but no requirements were produced")
    return problems
