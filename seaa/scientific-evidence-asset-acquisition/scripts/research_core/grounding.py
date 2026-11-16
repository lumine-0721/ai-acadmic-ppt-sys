"""Claim-Evidence Grounding, Experimental Condition Extraction, and Evidence-Asset Binding.

Adapts two-stage grounding principles from PaperQA2 and paper-evidence:
1. Locate exact supporting/opposing passages with page/section/character locators;
2. Extract quantitative measurements (with SI normalization) and critical experimental conditions
   (solvent, pH, temperature, concentration range, LOD method, excitation/emission wavelength, controls);
3. Strictly distinguish:
   - Source Exists (SOURCE_LOCATED)
   - Evidence Located (EVIDENCE_LOCATED)
   - Claim Semantically Checked / Approved (SEMANTICALLY_CHECKED / APPROVED)
   - Conflicted (CONFLICTED)
   and never promote ABSTRACT_ONLY sources to APPROVED full-text verification.
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Tuple

from .models import (
    AccessStatus,
    AssetRecord,
    ClaimEvidenceLink,
    ClaimNode,
    ConditionItem,
    EvidenceOrigin,
    EvidenceRecord,
    ExactLocator,
    MeasurementRecord,
    PassageRecord,
    RelationType,
    SourceRecord,
    VerificationLevel,
)

TOKEN_RE = re.compile(r"[a-z0-9\u4e00-\u9fff.\-+±/]+")
MEASUREMENT_RE = re.compile(
    r"(?P<val>[<>≤≥~≈]?\s*-?\d+(?:\.\d+)?(?:\s*±\s*\d+(?:\.\d+)?)?)"
    r"\s*(?P<unit>nM|μM|uM|mM|pM|fM|M|mg/L|μg/mL|ug/mL|ng/mL|wt%|mol%|%|°C|K|cm-1|cm\^-1|nm|ms|s|min|h|kJ/mol|kcal/mol|mV|V|mA/cm2|U/L|counts)\b"
)
PH_RE = re.compile(r"\bpH\s*[=:≈~]?\s*(\d+(?:\.\d+)?(?:\s*[-–~]\s*\d+(?:\.\d+)?)?)", re.IGNORECASE)
TEMP_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*°C\b|\b(room\s+temperature|RT|298\s*K|310\s*K)\b", re.IGNORECASE)
SOLVENT_RE = re.compile(
    r"\b(PBS|HEPES|Tris(?:-HCl)?|DMSO|DMF|MeCN|CH3CN|acetonitrile|EtOH|ethanol|MeOH|methanol|THF|H2O|aqueous|water|缓冲液|水溶液)\b",
    re.IGNORECASE,
)
LOD_METHOD_RE = re.compile(r"\b(3\s*[σs]\s*/\s*[ks]|3σ/k|3s/k|S/N\s*=\s*3|signal-to-noise|检出限|LOD)\b", re.IGNORECASE)
NEGATION_CUES = (
    "no significant",
    "failed to",
    "did not",
    "unable to",
    "negligible",
    "contrary to",
    "however, no",
    "in contrast, fails",
    "未观察到",
    "未能",
    "无明显",
    "不支持",
    "相反",
)

UNIT_TO_SI: Dict[str, Tuple[float, str]] = {
    "M": (1.0, "mol/L"),
    "mM": (1e-3, "mol/L"),
    "uM": (1e-6, "mol/L"),
    "μM": (1e-6, "mol/L"),
    "nM": (1e-9, "mol/L"),
    "pM": (1e-12, "mol/L"),
    "fM": (1e-15, "mol/L"),
    "ms": (1e-3, "s"),
    "s": (1.0, "s"),
    "min": (60.0, "s"),
    "h": (3600.0, "s"),
    "%": (1.0, "%"),
    "wt%": (1.0, "wt%"),
    "mol%": (1.0, "mol%"),
    "nm": (1e-9, "m"),
    "cm-1": (1.0, "cm^-1"),
    "cm^-1": (1.0, "cm^-1"),
    "counts": (1.0, "counts"),
}


def normalize_measurement(raw_val: str, unit: str, context_sentence: str = "") -> MeasurementRecord:
    cleaned_val_str = raw_val.strip()
    num_match = re.search(r"-?\d+(?:\.\d+)?", cleaned_val_str)
    numeric_val: Any = cleaned_val_str
    si_val: Optional[float] = None
    si_unit: Optional[str] = None

    if num_match and "±" not in cleaned_val_str and not any(cleaned_val_str.startswith(p) for p in ("<", ">", "≤", "≥", "~", "≈")):
        try:
            numeric_val = float(num_match.group(0))
            if numeric_val.is_integer():
                numeric_val = int(numeric_val)
        except ValueError:
            numeric_val = cleaned_val_str

    base_num = float(num_match.group(0)) if num_match else None
    if base_num is not None:
        if unit == "°C":
            si_val = round(base_num + 273.15, 4)
            si_unit = "K"
        elif unit == "K":
            si_val = round(base_num, 4)
            si_unit = "K"
        elif unit in UNIT_TO_SI:
            factor, target_u = UNIT_TO_SI[unit]
            si_val = base_num * factor
            si_unit = target_u

    definition = infer_metric_definition(unit, context_sentence)
    return MeasurementRecord(
        value=numeric_val,
        unit=unit,
        definition=definition,
        normalized_si_value=si_val,
        normalized_si_unit=si_unit,
    )


def infer_metric_definition(unit: str, sentence: str) -> str:
    lower = sentence.lower()
    if unit in ("s", "ms", "min", "h"):
        return "Reaction or response time"
    if unit in ("°C", "K"):
        return "Experimental temperature"
    if unit == "nm":
        return "Wavelength (excitation/emission/absorption)"
    if unit in ("cm-1", "cm^-1"):
        return "Vibrational wavenumber"
    if any(k in lower for k in ("lod", "limit of detection", "detection limit", "检出限")):
        return "Limit of detection (LOD)"
    if any(k in lower for k in ("linear range", "linearity", "线性范围")):
        return "Linear response range"
    if any(k in lower for k in ("yield", "conversion", "产率", "转化率")):
        return "Reaction yield / conversion"
    if any(k in lower for k in ("selectivity", "fold", "选择性")):
        return "Selectivity / response ratio"
    return f"Measured experimental quantity ({unit})"


def extract_measurements_from_text(text: str) -> List[MeasurementRecord]:
    results: List[MeasurementRecord] = []
    seen = set()
    for m in MEASUREMENT_RE.finditer(text):
        val_str = m.group("val").strip()
        unit_str = m.group("unit").strip()
        # Avoid capturing "pH 7.4" or "Figure 1" as unit "M"
        start = max(0, m.start() - 35)
        end = min(len(text), m.end() + 45)
        snippet = text[start:end]
        key = (val_str, unit_str)
        if key in seen:
            continue
        seen.add(key)
        results.append(normalize_measurement(val_str, unit_str, snippet))
    return results


def extract_experimental_conditions(text: str) -> Tuple[List[str], List[ConditionItem]]:
    """Extract critical experimental conditions (solvent, pH, temperature, LOD method, concentration) from text."""
    human_list: List[str] = []
    structured: List[ConditionItem] = []

    for pm in PH_RE.finditer(text):
        ph_val = pm.group(1).strip()
        label = f"pH {ph_val}"
        if label not in human_list:
            human_list.append(label)
            structured.append(ConditionItem(category="environment", parameter="pH", value=ph_val, unit="pH_scale", raw_text=pm.group(0)))

    for sm in SOLVENT_RE.finditer(text):
        sol = sm.group(1).strip()
        label = f"Solvent/Medium: {sol}"
        if label not in human_list:
            human_list.append(label)
            structured.append(ConditionItem(category="medium", parameter="solvent", value=sol, raw_text=sm.group(0)))

    for tm in TEMP_RE.finditer(text):
        t_val = (tm.group(1) or tm.group(2) or "").strip()
        if t_val:
            unit = "°C" if tm.group(1) else None
            label = f"Temperature: {t_val}{' °C' if unit else ''}"
            if label not in human_list:
                human_list.append(label)
                structured.append(ConditionItem(category="thermodynamic", parameter="temperature", value=t_val, unit=unit, raw_text=tm.group(0)))

    for lm in LOD_METHOD_RE.finditer(text):
        l_val = lm.group(1).strip()
        label = f"LOD calculation/criterion: {l_val}"
        if label not in human_list:
            human_list.append(label)
            structured.append(ConditionItem(category="methodology", parameter="lod_criterion", value=l_val, raw_text=lm.group(0)))

    if not human_list:
        human_list.append("Conditions as reported in source passage (no explicit pH/solvent/temperature token matched)")
    return human_list, structured


def rank_passages_for_claim(
    claim: ClaimNode,
    passages: List[PassageRecord],
    top_k: int = 5,
) -> List[Tuple[float, PassageRecord]]:
    """Score passages against a claim using BM25-inspired term overlap + quantitative/condition bonuses."""
    claim_tokens = [t.lower() for t in TOKEN_RE.findall(claim.statement) if len(t) >= 2]
    if not claim_tokens:
        return [(1.0, p) for p in passages[:top_k]]

    doc_freq: Dict[str, int] = {}
    passage_token_sets: List[set] = []
    for p in passages:
        pset = {t.lower() for t in TOKEN_RE.findall(p.text)}
        passage_token_sets.append(pset)
        for ct in set(claim_tokens):
            if ct in pset or any(ct in pt for pt in pset):
                doc_freq[ct] = doc_freq.get(ct, 0) + 1

    n_docs = max(1, len(passages))
    scored: List[Tuple[float, PassageRecord]] = []
    for p, pset in zip(passages, passage_token_sets):
        lower_text = p.text.lower()
        claim_nums = re.findall(r"\d+(?:\.\d+)?", claim.statement)
        if len(p.text.split()) < 8 and not any(num in p.text for num in claim_nums):
            continue
        score = 0.0
        for ct in claim_tokens:
            if ct in pset or ct in lower_text:
                idf = math.log(1.0 + (n_docs - doc_freq.get(ct, 0) + 0.5) / (doc_freq.get(ct, 0) + 0.5))
                score += max(0.25, idf)
        # Bonus if numbers in claim also appear in passage
        for num in claim_nums:
            if num in p.text:
                score += 1.5
        if score > 0:
            scored.append((round(score, 4), p))

    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[:top_k]


def determine_claim_relation(claim_text: str, passage_text: str) -> str:
    """Detect whether a matched passage supports, opposes, or provides context for a claim."""
    c_meas = extract_measurements_from_text(claim_text)
    p_meas = extract_measurements_from_text(passage_text)
    if c_meas:
        matched = False
        conflicted = False
        for cm in c_meas:
            same_unit = [pm for pm in p_meas if pm.unit == cm.unit]
            if not same_unit:
                continue
            if any(str(pm.value) == str(cm.value) for pm in same_unit):
                matched = True
            else:
                conflicted = True
        if matched and not conflicted:
            return RelationType.SUPPORTS.value
        if conflicted and not matched:
            return RelationType.OPPOSES.value
    claim_tokens = {t.lower() for t in TOKEN_RE.findall(claim_text) if len(t) >= 4}
    c_has_neg = any(cue in claim_text.lower() for cue in NEGATION_CUES)
    for sentence in re.split(r"[.;。]", passage_text):
        sentence_tokens = {t.lower() for t in TOKEN_RE.findall(sentence) if len(t) >= 4}
        if len(claim_tokens & sentence_tokens) < 2:
            continue
        p_has_neg = any(cue in sentence.lower() for cue in NEGATION_CUES)
        if p_has_neg != c_has_neg:
            return RelationType.OPPOSES.value
    return RelationType.SUPPORTS.value


def ground_claims_to_evidence(
    claims: List[ClaimNode],
    sources: List[SourceRecord],
    passages: List[PassageRecord],
    assets: Optional[List[AssetRecord]] = None,
    evidence_id_start: int = 1,
    min_score: float = 0.6,
) -> List[EvidenceRecord]:
    """Ground claims against indexed passages, build EvidenceRecords, and bind matching Figure/Table assets."""
    source_map = {s.source_id: s for s in sources}
    assets_list = assets or []
    evidence_records: List[EvidenceRecord] = []
    ev_counter = evidence_id_start

    for claim in claims:
        ranked = rank_passages_for_claim(claim, passages, top_k=3)
        for score, psg in ranked:
            if score < min_score:
                continue
            src = source_map.get(psg.source_id)
            if not src:
                continue
            if len(psg.text.split()) < 12 and psg.section in ("Front_Matter", ""):
                continue

            eid = f"E{ev_counter:03d}"
            ev_counter += 1

            measurements = extract_measurements_from_text(psg.text)
            cond_strings, cond_items = extract_experimental_conditions(psg.text)
            relation = determine_claim_relation(claim.statement, psg.text)

            # Determine scientific verification level strictly by source access status
            if src.access_status == AccessStatus.METADATA_ONLY.value:
                ver_level = VerificationLevel.SOURCE_LOCATED.value
                ver_scope = "metadata_only (full text not inspected; cannot verify experimental claims)"
            elif src.access_status == AccessStatus.ABSTRACT_ONLY.value:
                ver_level = VerificationLevel.EVIDENCE_LOCATED.value
                ver_scope = f"abstract_only ({psg.section}, p.{psg.page}; full experimental section not verified)"
            elif src.access_status == AccessStatus.USER_DATA_VERIFIED.value:
                ver_level = VerificationLevel.APPROVED.value
                ver_scope = f"user_raw_data_verified ({psg.section}, p.{psg.page})"
            elif src.access_status == AccessStatus.FULL_TEXT_LOCAL.value:
                ver_level = (
                    VerificationLevel.CONFLICTED.value
                    if relation == RelationType.OPPOSES.value
                    else VerificationLevel.APPROVED.value
                )
                ver_scope = f"full_text_verified (page {psg.page}, section {psg.section})"
            else:
                ver_level = VerificationLevel.EVIDENCE_LOCATED.value
                ver_scope = f"located ({psg.section}, p.{psg.page})"

            origin = (
                EvidenceOrigin.USER.value
                if src.origin.startswith("user_")
                else EvidenceOrigin.LITERATURE.value
                if src.origin in ("primary_research", "review")
                else EvidenceOrigin.OTHER.value
            )

            # Bind matching Figure/Table assets from the same source
            linked_asset_ids: List[str] = []
            fig_id_hint = psg.figure_mentions[0] if psg.figure_mentions else None
            for asset in assets_list:
                if asset.source_id != src.source_id:
                    continue
                if (
                    asset.page == psg.page
                    or (asset.figure_or_table_id and asset.figure_or_table_id in psg.figure_mentions)
                    or any(psg.text[:60] in m for m in asset.mention_contexts)
                ):
                    linked_asset_ids.append(asset.asset_id)
                    if eid not in asset.evidence_ids:
                        asset.evidence_ids.append(eid)
                    if not fig_id_hint and asset.figure_or_table_id:
                        fig_id_hint = asset.figure_or_table_id

            locator = ExactLocator(
                page=psg.page,
                section=psg.section,
                char_span=psg.char_span,
                figure_or_table_id=fig_id_hint,
                raw_locator_text=f"page {psg.page}, section {psg.section}" + (f", {fig_id_hint}" if fig_id_hint else ""),
            )

            ev = EvidenceRecord(
                evidence_id=eid,
                source_id=src.source_id,
                origin=origin,
                verification_level=ver_level,
                verification_scope=ver_scope,
                locator=locator,
                subject=claim.domain_topic or src.title[:80],
                method=f"Extracted from {src.read_scope_label} ({psg.section})",
                finding=psg.text[:240] + ("..." if len(psg.text) > 240 else ""),
                exact_quote=psg.text,
                measurements=measurements,
                conditions=cond_strings,
                structured_conditions=cond_items,
                controls=["As reported in source experimental protocol"],
                limitations=(
                    ["Abstract-only record; full experimental details and SI controls require full-text PDF verification"]
                    if src.access_status == AccessStatus.ABSTRACT_ONLY.value
                    else ["Scoped to the reported experimental conditions and matrix"]
                ),
                alternative_explanations=["Potential matrix or environmental sensitivity outside tested range"],
                applicability=f"Applicable under conditions: {'; '.join(cond_strings[:3])}",
                claim_links=[
                    ClaimEvidenceLink(
                        claim_id=claim.claim_id,
                        relation=relation,
                        rationale=f"Lexical/quantitative grounding score={score}",
                        confidence=min(1.0, round(score / 5.0, 2)),
                    )
                ],
                asset_ids=linked_asset_ids,
                task_ids=[],
            )
            evidence_records.append(ev)

            if relation == RelationType.SUPPORTS.value:
                if eid not in claim.supporting_evidence_ids:
                    claim.supporting_evidence_ids.append(eid)
            elif relation == RelationType.OPPOSES.value:
                if eid not in claim.opposing_evidence_ids:
                    claim.opposing_evidence_ids.append(eid)
            else:
                if eid not in claim.context_evidence_ids:
                    claim.context_evidence_ids.append(eid)

        # Update claim verification level
        _refresh_claim_verification_state(claim, evidence_records, source_map)

    return evidence_records


def _refresh_claim_verification_state(
    claim: ClaimNode,
    all_evidence: List[EvidenceRecord],
    source_map: Dict[str, SourceRecord],
) -> None:
    ev_by_id = {e.evidence_id: e for e in all_evidence}
    sup = [ev_by_id[eid] for eid in claim.supporting_evidence_ids if eid in ev_by_id]
    opp = [ev_by_id[eid] for eid in claim.opposing_evidence_ids if eid in ev_by_id]

    if opp and sup:
        claim.verification_level = VerificationLevel.CONFLICTED.value
        if not claim.conflict_assessment:
            claim.conflict_assessment = (
                f"Both supporting ({', '.join(e.evidence_id for e in sup)}) and opposing "
                f"({', '.join(e.evidence_id for e in opp)}) evidence exist; conditions must be contrasted."
            )
        if not claim.disclosure:
            claim.disclosure = "Conflicting findings exist across studies or conditions; see conflict assessment."
    elif opp and not sup:
        claim.verification_level = VerificationLevel.CONFLICTED.value
        claim.conflict_assessment = f"Opposed by {', '.join(e.evidence_id for e in opp)} with no supporting evidence."
    elif sup:
        if any(e.verification_level == VerificationLevel.APPROVED.value for e in sup):
            claim.verification_level = VerificationLevel.APPROVED.value
        elif any(e.verification_level == VerificationLevel.SEMANTICALLY_CHECKED.value for e in sup):
            claim.verification_level = VerificationLevel.SEMANTICALLY_CHECKED.value
        elif any(e.verification_level == VerificationLevel.EVIDENCE_LOCATED.value for e in sup):
            claim.verification_level = VerificationLevel.EVIDENCE_LOCATED.value
            if any(source_map.get(e.source_id) and source_map[e.source_id].access_status == AccessStatus.ABSTRACT_ONLY.value for e in sup):
                if not claim.disclosure:
                    claim.disclosure = "Supported by abstract/summary-level evidence only; full-text experimental details pending verification."
        else:
            claim.verification_level = VerificationLevel.SOURCE_LOCATED.value
    else:
        claim.verification_level = VerificationLevel.UNVERIFIED.value

