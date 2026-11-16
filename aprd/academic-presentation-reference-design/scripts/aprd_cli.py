#!/usr/bin/env python3
"""Unified CLI for APRD Fusion Engine (Extract, Compile Package, Replicate, Generalize, Verify, Registry)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from aprd_engine.reconstructor_verifier import (  # noqa: E402
    generalize_deck_from_spec,
    replicate_deck_with_slot_payload,
    verify_deck_against_spec,
)
from aprd_engine.spec_compiler import (  # noqa: E402
    DesignAssetRegistry,
    build_design_specification,
    compile_ppt_reference_package,
    validate_design_spec_schema,
)


def cmd_extract(args: argparse.Namespace) -> int:
    pptx_path = Path(args.source).resolve()
    out_path = Path(args.output).resolve()
    if out_path.exists() and not args.force:
        print(f"FAIL output exists: {out_path}", file=sys.stderr)
        return 1

    registry = DesignAssetRegistry(Path(args.registry)) if args.registry else None
    if registry and not args.no_cache:
        cached = registry.lookup(pptx_path)
        if cached:
            spec = cached["spec"]
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(
                f"PASS cache_hit=true ref_id={spec['source_provenance']['reference_id']} "
                f"slides={spec['source_provenance']['extraction_metadata']['slide_count']} "
                f"patterns={len(spec['layout_patterns'])} schema_valid={str(spec['validation_metadata']['schema_valid']).lower()}"
            )
            return 0

    visual_ann = None
    if args.visual_annotations:
        visual_ann = json.loads(Path(args.visual_annotations).read_text(encoding="utf-8-sig"))

    spec = build_design_specification(
        pptx_path,
        ref_id=args.ref_id,
        visual_annotations=visual_ann,
        scrub_text=not args.include_text,
    )
    if not spec["validation_metadata"]["schema_valid"]:
        print(f"FAIL schema validation errors: {spec['validation_metadata']['warnings']}", file=sys.stderr)
        return 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if registry:
        registry.register(pptx_path, spec)

    print(
        f"PASS cache_hit=false ref_id={spec['source_provenance']['reference_id']} "
        f"slides={spec['source_provenance']['extraction_metadata']['slide_count']} "
        f"patterns={len(spec['layout_patterns'])} schema_valid=true"
    )
    return 0


def cmd_validate_spec(args: argparse.Namespace) -> int:
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8-sig"))
    ok, errors, validator = validate_design_spec_schema(spec)
    if not ok:
        for err in errors:
            print(f"ERROR {err}", file=sys.stderr)
        print(f"FAIL schema_valid=false errors={len(errors)} validator={validator}", file=sys.stderr)
        return 1
    print(f"PASS schema_valid=true patterns={len(spec.get('layout_patterns', []))} validator={validator}")
    return 0


def cmd_compile_package(args: argparse.Namespace) -> int:
    pptx_path = Path(args.source).resolve()
    spec = (
        json.loads(Path(args.spec).read_text(encoding="utf-8-sig"))
        if args.spec
        else build_design_specification(pptx_path, ref_id=args.ref_id)
    )
    res = compile_ppt_reference_package(
        spec=spec,
        source_pptx=pptx_path,
        package_dir=Path(args.package_dir),
        handoff_path=Path(args.handoff) if args.handoff else None,
        design_revision=args.design_revision,
        rights_status=args.rights_status,
    )
    print(
        f"PASS package_dir={res['package_dir']} design_revision={res['design_revision']} "
        f"layouts={res['layout_count']} mapped={res['mapped_slides']}"
    )
    return 0


def cmd_reconstruct(args: argparse.Namespace) -> int:
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8-sig"))
    out_pptx = Path(args.output).resolve()
    if args.mode == "replicate":
        if not args.source:
            print("FAIL --source reference PPTX is required for replicate mode", file=sys.stderr)
            return 1
        payloads = json.loads(Path(args.payload).read_text(encoding="utf-8-sig")) if args.payload else None
        res = replicate_deck_with_slot_payload(Path(args.source), spec, out_pptx, payloads)
        print(f"PASS mode={res['mode']} slides={res['slides_processed']} slots_updated={res['slots_updated']} output={out_pptx}")
        return 0
    else:
        if not args.payload:
            print("FAIL --payload content plan JSON is required for generalize mode", file=sys.stderr)
            return 1
        plan = json.loads(Path(args.payload).read_text(encoding="utf-8-sig"))
        res = generalize_deck_from_spec(spec, plan, out_pptx)
        print(f"PASS mode={res['mode']} slides={res['slide_count']} output={out_pptx}")
        return 0


def cmd_verify(args: argparse.Namespace) -> int:
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8-sig"))
    report = verify_deck_against_spec(
        pptx_path=Path(args.pptx),
        spec=spec,
        render_dir=Path(args.render_dir) if args.render_dir else None,
        reference_render_dir=Path(args.reference_render_dir) if args.reference_render_dir else None,
    )
    if args.report:
        rp = Path(args.report).resolve()
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    s = report["summary"]
    if s["failed"] > 0:
        print(f"FAIL checks={s['total_checks']} failed={s['failed']} warnings={s['warnings']}", file=sys.stderr)
        return 1
    print(
        f"PASS checks={s['total_checks']} passed={s['passed']} warnings={s['warnings']} "
        f"editable_objects={s['editable_objects']} avg_ssim={s['average_ssim']}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_ext = sub.add_parser("extract", help="Extract schema-validated DesignSpecification v1.0 from a reference PPTX")
    p_ext.add_argument("source", help="Reference .pptx path")
    p_ext.add_argument("output", help="Output DESIGN_SPECIFICATION.json path")
    p_ext.add_argument("--ref-id", default="REF001")
    p_ext.add_argument("--visual-annotations", help="Optional visual annotations JSON path")
    p_ext.add_argument("--registry", help="Optional DesignAssetRegistry directory for SHA-256 caching")
    p_ext.add_argument("--no-cache", action="store_true")
    p_ext.add_argument("--include-text", action="store_true", help="Disable default scientific content scrubbing")
    p_ext.add_argument("--force", action="store_true")
    p_ext.set_defaults(func=cmd_extract)

    p_val = sub.add_parser("validate-spec", help="Validate a DesignSpecification JSON against Draft 2020-12 schema")
    p_val.add_argument("spec", help="DESIGN_SPECIFICATION.json path")
    p_val.set_defaults(func=cmd_validate_spec)

    p_pkg = sub.add_parser("compile-package", help="Compile a full PPT_REFERENCE_PACKAGE from a reference PPTX")
    p_pkg.add_argument("source", help="Reference .pptx path")
    p_pkg.add_argument("package_dir", help="Target PPT_REFERENCE_PACKAGE directory")
    p_pkg.add_argument("--spec", help="Optional pre-extracted DESIGN_SPECIFICATION.json")
    p_pkg.add_argument("--handoff", help="Optional APC PPT_HANDOFF.json for project-scoped mapping")
    p_pkg.add_argument("--ref-id", default="REF001")
    p_pkg.add_argument("--design-revision", type=int, default=1)
    p_pkg.add_argument("--rights-status", default="reference_only")
    p_pkg.set_defaults(func=cmd_compile_package)

    p_rec = sub.add_parser("reconstruct", help="Replicate (Level 2) or generalize (Level 3) a PPTX from DesignSpecification")
    p_rec.add_argument("--mode", choices=("replicate", "generalize"), required=True)
    p_rec.add_argument("--spec", required=True, help="DESIGN_SPECIFICATION.json path")
    p_rec.add_argument("--source", help="Reference .pptx path (required for replicate mode)")
    p_rec.add_argument("--payload", help="Slot replacement or new academic content plan JSON")
    p_rec.add_argument("-o", "--output", required=True, help="Output .pptx path")
    p_rec.set_defaults(func=cmd_reconstruct)

    p_ver = sub.add_parser("verify", help="Verify a generated/replicated PPTX against DesignSpecification + optional COM render")
    p_ver.add_argument("pptx", help="Target .pptx path")
    p_ver.add_argument("--spec", required=True, help="DESIGN_SPECIFICATION.json path")
    p_ver.add_argument("--render-dir", help="Optional directory to export PowerPoint COM PNGs and BoundHeight checks")
    p_ver.add_argument("--reference-render-dir", help="Optional reference slide PNG directory for SSIM comparison")
    p_ver.add_argument("--report", help="Optional JSON report output path")
    p_ver.set_defaults(func=cmd_verify)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
