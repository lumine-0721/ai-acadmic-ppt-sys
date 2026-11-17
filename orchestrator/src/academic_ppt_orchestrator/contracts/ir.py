"""Build Academic Deck IR from module artifacts. Missing semantics stay explicit."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from academic_ppt_orchestrator.adapters.common import read_json

SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "academic-deck-ir.schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA)
IR_VERSION = "academic-deck-ir/1"
SLIDE_STATUS_ORDER = {"READY": 0, "PROVISIONAL": 1, "BLOCKED": 2}


def _load(path: str | None) -> dict | None:
    if path and Path(path).is_file():
        return read_json(Path(path))
    return None


def merge_slide_status(record: dict, evidence_rows: list[dict], apc_ok: bool,
                       seaa_ok: bool) -> dict:
    """Readiness is a conjunction, not last-writer-wins. Never alter module input."""
    rows = [row for row in evidence_rows if not row.get("retired")]
    evidence = rows[0] if len(rows) == 1 else {}
    original = {"apc": record.get("status"), "seaa": evidence.get("status")}
    reasons = []
    status = original["apc"]
    if not isinstance(status, str) or status not in SLIDE_STATUS_ORDER:
        status = "BLOCKED"
        reasons.append("apc_status_unknown")
    if not rows:
        status = max((status, "PROVISIONAL"), key=SLIDE_STATUS_ORDER.get)
        reasons.append("seaa_slide_assessment_missing")
    elif len(rows) != 1:
        status = "BLOCKED"
        reasons.append("seaa_slide_assessment_ambiguous")
    elif not isinstance(original["seaa"], str) or original["seaa"] not in SLIDE_STATUS_ORDER:
        status = "BLOCKED"
        reasons.append("seaa_status_unknown")
    else:
        status = max((status, original["seaa"]), key=SLIDE_STATUS_ORDER.get)
    for module, accepted in (("apc", apc_ok), ("seaa", seaa_ok)):
        if not accepted:
            status = "BLOCKED"
            reasons.append(module + "_validation_not_accepted")
        if original[module] in ("PROVISIONAL", "BLOCKED"):
            reasons.append(module + "_" + original[module].lower())
    if record.get("retired"):
        status = "BLOCKED"
        reasons.append("apc_slide_retired")
    gaps = list(evidence.get("gaps") or [])
    if gaps and status == "READY":
        status = "PROVISIONAL"
        reasons.append("seaa_unresolved_gaps")
    return {"status": status, "status_sources": original,
            "status_reasons": reasons or ["both_modules_ready"], "evidence_gaps": gaps}


def build_ir(state: dict) -> dict:
    handoff_path = state.get("handoff_path")
    handoff = _load(handoff_path) or {}
    records = handoff.get("records") or {}
    manifest = ((state.get("seaa_result") or {}).get("payload") or {}).get("manifest") or {}
    supports = ((state.get("seaa_result") or {}).get("validation_report") or {}).get("supports") or {}
    package = state.get("design_package")
    design = _load(str(Path(package) / "DESIGN_SYSTEM.json")) if package and Path(package).is_dir() else None
    layouts = _load(str(Path(package) / "LAYOUT_LIBRARY.json")) if package and Path(package).is_dir() else None
    mapping = _load(str(Path(package) / "SLIDE_STYLE_MAPPING.json")) if package and Path(package).is_dir() else None
    assets = _load(str(Path(state.get("evidence_package") or "") / "03_ASSET_MANIFEST.json")) if state.get("evidence_package") else None
    decision = state.get("evidence_decision") or {"gate": "missing", "reason": "not evaluated"}
    evidence_gate = decision.get("gate")
    conflicts = list((state.get("conflicts") or {}).values())
    apc_ok = (state.get("apc_result") or {}).get("acceptance") == "accepted"
    aprd_acceptance = (state.get("aprd_result") or {}).get("acceptance")
    aprd_ok = aprd_acceptance == "accepted"
    seaa_ok = ((state.get("seaa_result") or {}).get("acceptance") == "accepted"
               and evidence_gate in ("sufficient", "partial", "insufficient"))
    # No submitted assessment is not a validation failure. The deck is still blocked.
    seaa_absent = evidence_gate == "missing" and not manifest
    adjustments = list(state.get("adjustments") or [])
    assessments: dict[str, list[dict]] = {}
    for row in manifest.get("slides") or []:
        assessments.setdefault(row.get("slide_id"), []).append(row)
    slides = []
    for slide_id in handoff.get("slide_order") or []:
        record = records.get(slide_id) or {}
        overflow = next((item for item in conflicts if item.get("slide_id") == slide_id), None)
        readiness = merge_slide_status(record, assessments.get(slide_id, []), apc_ok, seaa_ok or seaa_absent)
        if readiness["status"] != record.get("status"):
            adjustments.append({"kind": "merge_slide_readiness", "slide_id": slide_id, **readiness})
        slides.append({
            "slide_id": slide_id,
            "summary": record.get("summary"),
            **readiness,
            "depends_on": record.get("depends_on") or [],
            "on_slide_text": record.get("summary"),
            "speaker_notes": record.get("summary") if overflow else None,
            "overflow_policy": overflow.get("resolution") if overflow else None,
            "layout_preserved": True,
        })
    bindings = []
    for claim_id, evidence_ids in sorted(supports.items()):
        bindings.append({
            "claim_id": claim_id,
            "evidence_ids": evidence_ids,
            "source_ids": sorted({
                item.get("source_id")
                for item in manifest.get("evidence") or []
                if item.get("evidence_id") in evidence_ids and item.get("source_id")
            }),
        })
    asset_bindings = []
    for asset in (assets or {}).get("assets") or []:
        asset_bindings.append({
            "asset_id": asset.get("asset_id"),
            "path": asset.get("path"),
            "sha256": asset.get("sha256"),
            "rights": asset.get("rights"),
            "scientific_immutable": asset.get("scientific_immutable"),
        })
    reasons = []
    if not apc_ok:
        reasons.append("apc_not_accepted")
    if not aprd_ok:
        reasons.append("aprd_not_accepted")
    if evidence_gate != "sufficient":
        reasons.append("evidence_" + str(evidence_gate))
    if conflicts:
        reasons.append("design_conflict_recorded")
    if not slides:
        reasons.append("slides_missing")
    for slide in slides:
        if slide["status"] != "READY":
            reasons.append(f"slide_{slide['slide_id']}_{slide['status'].lower()}")
    if (not apc_ok or not seaa_ok or not slides
            or aprd_acceptance not in ("accepted", "conflict") or evidence_gate == "insufficient"
            or any(slide["status"] == "BLOCKED" for slide in slides)):
        status = "blocked"
    elif apc_ok and aprd_ok and evidence_gate == "sufficient" and not conflicts and all(s["status"] == "READY" for s in slides):
        status = "accepted"
    else:
        status = "partial"
    ir = {
        "schema_version": IR_VERSION,
        "deck_metadata": {
            "project_id": handoff.get("project_id"),
            "revision": handoff.get("revision"),
            "run_id": state.get("run_id"),
        },
        "presentation_requirements": {"path": state.get("requirements_path")},
        "narrative_structure": {
            "slide_order": handoff.get("slide_order") or [],
            "claims": {
                key: value for key, value in records.items() if key.startswith("C")
            },
        },
        "evidence_bindings": bindings,
        "design_bindings": {
            "package_path": package,
            "design_system": design,
            "layouts": layouts,
            "mapping": mapping,
            "constraints_dropped": False,
        },
        "asset_bindings": asset_bindings,
        "slide_specifications": slides,
        "validation_state": {
            "status": status,
            "reasons": reasons,
            "evidence_gate": decision.get("gate"),
            "evidence_reason": decision.get("reason"),
            "conflicts": conflicts,
            "adjustments": adjustments,
        },
        "execution_metadata": {
            "module_runs": [
                {
                    "module_id": item.get("module_id"),
                    "module_version": item.get("module_version"),
                    "execution_status": item.get("execution_status"),
                    "acceptance": item.get("acceptance"),
                    "duration_ms": item.get("duration_ms"),
                }
                for item in state.get("module_runs") or []
            ]
        },
    }
    errors = sorted(VALIDATOR.iter_errors(ir), key=lambda item: str(list(item.path)))
    if errors:
        ir["validation_state"]["status"] = "blocked"
        ir["validation_state"]["schema_errors"] = [err.message for err in errors]
    return ir
