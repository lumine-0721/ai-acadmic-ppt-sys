"""pptv01 command line.

  pptv01 run "帮我做一个城市热岛效应的 PPT。" --out runs/
  pptv01 export-schemas [--out contracts/]
  pptv01 validate <kind> <file.json>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pptv01.contracts import CONTRACT_FILES, export_schemas, load_contract
from pptv01.llm import AwaitingAgentResponse, LLMConfigError, LLMError, client_from_env
from pptv01.runner import run_pipeline


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        client = client_from_env()
    except LLMConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    try:
        report, run_dir = run_pipeline(args.utterance, Path(args.out), client, run_id=args.run_id, resume=args.resume)
    except AwaitingAgentResponse as e:
        print(f"awaiting agent response: {e}", file=sys.stderr)
        return 3
    except (ValueError, FileExistsError, LLMError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    finally:
        getattr(client, "close", lambda: None)()
    print(f"run_dir: {run_dir}")
    print(f"overall: {report.overall.value}   model: {report.model_id}   commit: {report.code_commit}")
    for s in report.stages:
        print(f"  {s.stage} {s.name:<24} {s.status.value}")
    if report.failure_reason:
        print(f"failure: {report.failure_reason}", file=sys.stderr)
        return 1
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    for p in export_schemas(Path(args.out)):
        print(p)
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        load_contract(CONTRACT_FILES[args.kind], Path(args.file))
    except Exception as e:  # noqa: BLE001 - report any validation problem
        print(f"INVALID {args.kind}: {e}", file=sys.stderr)
        return 1
    print(f"OK {args.kind}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pptv01", description="AI PPT Sys V0.1")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the chain for one utterance")
    r.add_argument("utterance")
    r.add_argument("--out", default="runs", help="root dir; creates <out>/<run_id>/")
    r.add_argument("--run-id", default=None)
    r.add_argument("--resume", action="store_true", help="re-enter an existing run dir (agent backend: replays answered prompts)")
    r.set_defaults(fn=_cmd_run)
    e = sub.add_parser("export-schemas", help="write JSON Schemas from the Pydantic contracts")
    e.add_argument("--out", default="contracts")
    e.set_defaults(fn=_cmd_export)
    v = sub.add_parser("validate", help="validate a contract file")
    v.add_argument("kind", choices=sorted(CONTRACT_FILES))
    v.add_argument("file")
    v.set_defaults(fn=_cmd_validate)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
