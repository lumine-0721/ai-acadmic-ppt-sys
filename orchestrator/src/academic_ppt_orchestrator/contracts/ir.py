"""Build Academic Deck IR from module artifacts. Missing semantics stay explicit."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from academic_ppt_orchestrator.adapters.common import read_json

SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "academic-deck-ir.schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA)


def _load(path: str | None) -> dict | None:
    if path and Path(path).is_file():
        return read_json(Path(path))
    return None


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
    conflicts = list((state.get("conflicts") or {}).values())
    slides = []
    for slide_id in handoff.get("slide_order") or []:
        record = records.get(slide_id) or {}
        overflow = next((item for item in conflicts if item.get("slide_id") == slide_id), None)
        slides.append({
            "slide_id": slide_id,
            "summary": record.get("summary"),
            "status": record.get("status"),
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
    apc_ok = (state.get("apc_result") or {}).get("acceptance") == "accepted"
    aprd_ok = (state.get("aprd_result") or {}).get("acceptance") == "accepted"
    evidence_gate = decision.get("gate")
    reasons = []
    if not apc_ok:
        reasons.append("apc_not_accepted")
    if not aprd_ok:
        reasons.append("aprd_not_accepted")
    if evidence_gate != "sufficient":
        reasons.append("evidence_" + str(evidence_gate))
    if conflicts:
        reasons.append("design_conflict_recorded")
    if apc_ok and aprd_ok and evidence_gate == "sufficient" and not conflicts:
        status = "accepted"
    elif evidence_gate in ("missing", "blocked", "conflict", "insufficient"):
        status = "blocked"
    else:
        status = "partial"
    ir = {
        "schema_version": "academic-deck-ir/1",
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
            "adjustments": state.get("adjustments") or [],
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
    errors = sorted(VALIDATOR.iter_errors(ir), key=lambda item: list(item.path))
    if errors:
        ir["validation_state"]["status"] = "blocked"
        ir["validation_state"]["schema_errors"] = [err.message for err in errors]
    return ir
