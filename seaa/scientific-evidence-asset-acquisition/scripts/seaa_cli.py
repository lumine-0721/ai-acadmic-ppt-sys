#!/usr/bin/env python3
"""Unified SEAA CLI for AI PPT integration and Research Core orchestration.

Provides subcommands:
- discover       : Literature discovery, deduplication, ranking, and ABSTRACT_ONLY isolation
- extract-pdf    : Full-text PDF section parsing + Figure/Table/Multi-panel extraction
- ground         : Claim-Evidence grounding, condition/measurement extraction, and asset binding
- build-package  : Compile a ResearchBundle (+ optional APC PPT_HANDOFF.json) into EVIDENCE_PACKAGE/
- audit          : Scientific consistency checking + check_evidence.py validation
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import check_evidence
from research_core import cli as core_cli
from research_core.models import ResearchBundle
from seaa_adapter import compile_evidence_package_from_bundle


def cmd_build_package(args: argparse.Namespace) -> int:
    bundle = ResearchBundle.load(args.bundle)
    report = compile_evidence_package_from_bundle(
        bundle,
        args.out_dir,
        handoff_path=args.handoff,
        mode=args.mode,
        scope=args.scope,
    )
    pkg = check_evidence.check(args.out_dir, args.handoff, args.production)
    h = pkg["header"]
    print(
        f"PASS seaa_build_package revision={h['package_revision']} "
        f"sources={report['counts']['sources']} evidence={report['counts']['evidence']} "
        f"assets={report['counts']['assets']} slides={report['counts']['slides']} "
        f"ready={report['counts']['ready_slides']}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    bp = sub.add_parser("build-package", help="Compile ResearchBundle into AI PPT EVIDENCE_PACKAGE/")
    bp.add_argument("bundle", type=Path)
    bp.add_argument("--out-dir", type=Path, required=True)
    bp.add_argument("--handoff", type=Path, default=None)
    bp.add_argument("--mode", type=str, default="FULL_RESEARCH")
    bp.add_argument("--scope", type=str, default="full")
    bp.add_argument("--production", action="store_true")
    bp.set_defaults(func=cmd_build_package)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    args_list = list(argv if argv is not None else sys.argv[1:])
    if args_list and args_list[0] in ("discover", "ingest-pdf", "ground", "audit", "synthesize"):
        return core_cli.main(args_list)
    parser = build_parser()
    args = parser.parse_args(args_list)
    try:
        return args.func(args)
    except Exception as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
