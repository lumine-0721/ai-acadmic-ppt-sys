"""APC adapter. Validates and applies recorded status downgrades. It does not invent claims."""

from __future__ import annotations

import re
from pathlib import Path

from academic_ppt_orchestrator.adapters.common import (
    artifact_ref,
    blank_result,
    cache_get,
    claim_status_hash,
    invoke,
    log_call,
    read_json,
    remember,
    write_json,
)
from academic_ppt_orchestrator.config import module_roots
from academic_ppt_orchestrator.hashing import sha256_file

DISCLOSURE = "证据不足：此条已从已核结论降为待验证假说，编排器未新增实验事实。"


def _script() -> Path:
    return module_roots()["apc"] / "scripts" / "check_handoff.py"


def _version() -> str:
    script = _script()
    return "check_handoff.py:" + (sha256_file(script)[:12] if script.is_file() else "missing")


def validate(state: dict) -> dict:
    requirements = state.get("requirements_path")
    handoff = state.get("handoff_path")
    run_id = state["run_id"]
    result = blank_result("apc", _version(), run_id)
    if not requirements or not handoff or not Path(requirements).is_file() or not Path(handoff).is_file():
        result["error"] = {
            "type": "missing_input",
            "message": "APC deterministic entry is check_handoff.py. No requirements/handoff was supplied, and no compile engine is installed.",
        }
        result["execution_status"] = "BLOCKED"
        result["acceptance"] = "blocked"
        log_call(state, "apc", "blocked", {"reason": "missing_input"})
        return result
    input_hash = sha256_file(Path(requirements)) + ":" + sha256_file(Path(handoff))
    previous = state.get("previous_handoff_path")
    if previous:
        input_hash += ":" + sha256_file(Path(previous))
    cached = cache_get(state, "apc.validate", input_hash)
    if cached:
        log_call(state, "apc", "skip", {"cache_key": "apc.validate", "input_hash": input_hash})
        return cached
    python = state["task"].get("python") or state.get("python")
    argv = [python, str(_script()), "validate", requirements, handoff, "--strict-assets"]
    if previous:
        argv.extend(["--previous", previous])
    command = invoke(state, "apc", argv, float(state["budgets"]["timeout_seconds"]))
    result["duration_ms"] = command["duration_ms"]
    result["input_artifact_refs"] = [
        artifact_ref(Path(requirements), "requirements"),
        artifact_ref(Path(handoff), "handoff", "ppt-handoff"),
    ]
    result["validation_report"] = {"command": command, "claim_hash": claim_status_hash(handoff)}
    result["provenance"] = {"entrypoint": str(_script()), "rewritten": False}
    if command["returncode"] == 0:
        result["execution_status"] = "SUCCEEDED"
        result["acceptance"] = "accepted"
    else:
        result["execution_status"] = "FAILED" if command["timed_out"] else "SUCCEEDED"
        result["acceptance"] = "blocked"
        result["error"] = {"type": "validation", "message": (command["stderr"] or command["stdout"]).strip()}
    result["payload"] = {"handoff_path": handoff, "requirements_path": requirements}
    return remember(state, "apc.validate", input_hash, result)


def _affected_slide_ids(records: dict, claim_ids: list[str]) -> set[str]:
    affected = set(claim_ids)
    changed = True
    while changed:
        changed = False
        for key, record in records.items():
            if record.get("retired") or key in affected:
                continue
            deps = record.get("depends_on") or []
            if any(dep in affected for dep in deps):
                affected.add(key)
                changed = True
    return {key for key in affected if key.startswith("S")}


def revise_for_insufficient_evidence(state: dict, claim_ids: list[str]) -> dict:
    """Lower claim strength only. Sources, locators, and measurements are not edited."""
    result = blank_result("apc", _version(), state["run_id"])
    handoff_path = Path(state["handoff_path"])
    requirements_path = Path(state["requirements_path"])
    data = read_json(handoff_path)
    records = data["records"]
    changed = False
    for claim_id in claim_ids:
        record = records.get(claim_id)
        if not record or record.get("retired"):
            continue
        if record.get("kind") == "finding" and record.get("status") in ("VERIFIED", "USER_PROVIDED"):
            record["kind"] = "hypothesis"
            record["status"] = "HYPOTHESIS"
            record["disclosure"] = record.get("disclosure") or DISCLOSURE
            changed = True
    if not changed:
        result["execution_status"] = "SUCCEEDED"
        result["acceptance"] = "blocked"
        result["error"] = {"type": "no_progress", "message": "No VERIFIED finding could be downgraded."}
        result["payload"] = {"no_progress": True, "handoff_path": str(handoff_path)}
        log_call(state, "apc", "no_progress", {"claim_ids": claim_ids})
        return result
    data["revision"] = int(data["revision"]) + 1
    for slide_id in _affected_slide_ids(records, claim_ids):
        if records[slide_id].get("status") == "READY":
            records[slide_id]["status"] = "PROVISIONAL"
    revision = data["revision"]
    out_dir = Path(state["run_dir"]) / "revisions" / ("v" + str(revision).zfill(3))
    out_handoff = out_dir / "PPT_HANDOFF.json"
    out_requirements = out_dir / "PPT_REQUIREMENTS.md"
    write_json(out_handoff, data)
    text = requirements_path.read_text(encoding="utf-8-sig")
    updated, count = re.subn(r"(?m)^Revision:[ \t]*\d+[ \t]*$", "Revision: " + str(revision), text, count=1)
    if count != 1:
        result["error"] = {"type": "requirements", "message": "Could not update exactly one Revision line."}
        result["acceptance"] = "blocked"
        return result
    out_requirements.write_text(updated if updated.endswith("\n") else updated + "\n", encoding="utf-8", newline="\n")
    previous = str(handoff_path)
    revised_state = dict(state)
    revised_state["handoff_path"] = str(out_handoff)
    revised_state["requirements_path"] = str(out_requirements)
    revised_state["previous_handoff_path"] = previous
    validated = validate(revised_state)
    validated["provenance"] = {
        "entrypoint": str(_script()),
        "rewritten": True,
        "operation": "downgrade_finding_to_hypothesis",
        "claim_ids": claim_ids,
        "preserves": ["source", "locator", "measurements", "supports", "opposes"],
    }
    validated["payload"] = {
        "handoff_path": str(out_handoff),
        "requirements_path": str(out_requirements),
        "previous_handoff_path": previous,
        "no_progress": validated["acceptance"] != "accepted",
        "revision": revision,
    }
    log_call(state, "apc", "revise", {"revision": revision, "acceptance": validated["acceptance"], "claim_ids": claim_ids})
    return validated
