"""Shared adapter helpers. User inputs are read. Writes stay inside the run directory."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from academic_ppt_orchestrator.hashing import hash_tree, sha256_json
from academic_ppt_orchestrator.journal import append_event
from academic_ppt_orchestrator.process import run_command


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def blank_result(module_id: str, version: str, run_id: str) -> dict:
    return {
        "module_id": module_id,
        "module_version": version,
        "run_id": run_id,
        "input_schema_version": None,
        "output_schema_version": None,
        "input_artifact_refs": [],
        "output_artifact_refs": [],
        "execution_status": "BLOCKED",
        "acceptance": "blocked",
        "validation_report": {},
        "error": None,
        "duration_ms": 0,
        "provenance": {},
        "payload": {},
    }


def artifact_ref(path: Path, role: str, schema_version: str | None = None) -> dict:
    target = path.resolve()
    return {
        "role": role,
        "path": str(target),
        "sha256": hash_tree(target) if target.exists() else None,
        "schema_version": schema_version,
    }


def cache_get(state: dict, key: str, input_hash: str) -> dict | None:
    found = (state.get("cache") or {}).get(key)
    if not found or found.get("input_hash") != input_hash:
        return None
    if found.get("execution_status") not in ("SUCCEEDED", "PARTIAL", "BLOCKED"):
        return None
    for ref in found.get("output_artifact_refs") or []:
        if ref.get("path") and not Path(ref["path"]).exists():
            return None
    return found


def remember(state: dict, key: str, input_hash: str, result: dict) -> dict:
    stored = dict(result)
    stored["input_hash"] = input_hash
    stored["cache_key"] = key
    return stored


def log_call(state: dict, module_id: str, action: str, detail: dict) -> None:
    append_event(state["run_dir"], {"module_id": module_id, "action": action, **detail})


def invoke(state: dict, module_id: str, argv: list[str], timeout: float) -> dict:
    attempts = 0
    budget = int(state["budgets"].get("retry_budget", 0))
    last = None
    while attempts <= budget:
        attempts += 1
        log_call(state, module_id, "exec", {"argv": [str(part) for part in argv], "attempt": attempts})
        last = run_command(argv, timeout)
        if not last.timed_out:
            return last.as_dict() | {"attempts": attempts}
    return last.as_dict() | {"attempts": attempts}


def claim_status_hash(handoff_path: str | None) -> str:
    if not handoff_path or not Path(handoff_path).is_file():
        return "missing"
    data = read_json(Path(handoff_path))
    brief = {
        "project_id": data.get("project_id"),
        "revision": data.get("revision"),
        "slide_order": data.get("slide_order"),
        "claims": {
            key: {
                "kind": value.get("kind"),
                "status": value.get("status"),
                "origin": value.get("origin"),
                "retired": bool(value.get("retired")),
            }
            for key, value in (data.get("records") or {}).items()
            if key.startswith("C")
        },
    }
    return sha256_json(brief)
