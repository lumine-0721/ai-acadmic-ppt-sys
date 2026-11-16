"""Deterministic cross-module rules. These do not ask a model to score the science."""

from __future__ import annotations

from academic_ppt_orchestrator.adapters.common import read_json
from pathlib import Path


def verified_findings(handoff: dict) -> list[str]:
    found = []
    for key, record in (handoff.get("records") or {}).items():
        if record.get("retired") or not key.startswith("C"):
            continue
        if record.get("kind") == "finding" and record.get("status") in ("VERIFIED", "USER_PROVIDED"):
            found.append(key)
    return sorted(found)


def evidence_decision(apc_result: dict, seaa_result: dict) -> dict:
    if not seaa_result or seaa_result.get("payload", {}).get("evidence_gate_hint") == "missing" or not seaa_result.get("payload", {}).get("evidence_package"):
        if seaa_result and seaa_result.get("error", {}) and seaa_result["error"].get("type") == "missing_input":
            return {"gate": "missing", "claim_ids": [], "reason": seaa_result["error"]["message"]}
    if not seaa_result or seaa_result.get("acceptance") == "blocked" and not (seaa_result.get("payload") or {}).get("manifest"):
        message = (seaa_result or {}).get("error", {}) or {}
        if message.get("type") == "missing_input":
            return {"gate": "missing", "claim_ids": [], "reason": message.get("message")}
        return {"gate": "blocked", "claim_ids": [], "reason": message.get("message") or "SEAA did not return a readable package."}
    manifest = (seaa_result.get("payload") or {}).get("manifest") or {}
    handoff_path = (apc_result.get("payload") or {}).get("handoff_path")
    if not handoff_path or not Path(handoff_path).is_file():
        return {"gate": "blocked", "claim_ids": [], "reason": "APC handoff is unavailable, so evidence cannot be bound."}
    handoff = read_json(Path(handoff_path))
    if manifest.get("project_id") and manifest.get("project_id") != handoff.get("project_id"):
        return {
            "gate": "conflict",
            "claim_ids": [],
            "reason": "SEAA project_id does not match APC project_id.",
        }
    base_revision = manifest.get("base_revision")
    handoff_revision = handoff.get("revision")
    if base_revision is not None and handoff_revision is not None and int(base_revision) > int(handoff_revision):
        return {
            "gate": "conflict",
            "claim_ids": [],
            "reason": "SEAA base_revision is newer than the APC revision.",
        }
    supports = (seaa_result.get("validation_report") or {}).get("supports") or {}
    missing = [claim_id for claim_id in verified_findings(handoff) if claim_id not in supports]
    gaps = []
    for slide in manifest.get("slides") or []:
        if slide.get("gaps"):
            gaps.append({"slide_id": slide.get("slide_id"), "status": slide.get("status"), "gaps": slide.get("gaps")})
    if seaa_result.get("acceptance") != "accepted":
        return {"gate": "blocked", "claim_ids": missing, "reason": (seaa_result.get("error") or {}).get("message"), "gaps": gaps}
    if missing:
        return {"gate": "insufficient", "claim_ids": missing, "reason": "Verified findings have no SEAA supports link.", "gaps": gaps}
    if any(slide.get("status") != "READY" for slide in manifest.get("slides") or []):
        return {"gate": "partial", "claim_ids": [], "reason": "SEAA package is valid, but at least one slide is not READY.", "gaps": gaps}
    return {"gate": "sufficient", "claim_ids": [], "reason": "SEAA supports every verified finding.", "gaps": gaps}


def _capacity(layout: dict) -> int | None:
    capacity = layout.get("content_capacity")
    if not isinstance(capacity, dict):
        return None
    for key in ("headline_max_chars", "max_chars", "body_max_chars"):
        value = capacity.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return None


def design_conflicts(apc_result: dict, aprd_result: dict) -> list[dict]:
    package = (aprd_result.get("payload") or {}).get("design_package") if aprd_result else None
    handoff_path = (apc_result.get("payload") or {}).get("handoff_path") if apc_result else None
    if not package or not handoff_path or not Path(package).is_dir() or not Path(handoff_path).is_file():
        return []
    if aprd_result.get("acceptance") not in ("accepted", "conflict"):
        return []
    handoff = read_json(Path(handoff_path))
    library_path = Path(package) / "LAYOUT_LIBRARY.json"
    mapping_path = Path(package) / "SLIDE_STYLE_MAPPING.json"
    if not library_path.is_file() or not mapping_path.is_file():
        return []
    layouts = {row["layout_id"]: row for row in read_json(library_path).get("layouts") or []}
    mapping = read_json(mapping_path)
    conflicts = []
    for row in mapping.get("mappings") or []:
        slide_id = row.get("target_slide_id")
        layout = layouts.get(row.get("selected_layout_id"))
        if not slide_id or not layout:
            continue
        limit = _capacity(layout)
        summary = ((handoff.get("records") or {}).get(slide_id) or {}).get("summary") or ""
        if limit is not None and len(summary) > limit:
            conflicts.append({
                "id": "content_vs_design:" + slide_id,
                "type": "content_vs_design",
                "slide_id": slide_id,
                "layout_id": row.get("selected_layout_id"),
                "summary_chars": len(summary),
                "capacity_chars": limit,
                "resolution": "keep_layout_move_overflow_to_speaker_notes",
                "dropped_design_constraints": False,
            })
    return conflicts
