"""Thin AI PPT Adapter over the task-neutral Reusable Research Evidence & Asset Core.

All AI PPT / APC / APRD / Slide / Visual / EVIDENCE_PACKAGE (00-07) logic lives strictly here,
keeping research_core 100% task-neutral and reusable by future Review Engines.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Any, Dict, List, Optional

from research_core.consistency import audit_scientific_consistency, evaluate_evidence_comparability
from research_core.models import (
    AccessStatus,
    AssetRecord,
    AssetStatus,
    ClaimNode,
    ResearchBundle,
    RightsStatus,
    SourceRecord,
    VerificationLevel,
    compute_sha256,
)

CORE_TO_PPT_EVIDENCE_STATUS: Dict[str, str] = {
    VerificationLevel.APPROVED.value: "VERIFIED",
    VerificationLevel.SEMANTICALLY_CHECKED.value: "VERIFIED",
    VerificationLevel.EVIDENCE_LOCATED.value: "INFERRED",
    VerificationLevel.SOURCE_LOCATED.value: "TO_RETRIEVE",
    VerificationLevel.UNVERIFIED.value: "TO_RETRIEVE",
    VerificationLevel.CONFLICTED.value: "BLOCKED",
}


def map_core_source_to_ppt(src: SourceRecord, package_dir: Path) -> Dict[str, Any]:
    """Convert a task-neutral SourceRecord to SEAA 01_SOURCE_MANIFEST.json schema."""
    if src.origin.startswith("user_"):
        ver_status = "USER_PROVIDED" if src.verification_level in (
            VerificationLevel.APPROVED.value,
            VerificationLevel.SEMANTICALLY_CHECKED.value,
            VerificationLevel.EVIDENCE_LOCATED.value,
        ) else "TO_VERIFY"
    elif src.access_status == AccessStatus.UNAVAILABLE.value:
        ver_status = "UNAVAILABLE"
    elif src.verification_level in (
        VerificationLevel.APPROVED.value,
        VerificationLevel.SEMANTICALLY_CHECKED.value,
        VerificationLevel.EVIDENCE_LOCATED.value,
        VerificationLevel.SOURCE_LOCATED.value,
    ):
        ver_status = "VERIFIED"
    else:
        ver_status = "TO_VERIFY"

    row: Dict[str, Any] = {
        "source_id": src.source_id,
        "title": src.title,
        "origin": src.origin,
        "url_or_path": src.url_or_path,
        "read_scope": src.read_scope_label,
        "verification_status": ver_status,
        "metadata": {
            "authors": src.authors,
            "year": src.year,
            "doi": src.doi,
            "publication": src.publication,
            "peer_reviewed": src.peer_reviewed,
        },
        "usage_notes": src.usage_notes or f"Scope={src.read_scope_label}",
    }
    if src.retired:
        row["retired"] = True

    if src.local_path:
        lp = Path(src.local_path)
        if not lp.is_absolute():
            lp = package_dir / lp
        if lp.is_file():
            rel_dest = Path("given") / lp.name
            dest_file = package_dir / rel_dest
            if dest_file.resolve() != lp.resolve():
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(lp, dest_file)
            row["local_path"] = rel_dest.as_posix()
            row["sha256"] = compute_sha256(dest_file)
            if not str(row["url_or_path"]).startswith(("http://", "https://")):
                row["url_or_path"] = rel_dest.as_posix()
    return row


def score_asset_for_slide(asset: AssetRecord) -> Dict[str, Any]:
    """Compute AI PPT presentation suitability, readability, and crop guidance without mutating Core data."""
    width, height = asset.pixel_size if asset.pixel_size else (1200, 800)
    aspect_ratio = round(width / max(1, height), 3)
    min_dim = min(width, height)
    clarity = "HIGH" if min_dim >= 400 else ("MEDIUM" if min_dim >= 200 else "LOW")
    readability_score = 0.92 if clarity == "HIGH" else (0.75 if clarity == "MEDIUM" else 0.45)
    if asset.is_multi_panel:
        crop_advice = (
            f"Multi-panel scientific figure ({', '.join(asset.panel_labels)}): preserve full compound frame or "
            "keep panel labels (a/b/c) and shared legend intact; do not crop isolated curves without axes."
        )
    elif asset.asset_kind == "data_table":
        crop_advice = "Render as native structured slide table or highlight key comparison rows while keeping header units."
    else:
        crop_advice = "Trim outer whitespace margins only; preserve axis labels, tick marks, units, and sample legend."

    return {
        "asset_id": asset.asset_id,
        "pixel_size": [width, height],
        "aspect_ratio": aspect_ratio,
        "clarity_grade": clarity,
        "readability_score": readability_score,
        "is_multi_panel": asset.is_multi_panel,
        "panel_labels": asset.panel_labels,
        "crop_guidance": crop_advice,
        "ClearedForReadySlide": asset.status == AssetStatus.ACQUIRED.value and asset.rights.status == RightsStatus.ALLOWED.value,
    }


def compile_evidence_package_from_bundle(
    bundle: ResearchBundle,
    package_dir: Path,
    handoff_path: Optional[Path] = None,
    mode: str = "FULL_RESEARCH",
    scope: str = "full",
    slide_plan: Optional[List[Dict[str, Any]]] = None,
    tasks_plan: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Compile a task-neutral ResearchBundle (+ optional APC PPT_HANDOFF.json) into a compliant SEAA EVIDENCE_PACKAGE/."""
    package_dir.mkdir(parents=True, exist_ok=True)
    up = json.loads(handoff_path.read_text(encoding="utf-8-sig")) if handoff_path and handoff_path.is_file() else None

    project_id = up["project_id"] if up else bundle.bundle_id
    base_revision = up["revision"] if up else bundle.base_revision
    package_revision = bundle.revision

    ppt_sources = [map_core_source_to_ppt(s, package_dir) for s in bundle.sources]
    source_by_id = {s["source_id"]: s for s in ppt_sources}

    if up:
        ur = up.get("records", {})
        target_slide_ids = list(up.get("slide_order", []))
        claim_ids = sorted(k for k, v in ur.items() if k.startswith("C") and not v.get("retired"))
        task_ids = sorted(k for k, v in ur.items() if k.startswith("R") and not v.get("retired"))
    else:
        target_slide_ids = [s["slide_id"] for s in (slide_plan or [])] or ["S001"]
        claim_ids = [c.claim_id for c in bundle.claims if not c.retired] or ["C001"]
        task_ids = [t["task_id"] for t in (tasks_plan or [])] or ["R001"]

    claim_map = {c.claim_id: c for c in bundle.claims}
    for cid in claim_ids:
        if cid not in claim_map:
            stmt = up["records"][cid].get("statement", f"Claim {cid}") if (up and cid in up.get("records", {})) else f"Claim {cid}"
            claim_map[cid] = ClaimNode(claim_id=cid, statement=stmt)

    ppt_assets: List[Dict[str, Any]] = []
    asset_suitability: List[Dict[str, Any]] = []
    for a in bundle.assets:
        a_slides = list(a.extra.get("slide_ids") or target_slide_ids[:1])
        ev_ids = list(a.evidence_ids)
        if not ev_ids and bundle.evidence:
            same_src_ev = [e.evidence_id for e in bundle.evidence if e.source_id == a.source_id]
            ev_ids = same_src_ev[:1] or [bundle.evidence[0].evidence_id]

        rel_path: Optional[str] = None
        sha_val: Optional[str] = None
        fmt_val: Optional[str] = a.format
        st_val = a.status

        if a.status == AssetStatus.ACQUIRED.value and a.path:
            src_file = Path(a.path)
            if not src_file.is_absolute():
                src_file = package_dir / src_file
            if src_file.is_file():
                dest_rel = Path("assets") / src_file.name
                dest_abs = package_dir / dest_rel
                if dest_abs.resolve() != src_file.resolve():
                    dest_abs.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_file, dest_abs)
                rel_path = dest_rel.as_posix()
                sha_val = compute_sha256(dest_abs)
                fmt_val = (a.format or src_file.suffix.lstrip(".") or "dat").lower()
            else:
                st_val = AssetStatus.LOCATED.value

        ppt_assets.append({
            "asset_id": a.asset_id,
            "source_id": a.source_id,
            "evidence_ids": ev_ids,
            "slide_ids": a_slides,
            "status": st_val,
            "source_locator": a.source_locator or f"page {a.page or 1}, {a.figure_or_table_id or a.asset_id}",
            "path": rel_path if st_val == "ACQUIRED" else None,
            "sha256": sha_val if st_val == "ACQUIRED" else None,
            "format": fmt_val if st_val == "ACQUIRED" else None,
            "caption": a.caption or f"Scientific asset {a.asset_id}",
            "conditions": a.conditions if a.conditions else ["Preserve caption experimental conditions"],
            "rights": {
                "status": a.rights.status,
                "basis": a.rights.basis,
                "attribution": a.rights.attribution or f"Source {a.source_id}",
            },
            "scientific_immutable": a.scientific_immutable if a.scientific_immutable else ["Axis labels and units"],
            "transformation": {
                "kind": a.transformation.kind,
                "basis": a.transformation.basis,
            },
        })
        asset_suitability.append(score_asset_for_slide(a))

    ppt_evidence: List[Dict[str, Any]] = []
    for ev in bundle.evidence:
        src_row = source_by_id.get(ev.source_id, {})
        src_origin = src_row.get("origin", "primary_research")
        expected_origin = (
            "user" if src_origin.startswith("user_")
            else "literature" if src_origin in ("primary_research", "review")
            else "other"
        )
        if expected_origin == "user" and src_row.get("verification_status") == "USER_PROVIDED":
            ppt_status = "USER_PROVIDED"
        elif src_row.get("read_scope") == "metadata":
            ppt_status = "TO_RETRIEVE"
        elif src_row.get("read_scope") == "abstract" and ev.verification_level == VerificationLevel.EVIDENCE_LOCATED.value:
            ppt_status = ev.extra.get("ppt_status") or "INFERRED"
        else:
            ppt_status = ev.extra.get("ppt_status") or CORE_TO_PPT_EVIDENCE_STATUS.get(ev.verification_level, "VERIFIED")

        ev_slides = list(ev.extra.get("slide_ids") or target_slide_ids[:1])
        ev_tasks = [t for t in ev.task_ids if t in task_ids] or (task_ids[:1] if task_ids else [])
        clinks = [
            {"claim_id": cl.claim_id, "relation": cl.relation if cl.relation in ("supports", "opposes", "context") else "supports"}
            for cl in ev.claim_links
            if cl.claim_id in claim_ids
        ]
        if not clinks and claim_ids:
            clinks = [{"claim_id": claim_ids[0], "relation": "supports"}]

        row_ev: Dict[str, Any] = {
            "evidence_id": ev.evidence_id,
            "source_id": ev.source_id,
            "origin": expected_origin,
            "status": ppt_status,
            "verification_scope": ev.verification_scope,
            "locator": ev.locator.format_compact(),
            "subject": ev.subject,
            "method": ev.method,
            "finding": ev.finding,
            "measurements": [
                {"value": m.value, "unit": m.unit, "definition": m.definition}
                for m in ev.measurements
            ],
            "conditions": ev.conditions if ev.conditions else ["As specified in source protocol"],
            "controls": ev.controls if ev.controls else ["Standard reference/blank control"],
            "limitations": ev.limitations if ev.limitations else ["Scoped to tested experimental conditions"],
            "alternative_explanations": ev.alternative_explanations if ev.alternative_explanations else ["None reported within tested range"],
            "applicability": ev.applicability or "Applicable within stated experimental parameters",
            "claim_links": clinks,
            "slide_ids": ev_slides,
            "task_ids": ev_tasks,
        }
        if "upstream_source" in ev.extra:
            row_ev["upstream_source"] = ev.extra["upstream_source"]
        if ev.retired:
            row_ev["retired"] = True
        ppt_evidence.append(row_ev)

    if tasks_plan:
        ppt_tasks = tasks_plan
    else:
        ppt_tasks = []
        for tid in task_ids:
            linked_e = [e["evidence_id"] for e in ppt_evidence if tid in e["task_ids"]]
            linked_a = [
                a["asset_id"]
                for a in ppt_assets
                if any(eid in linked_e for eid in a["evidence_ids"])
            ]
            ppt_tasks.append({
                "task_id": tid,
                "status": "DONE" if linked_e else "OPEN",
                "evidence_ids": linked_e,
                "asset_ids": linked_a,
                "resolution": f"Grounded {len(linked_e)} evidence record(s) and {len(linked_a)} asset(s)." if linked_e else "",
            })

    ppt_comparisons: List[Dict[str, Any]] = []
    for cmp_rec in bundle.comparisons:
        ppt_comparisons.append({
            "comparison_id": cmp_rec.comparison_id,
            "evidence_ids": cmp_rec.evidence_ids,
            "status": cmp_rec.status,
            "dimensions": cmp_rec.dimensions,
            "rationale": cmp_rec.rationale,
            "permitted_use": cmp_rec.permitted_use,
        })
    if not ppt_comparisons and len(ppt_evidence) >= 2 and len(bundle.evidence) >= 2:
        auto_cmp = evaluate_evidence_comparability("CMP001", bundle.evidence[0], bundle.evidence[1])
        ppt_comparisons.append({
            "comparison_id": auto_cmp.comparison_id,
            "evidence_ids": auto_cmp.evidence_ids,
            "status": auto_cmp.status,
            "dimensions": auto_cmp.dimensions,
            "rationale": auto_cmp.rationale,
            "permitted_use": auto_cmp.permitted_use,
        })

    if slide_plan:
        ppt_slides = slide_plan
    else:
        ppt_slides = []
        for sid in target_slide_ids:
            s_evs = [e for e in ppt_evidence if sid in e["slide_ids"]]
            s_eids = [e["evidence_id"] for e in s_evs]
            s_assets = [
                a for a in ppt_assets
                if sid in a["slide_ids"] and set(a["evidence_ids"]) <= set(s_eids)
            ]
            s_cmps = [
                c["comparison_id"] for c in ppt_comparisons
                if set(c["evidence_ids"]) <= set(s_eids)
            ]
            v_ids: List[str] = []
            if up and sid in up.get("records", {}):
                v_ids = [k for k in up["records"][sid].get("depends_on", []) if k.startswith("V")]

            s_claim_ids: List[str] = []
            if up and sid in up.get("records", {}):
                s_claim_ids = [k for k in up["records"][sid].get("depends_on", []) if k.startswith("C")]
            if not s_claim_ids:
                for e in s_evs:
                    for cl in e["claim_links"]:
                        if cl["claim_id"] not in s_claim_ids:
                            s_claim_ids.append(cl["claim_id"])
            if not s_claim_ids and claim_ids:
                s_claim_ids = [claim_ids[0]]

            slide_claims_objs: List[Dict[str, Any]] = []
            all_claims_ready = True
            gaps: List[str] = []

            for cid in s_claim_ids:
                usable_sup = [
                    e for e in s_evs
                    if e["status"] in ("VERIFIED", "USER_PROVIDED")
                    and {"claim_id": cid, "relation": "supports"} in e["claim_links"]
                ]
                if up and cid in up.get("records", {}) and up["records"][cid].get("origin") == "user":
                    if not any(e["origin"] == "user" for e in usable_sup):
                        usable_sup = []
                has_opposing = any(
                    {"claim_id": cid, "relation": "opposes"} in e["claim_links"]
                    for e in ppt_evidence
                )
                has_user_prov = any(e["status"] == "USER_PROVIDED" for e in usable_sup)
                c_node = claim_map.get(cid)
                purpose = "establish" if usable_sup else "discuss"
                if not usable_sup:
                    all_claims_ready = False
                    gaps.append(f"Claim {cid} lacks VERIFIED/USER_PROVIDED supporting evidence on {sid}.")

                disclosure_str = (c_node.disclosure if c_node else "") or ""
                if purpose == "discuss" and not disclosure_str:
                    disclosure_str = "Presented as preliminary discussion pending full-text or experimental verification."
                if has_user_prov and not disclosure_str:
                    disclosure_str = "Based on user-provided experimental records; scoped to reported batch conditions."
                if has_opposing and not disclosure_str:
                    disclosure_str = "Opposing or condition-dependent evidence exists and must be disclosed on slide."

                c_entry: Dict[str, Any] = {
                    "claim_id": cid,
                    "purpose": purpose,
                    "disclosure": disclosure_str,
                }
                if has_opposing:
                    c_entry["conflict_assessment"] = (
                        (c_node.conflict_assessment if c_node and c_node.conflict_assessment else "")
                        or "Opposing evidence observed under differing experimental conditions; see comparison notes."
                    )
                slide_claims_objs.append(c_entry)

            cleared_assets = [
                a for a in s_assets
                if a["status"] == "ACQUIRED" and a["rights"]["status"] == "ALLOWED"
            ]
            asset_req = "required" if cleared_assets else "optional"
            used_asset_ids = [a["asset_id"] for a in cleared_assets] if all_claims_ready else [a["asset_id"] for a in s_assets]

            slide_status = "READY" if (all_claims_ready and not gaps) else "PROVISIONAL"
            if slide_status != "READY" and not gaps:
                gaps.append("Pending full evidence or asset clearance.")

            ppt_slides.append({
                "slide_id": sid,
                "status": slide_status,
                "scientific_message": f"Evidence-grounded findings for {', '.join(s_claim_ids)} on {sid}.",
                "claims": slide_claims_objs,
                "evidence_ids": s_eids,
                "asset_ids": used_asset_ids,
                "visual_ids": v_ids,
                "asset_requirement": asset_req,
                "asset_rationale": (
                    f"Backed by {len(used_asset_ids)} cleared scientific asset(s)."
                    if used_asset_ids
                    else "Text/quantitative evidence synthesis slide; visual asset optional."
                ),
                "must_preserve": [
                    cond for e in s_evs for cond in e["conditions"][:2]
                ] or ["Preserve original experimental conditions and units"],
                "prohibited_inferences": [
                    "Do not extrapolate beyond tested concentration, pH, or matrix conditions",
                    "Do not rank non-comparable cross-paper metrics directly",
                ],
                "gaps": [] if slide_status == "READY" else gaps,
                "comparison_ids": s_cmps,
            })

    evidence_manifest = {
        "project_id": project_id,
        "base_revision": base_revision,
        "package_revision": package_revision,
        "mode": mode,
        "scope": scope,
        "target_slide_ids": target_slide_ids,
        "claim_ids": claim_ids,
        "evidence": ppt_evidence,
        "tasks": ppt_tasks,
        "comparisons": ppt_comparisons,
        "slides": ppt_slides,
    }

    (package_dir / "01_SOURCE_MANIFEST.json").write_text(
        json.dumps({"sources": ppt_sources}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (package_dir / "02_EVIDENCE_MANIFEST.json").write_text(
        json.dumps(evidence_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (package_dir / "03_ASSET_MANIFEST.json").write_text(
        json.dumps({"assets": ppt_assets}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    (package_dir / "00_RESEARCH_OVERVIEW.md").write_text(
        f"# 00 Research Overview — {bundle.title}\n\n"
        f"- **Project ID**: {project_id} (Package Revision {package_revision}, Base Revision {base_revision})\n"
        f"- **Execution Mode**: {mode} (scope={scope})\n"
        f"- **Sources Registered**: {len(ppt_sources)} (FULL_TEXT_LOCAL / ABSTRACT_ONLY / USER_DATA strictly separated)\n"
        f"- **Grounded Evidence Records**: {len(ppt_evidence)}\n"
        f"- **Scientific Assets**: {len(ppt_assets)}\n\n"
        "## Core Scientific Findings & Scope\n\n"
        + "\n".join(f"- **{e['evidence_id']}** ({e['source_id']}, {e['status']}, {e['locator']}): {e['finding']}" for e in ppt_evidence)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    (package_dir / "04_RETRIEVAL_LOG.md").write_text(
        f"# 04 Retrieval & Verification Log\n\n"
        f"- **Mode**: {mode}\n"
        f"- **Processing Trail**:\n"
        + (
            "\n".join(
                f"  - {p.step_id} [{p.timestamp}] {p.operation}: inputs={p.input_refs} -> outputs={p.output_refs} ({p.notes})"
                for p in bundle.processing_records
            )
            if bundle.processing_records
            else "  - Local deterministic audit and evidence grounding executed."
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    issues = audit_scientific_consistency(bundle, base_dir=package_dir)
    (package_dir / "05_GAPS_AND_CONFLICTS.md").write_text(
        "# 05 Gaps, Conflicts & Consistency Audit\n\n"
        "## Scientific Consistency Findings\n\n"
        + (
            "\n".join(
                f"- **{i.issue_id}** [{i.severity} / {i.category}] ({', '.join(i.target_ids)}): {i.message} — Remediation: {i.remediation}"
                for i in issues
            )
            if issues
            else "- No P0/P1 scientific consistency conflicts detected in current bundle."
        )
        + "\n\n## Cross-Study Comparability\n\n"
        + (
            "\n".join(
                f"- **{c['comparison_id']}** ({', '.join(c['evidence_ids'])}) -> {c['status']} (Permitted use: {c['permitted_use']}): {c['rationale']}"
                for c in ppt_comparisons
            )
            if ppt_comparisons
            else "- No cross-study comparisons registered."
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    summary_rows = "\n".join(
        f"| {s['slide_id']} | {s['status']} | {', '.join(c['claim_id'] for c in s['claims'])} | {', '.join(s['evidence_ids'])} | {', '.join(s['asset_ids']) or 'none'} |"
        for s in ppt_slides
    )
    slide_details = "\n\n".join(
        f"### {s['slide_id']} ({s['status']})\n"
        f"- **Scientific Message**: {s['scientific_message']}\n"
        f"- **Evidence IDs**: {', '.join(s['evidence_ids'])}\n"
        f"- **Asset IDs**: {', '.join(s['asset_ids']) or 'none'} ({s['asset_requirement']} — {s['asset_rationale']})\n"
        f"- **Must Preserve Conditions**: {'; '.join(s['must_preserve'])}\n"
        f"- **Prohibited Inferences**: {'; '.join(s['prohibited_inferences'])}\n"
        f"- **Gaps**: {'; '.join(s['gaps']) if s['gaps'] else 'None'}"
        for s in ppt_slides
    )
    (package_dir / "06_HANDOFF_TO_PROMPT3.md").write_text(
        "# 06 Handoff to Prompt 3 (Orchestrator & Slide Compiler)\n\n"
        "| Slide ID | Status | Claims | Evidence | Assets |\n"
        "| --- | --- | --- | --- | --- |\n"
        f"{summary_rows}\n\n"
        f"## Per-Slide Scientific Constraints\n\n{slide_details}\n",
        encoding="utf-8",
        newline="\n",
    )

    validation_report = {
        "schema_version": "seaa.validation-report.v1",
        "project_id": project_id,
        "package_revision": package_revision,
        "base_revision": base_revision,
        "mode": mode,
        "scope": scope,
        "counts": {
            "sources": len(ppt_sources),
            "evidence": len(ppt_evidence),
            "assets": len(ppt_assets),
            "comparisons": len(ppt_comparisons),
            "slides": len(ppt_slides),
            "ready_slides": sum(1 for s in ppt_slides if s["status"] == "READY"),
        },
        "asset_suitability": asset_suitability,
        "consistency_issues": [
            {
                "issue_id": i.issue_id,
                "severity": i.severity,
                "category": i.category,
                "target_ids": i.target_ids,
                "message": i.message,
            }
            for i in issues
        ],
    }
    (package_dir / "SEAAValidationReport.json").write_text(
        json.dumps(validation_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return validation_report
