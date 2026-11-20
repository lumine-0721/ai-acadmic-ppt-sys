"""pptv01 command line.

  pptv01 run "帮我做一个城市热岛效应的 PPT。" --out runs/
  pptv01 export-schemas [--out contracts/]
  pptv01 validate <kind> <file.json>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pptv01.contracts import CONTRACT_FILES, RunOverall, UserInputRequests, export_schemas, load_contract
from pptv01.llm import AwaitingAgentResponse, LLMConfigError, LLMError, client_from_env
from pptv01.runner import run_pipeline
from pptv01.user_requests import USER_INPUT_REQUESTS_FILE, provide_user_input


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        client = client_from_env()
    except LLMConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    try:
        report, run_dir = run_pipeline(
            args.utterance,
            Path(args.out),
            client,
            run_id=args.run_id,
            resume=args.resume,
            on_missing=args.on_missing,
        )
    except AwaitingAgentResponse as e:
        print(str(e), file=sys.stderr)
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
    if report.user_input_requests:
        u = report.user_input_requests
        print(f"requests: {u.open_count} open ({u.blocking_open_count} blocking), {u.provided_count} provided, {u.placeholder_count} placeholders")
    if report.overall is RunOverall.needs_input:
        print(f"waiting for user input: run `pptv01 requests {run_dir}` and `pptv01 provide ...`, then `--resume`", file=sys.stderr)
        return 4
    if report.failure_reason:
        print(f"failure: {report.failure_reason}", file=sys.stderr)
        return 1
    return 0


def _cmd_gates(args: argparse.Namespace) -> int:
    from pptv01.grading.gates import check_gates
    from pptv01.contracts import write_contract
    rep = check_gates(Path(args.run_dir))
    write_contract(rep, Path(args.run_dir) / "gate_report.json")
    for r in rep.results:
        print(f"  {r.status:<8} {r.gate}  {r.detail}")
    print(f"overall: {rep.overall}")
    return 0 if rep.overall != "fail" else 1


def _cmd_grade(args: argparse.Namespace) -> int:
    """PPTEval grade. --slides-dir DIR grades rendered slide_*.png|jpg (pptx_render); default is the plan-text proxy."""
    from pptv01.contracts import SlidePlan, write_contract
    from pptv01.grading.ppteval import (GraderConfigError, SlideEvidence, grade_deck, grader_from_env,
                                        slide_text_from_plan)
    run_dir = Path(args.run_dir)
    try:
        grader = grader_from_env()
    except GraderConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    try:
        plan = load_contract(SlidePlan, run_dir / "slide_plan.json")
        ev = slide_text_from_plan(plan)
        level = "plan_text_proxy"
        if args.slides_dir:
            imgs = sorted(Path(args.slides_dir).glob("slide_*.*"))
            if len(imgs) != len(ev):
                print(f"error: {len(imgs)} images but {len(ev)} planned slides", file=sys.stderr)
                return 2
            for e, im in zip(ev, imgs):
                e.image = im
            level = "pptx_render"
        rep, trace = grade_deck(ev, grader, run_id=plan.run_id, task=args.task or run_dir.name, level=level)
    except AwaitingAgentResponse as e:
        print(str(e), file=sys.stderr)
        return 3
    except (ValueError, FileNotFoundError, LLMError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    finally:
        grader.close()
    write_contract(rep, run_dir / "grade_report.json")
    (run_dir / "llm_trace").mkdir(exist_ok=True)
    import json
    (run_dir / "llm_trace" / "grade.json").write_text(json.dumps({"grader": rep.grader.model_dump(), "trace": trace}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sm = rep.summary
    print(f"grader: {rep.grader.label}   evidence: {rep.evidence_level}")
    print(f"content_mean={sm.content_mean} design_mean={sm.design_mean} coherence={sm.coherence} passed={sm.passed} not_assessed={sm.not_assessed}")
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


def _cmd_requests(args: argparse.Namespace) -> int:
    req_path = Path(args.run_dir) / USER_INPUT_REQUESTS_FILE
    if not req_path.is_file():
        print(f"error: {req_path} not found", file=sys.stderr)
        return 2
    uir = load_contract(UserInputRequests, req_path)
    print(f"summary: {uir.summary}")
    for r in uir.requests:
        blk = "blocking" if r.blocking else "optional"
        prov = f" -> {r.provided.file}" if r.provided else ""
        print(f"  {r.request_id}  [{r.status.value}]  {r.kind.value:<9}  {blk:<8}  pages={','.join(r.page_ids)}  {r.need}{prov}")
    return 0


def _cmd_provide(args: argparse.Namespace) -> int:
    try:
        uir = provide_user_input(
            Path(args.run_dir),
            args.request_id,
            Path(args.file),
            source=args.source or "",
            license_str=args.license or "",
            note=args.note or "",
        )
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    req = next(r for r in uir.requests if r.request_id == args.request_id)
    print(f"updated {req.request_id}: status={req.status.value} file={req.provided.file} sha256={req.provided.sha256[:12]}...")
    print(f"summary: {uir.summary}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pptv01", description="AI PPT Sys V0.1")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the chain for one utterance")
    r.add_argument("utterance")
    r.add_argument("--out", default="runs", help="root dir; creates <out>/<run_id>/")
    r.add_argument("--run-id", default=None)
    r.add_argument("--resume", action="store_true", help="re-enter an existing run dir (agent backend: replays answered prompts)")
    r.add_argument("--on-missing", choices=["placeholder", "wait"], default="placeholder",
                   help="wait: pause after D (exit 4) when assets are missing; placeholder: mark explicit placeholders")
    r.set_defaults(fn=_cmd_run)
    e = sub.add_parser("export-schemas", help="write JSON Schemas from the Pydantic contracts")
    e.add_argument("--out", default="contracts")
    e.set_defaults(fn=_cmd_export)
    v = sub.add_parser("validate", help="validate a contract file")
    v.add_argument("kind", choices=sorted(CONTRACT_FILES))
    v.add_argument("file")
    v.set_defaults(fn=_cmd_validate)
    rq = sub.add_parser("requests", help="list user_input_requests.json for a run")
    rq.add_argument("run_dir")
    rq.set_defaults(fn=_cmd_requests)
    pv = sub.add_parser("provide", help="supply a verified file/dataset for a user input request (U###)")
    pv.add_argument("run_dir")
    pv.add_argument("request_id")
    pv.add_argument("--file", required=True, help="path to local image/JSON/CSV file")
    pv.add_argument("--source", default="", help="required attribution/dataset source (URL/DOI/origin)")
    pv.add_argument("--license", default="", help="required licence or usage terms")
    pv.add_argument("--note", default="")
    pv.set_defaults(fn=_cmd_provide)
    g = sub.add_parser("grade", help="PPTEval-style quality grade of a run (env PPTV01_GRADER_*)")
    g.add_argument("run_dir")
    g.add_argument("--slides-dir", default=None, help="rendered slide_*.png|jpg; omit for the plan-text proxy (Design not assessable)")
    g.add_argument("--task", default=None)
    g.set_defaults(fn=_cmd_grade)
    gt = sub.add_parser("gates", help="deterministic hard gates for a run dir (no model involved)")
    gt.add_argument("run_dir")
    gt.set_defaults(fn=_cmd_gates)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
