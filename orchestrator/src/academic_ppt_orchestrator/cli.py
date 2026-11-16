"""Command line for a single run or a checkpoint resume."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from academic_ppt_orchestrator.workflow.graph import invoke_run, resume_run


def _read_task(path: Path) -> dict:
    task = json.loads(path.read_text(encoding="utf-8-sig"))
    task["python"] = task.get("python") or sys.executable
    return task


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="academic-ppt-orchestrator")
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--task", type=Path, required=True)
    run_parser.add_argument("--out", type=Path, required=True)
    run_parser.add_argument("--run-id", default=None)
    resume_parser = sub.add_parser("resume")
    resume_parser.add_argument("--run-dir", type=Path, required=True)
    resume_parser.add_argument("--thread-id", required=True)
    args = parser.parse_args(argv)
    if args.command == "run":
        task = _read_task(args.task)
        run_id = args.run_id or task.get("run_id") or uuid.uuid4().hex[:12]
        run_dir = args.out / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        result = invoke_run(task, str(run_dir), run_id, str(run_dir / "checkpoints.sqlite"))
    else:
        run_dir = args.run_dir
        result = resume_run(str(run_dir), args.thread_id, str(run_dir / "checkpoints.sqlite"))
        run_id = args.thread_id
    summary = {
        "run_id": run_id,
        "terminal": result.get("terminal"),
        "reason": result.get("terminal_reason"),
        "run_dir": str(run_dir),
        "ir_path": result.get("ir_path"),
        "plan_path": result.get("plan_path"),
        "pptx_path": result.get("pptx_path"),
        "evidence_gate": (result.get("evidence_decision") or {}).get("gate"),
        "iteration": result.get("iteration"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["terminal"] in ("SUCCEEDED", "PARTIAL", "BLOCKED") else 1


if __name__ == "__main__":
    raise SystemExit(main())
