"""Execution plan for a PPTX backend. This module does not replace that backend."""

from __future__ import annotations


def build_plan(state: dict, ir: dict) -> dict:
    validation = ir["validation_state"]["status"]
    slides = []
    for spec in ir["slide_specifications"]:
        slides.append({
            "slide_id": spec["slide_id"],
            "layout_preserved": True,
            "on_slide_text": spec.get("on_slide_text"),
            "speaker_notes": spec.get("speaker_notes"),
            "overflow_policy": spec.get("overflow_policy"),
            "evidence_ids": [
                binding["evidence_ids"]
                for binding in ir["evidence_bindings"]
                if binding["claim_id"] in (spec.get("depends_on") or [])
            ],
        })
    return {
        "schema_version": "backend-execution-plan/1",
        "deck_ir_schema": ir["schema_version"],
        "run_id": state.get("run_id"),
        "backend": {
            "id": "unbound",
            "production_backend_present": False,
            "reason": "No installed backend consumes Academic Deck IR. ppt-master and the workspace build_deck.py are separate generators.",
        },
        "backend_status": "ready" if validation == "accepted" else "blocked",
        "slide_build_tasks": slides,
        "asset_references": ir["asset_bindings"],
        "layout_instructions": {
            "source": (ir["design_bindings"] or {}).get("package_path"),
            "constraints_dropped": False,
        },
        "citation_instructions": ir["evidence_bindings"],
        "validation_requirements": ["ir_schema", "module_acceptance", "evidence_gate"],
        "output_artifact_targets": [],
    }
