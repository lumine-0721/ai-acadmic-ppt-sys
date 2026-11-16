#!/usr/bin/env python3
"""Standalone CLI for the Reusable Research Evidence & Asset Core (research_core).

Can be run via `python -m research_core.cli` or the installed `research-core` entry point
with zero dependency on any presentation or slide-deck module.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import List, Optional

from . import __version__
from .consistency import (
    audit_scientific_consistency,
    build_review_synthesis_matrix,
    evaluate_evidence_comparability,
    propagate_invalidation,
)
from .discovery import discover_from_local_catalog, query_crossref_online
from .grounding import ground_claims_to_evidence
from .models import ClaimNode, ProcessingRecord, ResearchBundle
from .pdf_processor import parse_scientific_pdf


def cmd_discover(args: argparse.Namespace) -> int:
    if args.catalog:
        res = discover_from_local_catalog(
            args.catalog,
            query=args.query or "",
            max_results=args.max_results,
        )
        sources = res["selected_sources"]
        passages = res["selected_passages"]
    else:
        online = query_crossref_online(args.query or "scientific evidence", rows=args.max_results)
        sources = online["sources"]
        passages = online["passages"]
        res = online

    bundle = ResearchBundle(
        bundle_id=args.bundle_id,
        title=f"Literature Discovery: {args.query or (args.catalog.stem if args.catalog else 'query')}",
        research_questions=[args.query] if args.query else [],
        sources=sources,
        passages=passages,
        processing_records=[
            ProcessingRecord(
                step_id="STEP001",
                operation="discover_literature",
                timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                input_refs=[str(args.catalog)] if args.catalog else [args.query or ""],
                output_refs=[s.source_id for s in sources],
                status="COMPLETED",
                notes=f"Deduplicated and ranked {len(sources)} sources.",
            )
        ],
        metadata={"discovery_summary": {k: v for k, v in res.items() if k not in ("selected_sources", "selected_passages", "sources", "passages")}},
    )
    bundle.save(args.out)
    print(f"PASS core_discover sources={len(sources)} passages={len(passages)} out={args.out}")
    return 0


def cmd_ingest_pdf(args: argparse.Namespace) -> int:
    pages: Optional[List[int]] = None
    if args.pages:
        pages = []
        for part in args.pages.split(","):
            part = part.strip()
            if "-" in part:
                a, b = part.split("-", 1)
                pages.extend(range(int(a), int(b) + 1))
            elif part:
                pages.append(int(part))
        pages = sorted(set(pages))

    parsed = parse_scientific_pdf(
        args.pdf,
        source_id=args.source_id,
        pages_spec=pages,
        output_assets_dir=args.assets_dir,
        license_hint=args.license,
    )
    if args.bundle and args.bundle.is_file():
        bundle = ResearchBundle.load(args.bundle)
    else:
        bundle = ResearchBundle(bundle_id=args.bundle_id, title=parsed["source"].title)

    bundle.sources = [s for s in bundle.sources if s.source_id != parsed["source"].source_id] + [parsed["source"]]
    bundle.passages = [p for p in bundle.passages if p.source_id != parsed["source"].source_id] + parsed["passages"]
    bundle.assets = [a for a in bundle.assets if a.source_id != parsed["source"].source_id] + parsed["assets"]
    bundle.processing_records.append(
        ProcessingRecord(
            step_id=f"STEP{len(bundle.processing_records) + 1:03d}",
            operation="ingest_pdf",
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            input_refs=[str(args.pdf.resolve())],
            output_refs=[parsed["source"].source_id] + [a.asset_id for a in parsed["assets"]],
            status="COMPLETED",
            notes=f"Extracted {len(parsed['passages'])} passages and {len(parsed['assets'])} assets.",
        )
    )
    bundle.save(args.out)
    print(
        f"PASS core_ingest_pdf source={parsed['source'].source_id} "
        f"passages={len(parsed['passages'])} assets={len(parsed['assets'])} out={args.out}"
    )
    return 0


def cmd_ground(args: argparse.Namespace) -> int:
    bundle = ResearchBundle.load(args.bundle)
    if args.claims_json:
        raw_claims = json.loads(args.claims_json.read_text(encoding="utf-8-sig"))
        if isinstance(raw_claims, dict):
            raw_claims = raw_claims.get("claims", [])
        bundle.claims = [ClaimNode(**c) if isinstance(c, dict) else ClaimNode(claim_id=f"C{i:03d}", statement=str(c)) for i, c in enumerate(raw_claims, 1)]
    elif args.claim:
        for idx, stmt in enumerate(args.claim, start=len(bundle.claims) + 1):
            bundle.claims.append(ClaimNode(claim_id=f"C{idx:03d}", statement=stmt))

    new_ev = ground_claims_to_evidence(
        bundle.claims,
        bundle.sources,
        bundle.passages,
        assets=bundle.assets,
        evidence_id_start=len(bundle.evidence) + 1,
    )
    bundle.evidence.extend(new_ev)

    if len(bundle.evidence) >= 2 and not bundle.comparisons:
        bundle.comparisons.append(
            evaluate_evidence_comparability("CMP001", bundle.evidence[0], bundle.evidence[1])
        )

    issues = audit_scientific_consistency(bundle, base_dir=args.bundle.parent)
    bundle.save(args.out)
    blockers = sum(1 for i in issues if i.severity == "P0_BLOCKER")
    print(
        f"PASS core_ground claims={len(bundle.claims)} evidence={len(bundle.evidence)} "
        f"issues={len(issues)} blockers={blockers} out={args.out}"
    )
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    bundle = ResearchBundle.load(args.bundle)
    issues = audit_scientific_consistency(bundle, base_dir=args.bundle.parent)
    report = {
        "bundle_id": bundle.bundle_id,
        "schema_version": bundle.schema_version,
        "core_version": __version__,
        "total_issues": len(issues),
        "p0_blockers": sum(1 for i in issues if i.severity == "P0_BLOCKER"),
        "p1_warnings": sum(1 for i in issues if i.severity == "P1_WARNING"),
        "issues": [
            {
                "issue_id": i.issue_id,
                "severity": i.severity,
                "category": i.category,
                "target_ids": i.target_ids,
                "message": i.message,
                "evidence_locator": i.evidence_locator,
                "remediation": i.remediation,
            }
            for i in issues
        ],
    }
    if args.invalidate:
        report["invalidation_propagation"] = propagate_invalidation(bundle, args.invalidate)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(
        f"{'FAIL' if (args.strict and report['p0_blockers'] > 0) else 'PASS'} "
        f"core_audit issues={report['total_issues']} blockers={report['p0_blockers']}"
    )
    return 1 if (args.strict and report["p0_blockers"] > 0) else 0


def cmd_synthesize(args: argparse.Namespace) -> int:
    bundle = ResearchBundle.load(args.bundle)
    matrix = build_review_synthesis_matrix(bundle)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(matrix, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(
        f"PASS core_synthesize rows={len(matrix['evidence_matrix'])} "
        f"paragraphs={len(matrix['review_paragraphs'])} out={args.out}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--version", action="version", version=f"research-core {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("discover", help="Discover, deduplicate, and rank scientific literature")
    d.add_argument("--catalog", type=Path, help="Local Crossref/OpenAlex/curated JSON catalog")
    d.add_argument("--query", type=str, default="", help="Search query or research topic")
    d.add_argument("--max-results", type=int, default=15)
    d.add_argument("--bundle-id", type=str, default="BUNDLE001")
    d.add_argument("--out", type=Path, required=True)
    d.set_defaults(func=cmd_discover)

    ip = sub.add_parser("ingest-pdf", help="Parse scientific PDF into passages, tables, and multi-panel figures")
    ip.add_argument("pdf", type=Path)
    ip.add_argument("--source-id", type=str, default="SRC001")
    ip.add_argument("--pages", type=str, default=None)
    ip.add_argument("--assets-dir", type=Path, default=None)
    ip.add_argument("--license", type=str, default=None)
    ip.add_argument("--bundle", type=Path, default=None)
    ip.add_argument("--bundle-id", type=str, default="BUNDLE001")
    ip.add_argument("--out", type=Path, required=True)
    ip.set_defaults(func=cmd_ingest_pdf)

    g = sub.add_parser("ground", help="Ground scientific claims to evidence and bind assets")
    g.add_argument("bundle", type=Path)
    g.add_argument("--claims-json", type=Path, default=None)
    g.add_argument("--claim", action="append", default=[])
    g.add_argument("--out", type=Path, required=True)
    g.set_defaults(func=cmd_ground)

    a = sub.add_parser("audit", help="Run scientific consistency audit and optional invalidation propagation")
    a.add_argument("bundle", type=Path)
    a.add_argument("--invalidate", nargs="*", default=[])
    a.add_argument("--strict", action="store_true")
    a.add_argument("--out", type=Path, default=None)
    a.set_defaults(func=cmd_audit)

    s = sub.add_parser("synthesize", help="Build cross-paper synthesis matrix and review provenance paragraphs")
    s.add_argument("bundle", type=Path)
    s.add_argument("--out", type=Path, required=True)
    s.set_defaults(func=cmd_synthesize)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

