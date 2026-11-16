"""Scientific Consistency Auditing, Cross-Paper Comparability, Invalidation Propagation, and Review Synthesis.

Provides deterministic + semantic checks for:
1. Abstract Overclaim Detection (claiming APPROVED full-text support when source is ABSTRACT_ONLY / METADATA_ONLY);
2. Quantitative Value & Unit Mismatch Detection between Claims and Grounded Evidence;
3. Experimental Condition Mismatch & Cross-Paper Comparability Assessment
   (DIRECTLY_COMPARABLE vs CONDITIONAL vs NOT_COMPARABLE);
4. Asset Rights & Multi-panel Integrity Auditing;
5. Claim-Evidence Graph Invalidation Propagation & Review Synthesis Matrix generation (for Review Engine).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from .grounding import extract_measurements_from_text
from .models import (
    AccessStatus,
    AssetRecord,
    AssetStatus,
    ClaimNode,
    ComparabilityRecord,
    ComparabilityStatus,
    ConsistencyIssue,
    EvidenceRecord,
    ResearchBundle,
    RightsStatus,
    SourceRecord,
    VerificationLevel,
    compute_sha256,
)
from pathlib import Path


def audit_scientific_consistency(
    bundle: ResearchBundle,
    base_dir: Optional[Path] = None,
) -> List[ConsistencyIssue]:
    """Run comprehensive scientific integrity and consistency checks on a ResearchBundle."""
    issues: List[ConsistencyIssue] = []
    issue_seq = 1

    def add_issue(severity: str, category: str, targets: List[str], msg: str, loc: str = "", rem: str = "") -> None:
        nonlocal issue_seq
        issues.append(
            ConsistencyIssue(
                issue_id=f"ISS{issue_seq:03d}",
                severity=severity,
                category=category,
                target_ids=targets,
                message=msg,
                evidence_locator=loc,
                remediation=rem,
            )
        )
        issue_seq += 1

    source_map: Dict[str, SourceRecord] = {s.source_id: s for s in bundle.sources if not s.retired}
    evidence_map: Dict[str, EvidenceRecord] = {e.evidence_id: e for e in bundle.evidence if not e.retired}
    asset_map: Dict[str, AssetRecord] = {a.asset_id: a for a in bundle.assets if not a.retired}

    # 1. Source Integrity & SHA-256 Checks
    for sid, src in source_map.items():
        if src.access_status == AccessStatus.FULL_TEXT_LOCAL.value:
            if not src.local_path:
                add_issue(
                    "P0_BLOCKER",
                    "SOURCE_MISSING_LOCAL_PATH",
                    [sid],
                    f"{sid} declares FULL_TEXT_LOCAL but has no local_path.",
                    rem="Provide a valid local file path or downgrade access_status to ABSTRACT_ONLY / METADATA_ONLY.",
                )
            else:
                cand_path = Path(src.local_path)
                if not cand_path.is_absolute() and base_dir is not None:
                    cand_path = base_dir / cand_path
                if not cand_path.is_file():
                    add_issue(
                        "P0_BLOCKER",
                        "SOURCE_FILE_MISSING",
                        [sid],
                        f"{sid} local file does not exist: {cand_path}",
                        rem="Restore the source file or update access_status to UNAVAILABLE.",
                    )
                elif src.sha256 and compute_sha256(cand_path) != src.sha256:
                    add_issue(
                        "P0_BLOCKER",
                        "SOURCE_HASH_MISMATCH",
                        [sid],
                        f"{sid} file SHA-256 does not match recorded hash {src.sha256}.",
                        rem="Re-verify the document and update the SHA-256 fingerprint.",
                    )

    # 2. Evidence Grounding, Abstract Overclaim & Condition Integrity Checks
    for eid, ev in evidence_map.items():
        if ev.source_id not in source_map:
            add_issue(
                "P0_BLOCKER",
                "ORPHAN_EVIDENCE_SOURCE",
                [eid, ev.source_id],
                f"{eid} references unknown or retired source {ev.source_id}.",
                rem="Link to an active SourceRecord.",
            )
            continue

        src = source_map[ev.source_id]
        loc_str = ev.locator.format_compact()

        # Check 2a: Abstract / Metadata Overclaim
        if ev.verification_level == VerificationLevel.APPROVED.value and src.access_status in (
            AccessStatus.METADATA_ONLY.value,
            AccessStatus.ABSTRACT_ONLY.value,
        ):
            add_issue(
                "P0_BLOCKER",
                "ABSTRACT_OVERCLAIM_AS_FULLTEXT",
                [eid, src.source_id],
                f"{eid} is marked APPROVED (full-text verified) but source {src.source_id} has access_status={src.access_status}.",
                loc=loc_str,
                rem="Downgrade verification_level to EVIDENCE_LOCATED or attach and parse the full-text PDF.",
            )

        # Check 2b: Missing locator on verified evidence
        if ev.verification_level in (VerificationLevel.SEMANTICALLY_CHECKED.value, VerificationLevel.APPROVED.value):
            if loc_str == "unlocated" or not loc_str.strip():
                add_issue(
                    "P0_BLOCKER",
                    "MISSING_EXACT_LOCATOR",
                    [eid],
                    f"{eid} is marked {ev.verification_level} without a page/section/row locator.",
                    rem="Record exact page, section, figure/table ID, or data row range.",
                )

        # Check 2c: Missing experimental conditions on quantitative evidence
        if ev.measurements and not ev.conditions:
            add_issue(
                "P1_WARNING",
                "MISSING_EXPERIMENTAL_CONDITIONS",
                [eid],
                f"{eid} reports {len(ev.measurements)} quantitative measurement(s) but lists no experimental conditions.",
                loc=loc_str,
                rem="Record solvent/matrix, pH, temperature, or instrument parameters.",
            )

        # Check 2d: Numerical consistency between finding/quote and measurements
        if ev.exact_quote and ev.measurements:
            for m in ev.measurements:
                val_token = str(m.value)
                if val_token and val_token not in ev.exact_quote and val_token not in ev.finding:
                    add_issue(
                        "P0_BLOCKER",
                        "MEASUREMENT_NOT_IN_QUOTE",
                        [eid],
                        f"{eid} records measurement {m.value} {m.unit} ({m.definition}), which does not appear in exact_quote or finding.",
                        loc=loc_str,
                        rem="Verify original text or mark value as derived/converted with explicit formula.",
                    )

    # 3. Claim-Evidence Consistency, Unsupported Quantitative Claims & Unassessed Conflicts
    for claim in bundle.claims:
        if claim.retired:
            continue
        cid = claim.claim_id
        sup_evs = [evidence_map[e] for e in claim.supporting_evidence_ids if e in evidence_map]
        opp_evs = [evidence_map[e] for e in claim.opposing_evidence_ids if e in evidence_map]

        if claim.verification_level == VerificationLevel.APPROVED.value and not sup_evs:
            add_issue(
                "P0_BLOCKER",
                "UNSUPPORTED_APPROVED_CLAIM",
                [cid],
                f"Claim {cid} is marked APPROVED but has zero active supporting evidence records.",
                rem="Attach verified supporting evidence or downgrade claim status.",
            )

        if opp_evs and (not claim.conflict_assessment.strip() or not claim.disclosure.strip()):
            add_issue(
                "P0_BLOCKER",
                "UNDISCLOSED_OPPOSING_EVIDENCE",
                [cid] + [e.evidence_id for e in opp_evs],
                f"Claim {cid} has opposing evidence ({', '.join(e.evidence_id for e in opp_evs)}) without conflict_assessment and disclosure.",
                rem="Document condition/method differences in conflict_assessment and add explicit disclosure.",
            )

        if claim.origin_requirement == "user" and sup_evs:
            if not any(e.origin == "user" for e in sup_evs):
                add_issue(
                    "P0_BLOCKER",
                    "LITERATURE_DISGUISED_AS_USER_RESULT",
                    [cid] + [e.evidence_id for e in sup_evs],
                    f"Claim {cid} represents a user experimental finding but is only backed by literature evidence.",
                    rem="Provide user experimental evidence (origin='user') or reclassify the claim as literature background.",
                )

        # Check quantitative numbers in claim against supporting evidence
        claim_meas = extract_measurements_from_text(claim.statement)
        if claim_meas and sup_evs:
            ev_meas_all = [m for e in sup_evs for m in e.measurements]
            for cm in claim_meas:
                same_unit_or_si = [
                    em for em in ev_meas_all
                    if em.unit == cm.unit or (em.normalized_si_unit and em.normalized_si_unit == cm.normalized_si_unit)
                ]
                if same_unit_or_si:
                    matched_any = False
                    for em in same_unit_or_si:
                        if str(em.value) == str(cm.value):
                            matched_any = True
                            break
                        if (
                            em.normalized_si_value is not None
                            and cm.normalized_si_value is not None
                            and abs(em.normalized_si_value - cm.normalized_si_value) <= max(1e-12, abs(em.normalized_si_value) * 0.02)
                        ):
                            matched_any = True
                            break
                    if not matched_any:
                        add_issue(
                            "P0_BLOCKER",
                            "CLAIM_EVIDENCE_VALUE_MISMATCH",
                            [cid] + [e.evidence_id for e in sup_evs],
                            f"Claim {cid} states '{cm.value} {cm.unit}', which conflicts with supporting evidence values: "
                            + ", ".join(f"{em.value} {em.unit}" for em in same_unit_or_si),
                            rem="Align claim number/unit with the grounded evidence or split condition-specific claims.",
                        )

    # 4. Asset Provenance, Rights & Multi-panel Integrity Checks
    for aid, asset in asset_map.items():
        if asset.status == AssetStatus.ACQUIRED.value:
            if not asset.path or not asset.sha256:
                add_issue(
                    "P0_BLOCKER",
                    "ACQUIRED_ASSET_MISSING_HASH_OR_PATH",
                    [aid],
                    f"Asset {aid} is marked ACQUIRED but lacks local path or SHA-256.",
                    rem="Record verified file path and SHA-256 or set status to LOCATED.",
                )
            else:
                ap = Path(asset.path)
                if not ap.is_absolute() and base_dir is not None:
                    ap = base_dir / ap
                if not ap.is_file():
                    add_issue(
                        "P0_BLOCKER",
                        "ACQUIRED_ASSET_FILE_MISSING",
                        [aid],
                        f"Acquired asset file missing: {ap}",
                        rem="Re-extract the asset or downgrade status to LOCATED/UNAVAILABLE.",
                    )
                elif compute_sha256(ap) != asset.sha256:
                    add_issue(
                        "P0_BLOCKER",
                        "ASSET_HASH_MISMATCH",
                        [aid],
                        f"Asset {aid} file SHA-256 differs from manifest.",
                        rem="Re-verify asset file integrity.",
                    )

        if asset.rights.status == RightsStatus.ALLOWED.value and asset.rights.commercial_use_allowed is None:
            if "unknown" in asset.rights.basis.lower():
                add_issue(
                    "P0_BLOCKER",
                    "UNKNOWN_RIGHTS_MARKED_ALLOWED",
                    [aid],
                    f"Asset {aid} has unknown copyright basis but is marked ALLOWED.",
                    rem="Set rights.status to UNKNOWN or NEEDS_PERMISSION until license is confirmed.",
                )

    bundle.consistency_issues = issues
    return issues


def evaluate_evidence_comparability(
    comparison_id: str,
    ev_a: EvidenceRecord,
    ev_b: EvidenceRecord,
) -> ComparabilityRecord:
    """Compare two EvidenceRecords across metric, unit, conditions, controls, statistics, and scope."""
    meas_a = ev_a.measurements[0] if ev_a.measurements else None
    meas_b = ev_b.measurements[0] if ev_b.measurements else None

    metric_a = meas_a.definition if meas_a else ev_a.subject
    metric_b = meas_b.definition if meas_b else ev_b.subject
    same_metric = metric_a.lower() == metric_b.lower()

    unit_a = meas_a.unit if meas_a else "qualitative"
    unit_b = meas_b.unit if meas_b else "qualitative"
    same_unit = unit_a == unit_b
    convertible_unit = bool(
        meas_a
        and meas_b
        and meas_a.normalized_si_unit
        and meas_a.normalized_si_unit == meas_b.normalized_si_unit
    )

    cond_map_a = {c.parameter.lower(): c.value.lower() for c in ev_a.structured_conditions}
    cond_map_b = {c.parameter.lower(): c.value.lower() for c in ev_b.structured_conditions}
    shared_keys = set(cond_map_a.keys()) & set(cond_map_b.keys())
    mismatched_conds = [
        f"{k}: '{cond_map_a[k]}' vs '{cond_map_b[k]}'"
        for k in sorted(shared_keys)
        if cond_map_a[k] != cond_map_b[k]
    ]

    if not same_metric and not convertible_unit:
        status = ComparabilityStatus.NOT_COMPARABLE.value
        permitted = "separate"
        rationale = f"Metrics/definitions differ ({metric_a} vs {metric_b}); direct ranking prohibited."
    elif mismatched_conds:
        status = ComparabilityStatus.CONDITIONAL.value
        permitted = "qualified"
        rationale = f"Same metric ({metric_a}), but experimental conditions differ ({'; '.join(mismatched_conds)}); requires explicit condition qualifier."
    elif same_unit:
        status = ComparabilityStatus.DIRECTLY_COMPARABLE.value
        permitted = "rank"
        rationale = f"Identical metric ({metric_a}), unit ({unit_a}), and compatible experimental conditions."
    elif convertible_unit:
        status = ComparabilityStatus.CONDITIONAL.value
        permitted = "qualified"
        rationale = (
            f"Units differ ({unit_a} vs {unit_b}) but convert to common SI unit ({meas_a.normalized_si_unit}); "
            "preserve original values alongside SI conversion."
        )
    else:
        status = ComparabilityStatus.NOT_COMPARABLE.value
        permitted = "separate"
        rationale = f"Incompatible units ({unit_a} vs {unit_b}) without valid conversion."

    norm_vals: List[Dict[str, Any]] = []
    for ev, m in ((ev_a, meas_a), (ev_b, meas_b)):
        if m:
            norm_vals.append({
                "evidence_id": ev.evidence_id,
                "raw_value": m.value,
                "raw_unit": m.unit,
                "normalized_si_value": m.normalized_si_value,
                "normalized_si_unit": m.normalized_si_unit,
            })

    return ComparabilityRecord(
        comparison_id=comparison_id,
        evidence_ids=[ev_a.evidence_id, ev_b.evidence_id],
        status=status,
        dimensions={
            "metric": f"{metric_a} vs {metric_b}",
            "unit": f"{unit_a} vs {unit_b}" + (f" (SI: {meas_a.normalized_si_unit})" if convertible_unit and meas_a else ""),
            "conditions": "; ".join(mismatched_conds) if mismatched_conds else f"{'; '.join(ev_a.conditions[:2])} vs {'; '.join(ev_b.conditions[:2])}",
            "controls": f"{'; '.join(ev_a.controls[:1])} vs {'; '.join(ev_b.controls[:1])}",
            "statistics": "Reported point/interval estimates per source protocol",
            "scope": f"{ev_a.applicability[:60]} | {ev_b.applicability[:60]}",
        },
        rationale=rationale,
        permitted_use=permitted,
        normalized_values=norm_vals,
    )


def propagate_invalidation(
    bundle: ResearchBundle,
    invalidated_ids: List[str],
) -> Dict[str, Any]:
    """Compute downstream impact across Evidence, Assets, Comparisons, and Claims when a Source or Evidence is invalidated/retracted.

    Designed for both evidence updates and Review Engine invalidation propagation.
    """
    invalid_set: Set[str] = set(invalidated_ids)
    affected_evidence: Set[str] = set()
    affected_assets: Set[str] = set()
    affected_comparisons: Set[str] = set()
    affected_claims: Set[str] = set()

    for ev in bundle.evidence:
        if ev.evidence_id in invalid_set or ev.source_id in invalid_set:
            affected_evidence.add(ev.evidence_id)
            for cl in ev.claim_links:
                affected_claims.add(cl.claim_id)

    for asset in bundle.assets:
        if (
            asset.asset_id in invalid_set
            or asset.source_id in invalid_set
            or any(eid in affected_evidence for eid in asset.evidence_ids)
        ):
            affected_assets.add(asset.asset_id)

    for cmp_rec in bundle.comparisons:
        if any(eid in affected_evidence for eid in cmp_rec.evidence_ids):
            affected_comparisons.add(cmp_rec.comparison_id)

    for claim in bundle.claims:
        if any(eid in affected_evidence for eid in claim.supporting_evidence_ids + claim.opposing_evidence_ids):
            affected_claims.add(claim.claim_id)

    return {
        "invalidated_inputs": sorted(invalid_set),
        "affected_evidence_ids": sorted(affected_evidence),
        "affected_asset_ids": sorted(affected_assets),
        "affected_comparison_ids": sorted(affected_comparisons),
        "affected_claim_ids": sorted(affected_claims),
    }


def build_review_synthesis_matrix(bundle: ResearchBundle) -> Dict[str, Any]:
    """Generate a task-neutral cross-paper synthesis table & provenance-linked review draft for Review Engine."""
    source_map = {s.source_id: s for s in bundle.sources}
    rows: List[Dict[str, Any]] = []
    for ev in bundle.evidence:
        if ev.retired:
            continue
        src = source_map.get(ev.source_id)
        rows.append({
            "evidence_id": ev.evidence_id,
            "source_id": ev.source_id,
            "citation": f"{(src.authors[0] if src and src.authors else 'Anon')} ({src.year if src and src.year else 'n.d.'})",
            "doi": src.doi if src else None,
            "access_status": src.access_status if src else "UNKNOWN",
            "verification_level": ev.verification_level,
            "locator": ev.locator.format_compact(),
            "subject": ev.subject,
            "method": ev.method,
            "finding": ev.finding,
            "measurements": [
                {"value": m.value, "unit": m.unit, "definition": m.definition, "si_value": m.normalized_si_value, "si_unit": m.normalized_si_unit}
                for m in ev.measurements
            ],
            "conditions": ev.conditions,
            "claim_links": [{"claim_id": c.claim_id, "relation": c.relation} for c in ev.claim_links],
            "asset_ids": ev.asset_ids,
        })

    # Build provenance-linked synthesis paragraphs per claim
    synthesis_paragraphs: List[Dict[str, Any]] = []
    ev_map = {e.evidence_id: e for e in bundle.evidence}
    for claim in bundle.claims:
        if claim.retired:
            continue
        sentences: List[str] = [f"Claim [{claim.claim_id}]: {claim.statement} (Status: {claim.verification_level})."]
        citations_used: List[str] = []
        for eid in claim.supporting_evidence_ids:
            ev = ev_map.get(eid)
            if not ev:
                continue
            src = source_map.get(ev.source_id)
            cite_tag = f"[{ev.source_id}/{ev.evidence_id}, {ev.locator.format_compact()}]"
            cond_tag = f" (conditions: {'; '.join(ev.conditions[:2])})" if ev.conditions else ""
            sentences.append(f"Supported by {src.title if src else ev.source_id}: {ev.finding}{cond_tag} {cite_tag}.")
            citations_used.append(cite_tag)
        for eid in claim.opposing_evidence_ids:
            ev = ev_map.get(eid)
            if not ev:
                continue
            cite_tag = f"[{ev.source_id}/{ev.evidence_id}, {ev.locator.format_compact()}]"
            sentences.append(f"Conversely, opposing/limiting evidence was reported: {ev.finding} {cite_tag}.")
            citations_used.append(cite_tag)
        if claim.disclosure:
            sentences.append(f"Qualification: {claim.disclosure}")
        synthesis_paragraphs.append({
            "claim_id": claim.claim_id,
            "verification_level": claim.verification_level,
            "paragraph_text": " ".join(sentences),
            "provenance_anchors": citations_used,
        })

    return {
        "bundle_id": bundle.bundle_id,
        "schema_version": bundle.schema_version,
        "evidence_matrix": rows,
        "comparisons": [
            {
                "comparison_id": c.comparison_id,
                "evidence_ids": c.evidence_ids,
                "status": c.status,
                "permitted_use": c.permitted_use,
                "rationale": c.rationale,
            }
            for c in bundle.comparisons
        ],
        "review_paragraphs": synthesis_paragraphs,
    }

