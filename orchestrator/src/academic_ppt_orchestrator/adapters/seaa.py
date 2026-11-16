"""SEAA adapter. Validates an evidence package or compiles one from a ResearchBundle. It does not search the web."""

from __future__ import annotations

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
)
from academic_ppt_orchestrator.config import module_roots
from academic_ppt_orchestrator.hashing import hash_tree, sha256_file, sha256_json


def _root() -> Path:
    return module_roots()["seaa"]


def _version() -> str:
    script = _root() / "scripts" / "check_evidence.py"
    return "check_evidence.py:" + (sha256_file(script)[:12] if script.is_file() else "missing")


def _supports(manifest: dict) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for evidence in manifest.get("evidence") or []:
        if evidence.get("retired") or evidence.get("status") not in ("VERIFIED", "USER_PROVIDED"):
            continue
        for link in evidence.get("claim_links") or []:
            if link.get("relation") == "supports" and link.get("claim_id"):
                found.setdefault(link["claim_id"], []).append(evidence.get("evidence_id"))
    return found


def execute(state: dict) -> dict:
    result = blank_result("seaa", _version(), state["run_id"])
    package = state.get("evidence_package")
    bundle = state.get("research_bundle")
    handoff = state.get("handoff_path")
    if not package and not bundle:
        result["error"] = {
            "type": "missing_input",
            "message": "SEAA was not given an EVIDENCE_PACKAGE or ResearchBundle. No literature was invented.",
        }
        result["payload"] = {"evidence_gate_hint": "missing"}
        log_call(state, "seaa", "blocked", {"reason": "missing_input"})
        return result
    identity = {
        "package": hash_tree(Path(package)) if package and Path(package).exists() else None,
        "bundle": sha256_file(Path(bundle)) if bundle and Path(bundle).is_file() else None,
        "claims": claim_status_hash(handoff),
    }
    input_hash = sha256_json(identity)
    cached = cache_get(state, "seaa.execute", input_hash)
    if cached:
        log_call(state, "seaa", "skip", {"cache_key": "seaa.execute", "input_hash": input_hash})
        return cached
    python = state["task"].get("python")
    commands = []
    produced = package
    if bundle and (not package or not Path(package).is_dir()):
        produced = str(Path(state["run_dir"]) / "modules" / "seaa" / "EVIDENCE_PACKAGE")
        argv = [
            python,
            str(_root() / "scripts" / "seaa_cli.py"),
            "build-package",
            bundle,
            "--out-dir",
            produced,
        ]
        if handoff and Path(handoff).is_file():
            argv.extend(["--handoff", handoff])
        commands.append(invoke(state, "seaa", argv, float(state["budgets"]["timeout_seconds"])))
    if produced and Path(produced).is_dir():
        argv = [python, str(_root() / "scripts" / "check_evidence.py"), "validate", produced]
        if handoff and Path(handoff).is_file():
            argv.extend(["--handoff", handoff])
        commands.append(invoke(state, "seaa", argv, float(state["budgets"]["timeout_seconds"])))
        result["input_artifact_refs"].append(artifact_ref(Path(produced), "evidence_package", "evidence-package"))
    result["duration_ms"] = sum(item["duration_ms"] for item in commands)
    manifest = None
    manifest_path = Path(produced) / "02_EVIDENCE_MANIFEST.json" if produced else None
    if manifest_path and manifest_path.is_file():
        manifest = read_json(manifest_path)
    result["validation_report"] = {"commands": commands, "supports": _supports(manifest or {})}
    result["provenance"] = {"entrypoint": str(_root() / "scripts" / "check_evidence.py"), "online_discovery": False}
    failed = [item for item in commands if item["returncode"] != 0]
    result["execution_status"] = "FAILED" if any(item["timed_out"] for item in commands) else "SUCCEEDED"
    if failed:
        result["acceptance"] = "blocked"
        result["error"] = {"type": "validation", "message": (failed[0]["stderr"] or failed[0]["stdout"]).strip()}
    elif commands:
        result["acceptance"] = "accepted"
    result["payload"] = {"evidence_package": produced, "manifest": manifest}
    return remember(state, "seaa.execute", input_hash, result)
