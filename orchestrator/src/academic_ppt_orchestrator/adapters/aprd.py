"""APRD adapter. Calls the installed package checker and PPTX inspector. Does not redraw layouts."""

from __future__ import annotations

from pathlib import Path

from academic_ppt_orchestrator.adapters.common import (
    artifact_ref,
    blank_result,
    cache_get,
    invoke,
    log_call,
    read_json,
    remember,
)
from academic_ppt_orchestrator.config import module_roots
from academic_ppt_orchestrator.hashing import hash_tree, sha256_file


def _root() -> Path:
    return module_roots()["aprd"]


def _version() -> str:
    script = _root() / "scripts" / "check_package.py"
    return "check_package.py:" + (sha256_file(script)[:12] if script.is_file() else "missing")


def execute(state: dict) -> dict:
    result = blank_result("aprd", _version(), state["run_id"])
    package = state.get("design_package")
    pptx = state.get("reference_pptx")
    handoff = state.get("handoff_path")
    if not package and not pptx:
        result["error"] = {"type": "missing_input", "message": "APRD needs a PPT_REFERENCE_PACKAGE or a reference PPTX."}
        log_call(state, "aprd", "blocked", {"reason": "missing_input"})
        return result
    identity = {
        "package": hash_tree(Path(package)) if package and Path(package).exists() else None,
        "pptx": sha256_file(Path(pptx)) if pptx and Path(pptx).is_file() else None,
        "handoff_revision": None,
    }
    if handoff and Path(handoff).is_file():
        index = read_json(Path(handoff))
        identity["handoff_revision"] = [index.get("project_id"), index.get("revision"), index.get("slide_order")]
    from academic_ppt_orchestrator.hashing import sha256_json
    input_hash = sha256_json(identity)
    cached = cache_get(state, "aprd.execute", input_hash)
    if cached:
        log_call(state, "aprd", "skip", {"cache_key": "aprd.execute", "input_hash": input_hash})
        return cached
    python = state["task"].get("python")
    commands = []
    if pptx and Path(pptx).is_file():
        out = Path(state["run_dir"]) / "modules" / "aprd" / "inspect_pptx.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        commands.append(invoke(
            state,
            "aprd",
            [python, str(_root() / "scripts" / "inspect_pptx.py"), pptx, str(out)],
            float(state["budgets"]["timeout_seconds"]),
        ))
        result["output_artifact_refs"].append(artifact_ref(out, "pptx_inspection", "inspect_pptx"))
    if package and Path(package).is_dir():
        argv = [python, str(_root() / "scripts" / "check_package.py"), "validate", package]
        if handoff and Path(handoff).is_file():
            argv.extend(["--handoff", handoff])
        commands.append(invoke(state, "aprd", argv, float(state["budgets"]["timeout_seconds"])))
        result["input_artifact_refs"].append(artifact_ref(Path(package), "design_package", "ppt-reference-package"))
    result["duration_ms"] = sum(item["duration_ms"] for item in commands)
    result["validation_report"] = {"commands": commands}
    result["provenance"] = {"entrypoint": str(_root() / "scripts"), "fusion_engine_invoked": False}
    hard_fail = [item for item in commands if item["returncode"] != 0]
    result["execution_status"] = "SUCCEEDED" if commands and not any(item["timed_out"] for item in commands) else "FAILED"
    if not commands:
        result["acceptance"] = "blocked"
    elif hard_fail:
        result["acceptance"] = "conflict" if package and handoff else "blocked"
        result["error"] = {"type": "validation", "message": (hard_fail[0]["stderr"] or hard_fail[0]["stdout"]).strip()}
    else:
        result["acceptance"] = "accepted"
    result["payload"] = {"design_package": package, "reference_pptx": pptx}
    return remember(state, "aprd.execute", input_hash, result)
