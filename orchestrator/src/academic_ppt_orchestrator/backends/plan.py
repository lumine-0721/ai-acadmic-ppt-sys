"""Execution plan for a PPTX backend. This module does not replace that backend."""

from __future__ import annotations

from academic_ppt_orchestrator.contracts.ir import IR_VERSION, VALIDATOR


def build_plan(state: dict, ir: dict) -> dict:
    if ir.get("schema_version") != IR_VERSION:
        raise ValueError(f"Unsupported Deck IR {ir.get('schema_version')!r}; expected {IR_VERSION}. "
                         "Reassemble from module artifacts in a new run; do not relabel an old IR.")
    VALIDATOR.validate(ir)
    validation = ir["validation_state"]["status"]
    return _assemble_plan(state, ir, validation)


def blocked_schema_plan(state: dict, ir: dict) -> dict:
    """Record the failed assembly without emitting instructions from invalid IR."""
    return _assemble_plan(state, ir, "blocked", ir["validation_state"]["schema_errors"])


def _assemble_plan(state: dict, ir: dict, validation: str, schema_errors: list | None = None) -> dict:
    slides = []
    evidence = [] if schema_errors else ir["evidence_bindings"]
    for spec in ([] if schema_errors else ir["slide_specifications"]):
        bindings = [binding for binding in evidence
                    if binding["claim_id"] in (spec.get("depends_on") or [])]
        slides.append({
            "slide_id": spec["slide_id"],
            "status": spec["status"],
            "status_sources": spec["status_sources"],
            "status_reasons": spec["status_reasons"],
            "evidence_gaps": spec["evidence_gaps"],
            "layout_preserved": True,
            "on_slide_text": spec.get("on_slide_text"),
            "speaker_notes": spec.get("speaker_notes"),
            "overflow_policy": spec.get("overflow_policy"),
            # Legacy field is now explicitly supporting evidence, a flat unique list.
            "evidence_ids": sorted({eid for binding in bindings
                                    if binding["relation"] == "supports" and binding["eligible_support"]
                                    for eid in binding["evidence_ids"]}),
            "evidence_bindings": bindings,
        })
    return {
        "schema_version": "backend-execution-plan/2",
        "deck_ir_schema": ir["schema_version"],
        "run_id": state.get("run_id"),
        "backend": {
            "id": "unbound",
            "production_backend_present": False,
            "reason": "No installed backend consumes Academic Deck IR. ppt-master and the workspace build_deck.py are separate generators.",
        },
        "input_status": "ready" if validation == "accepted" and slides and all(s["status"] == "READY" for s in slides) else "blocked",
        "backend_status": "blocked",  # An unbound production backend never becomes executable.
        "slide_build_tasks": slides,
        "asset_references": [] if schema_errors else ir["asset_bindings"],
        "layout_instructions": {
            "source": (ir["design_bindings"] or {}).get("package_path"),
            "constraints_dropped": False,
        },
        "citation_instructions": evidence,
        "validation_requirements": ["ir_schema", "module_acceptance", "evidence_gate", "slide_readiness"],
        **({"schema_errors": schema_errors} if schema_errors else {}),
        "output_artifact_targets": [],
    }
