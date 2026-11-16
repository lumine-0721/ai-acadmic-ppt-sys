#!/usr/bin/env python3
"""Optional JSON Schema + semantic diagnostic inspector for APC PPT_HANDOFF.json.

Reuses python-jsonschema (Draft202012Validator) for multi-error structural validation
and pairs it with check_handoff.py for cross-record scientific admissibility checks.
Does not replace check_handoff.py or imply scientific validity from schema pass alone.
"""
import argparse
import json
from pathlib import Path
import sys

import check_handoff as semantic

SUPPORTED_JSONSCHEMA = "4.25.1"
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "ppt-handoff.schema.json"


def json_pointer(path_items):
    if not path_items:
        return "/"
    escaped = [str(p).replace("~", "~0").replace("/", "~1") for p in path_items]
    return "/" + "/".join(escaped)


def strict_int_guard(obj, path=()):
    """Guard against JSON Schema treating 1.0 float as integer in Python."""
    issues = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("revision", "reviewed_in") and isinstance(v, float):
                issues.append({
                    "layer": "schema",
                    "path": json_pointer((*path, k)),
                    "keyword": "type",
                    "message": f"{k} must be an exact integer, not float {v!r}",
                })
            issues.extend(strict_int_guard(v, (*path, k)))
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            issues.extend(strict_int_guard(item, (*path, idx)))
    return issues


def diagnose(index_path, requirements_path=None, final=False, strict_assets=False):
    from importlib.metadata import version
    from jsonschema import Draft202012Validator

    ver = version("jsonschema")
    if ver != SUPPORTED_JSONSCHEMA:
        raise ValueError(f"expected jsonschema=={SUPPORTED_JSONSCHEMA}; found {ver}")

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)

    diagnostics = []
    try:
        raw_text = index_path.read_text(encoding="utf-8-sig")
        instance = json.loads(raw_text, object_pairs_hook=semantic.unique_pairs)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        diagnostics.append({
            "layer": "parse",
            "path": "/",
            "keyword": "json_parse",
            "message": str(exc),
        })
        return {
            "format": "apc-handoff-diagnostics/1",
            "source": str(index_path.resolve()),
            "validator": f"jsonschema/{ver}",
            "schema_valid": False,
            "semantic_valid": False,
            "valid": False,
            "error_count": len(diagnostics),
            "errors": diagnostics,
        }

    raw_errors = sorted(
        validator.iter_errors(instance),
        key=lambda e: (list(map(str, e.absolute_path)), e.validator),
    )
    for err in raw_errors:
        diagnostics.append({
            "layer": "schema",
            "path": json_pointer(err.absolute_path),
            "keyword": err.validator,
            "message": err.message,
        })
    diagnostics.extend(strict_int_guard(instance))
    schema_valid = len(diagnostics) == 0

    semantic_valid = False
    try:
        checked = semantic.check_index(instance, final=final, strict_assets=strict_assets)
        if requirements_path is not None:
            semantic.check_requirements(requirements_path, checked)
        semantic_valid = True
    except (OSError, ValueError, KeyError, TypeError) as exc:
        diagnostics.append({
            "layer": "semantic",
            "path": "/",
            "keyword": "semantic_invariants",
            "message": str(exc),
        })

    return {
        "format": "apc-handoff-diagnostics/1",
        "source": str(index_path.resolve()),
        "validator": f"jsonschema/{ver}",
        "schema_valid": schema_valid,
        "semantic_valid": semantic_valid,
        "valid": schema_valid and semantic_valid,
        "error_count": len(diagnostics),
        "errors": diagnostics,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("index", type=Path, help="Path to PPT_HANDOFF.json")
    parser.add_argument("--requirements", type=Path, help="Optional PPT_REQUIREMENTS.md to cross-check header")
    parser.add_argument("--final", action="store_true", help="Check final-ready slide constraints")
    parser.add_argument("--strict-assets", action="store_true", help="Check READY S/V direct R dependencies")
    parser.add_argument("--out", type=Path, help="Optional new JSON path to write structured diagnostic report")
    args = parser.parse_args()

    try:
        if args.out is not None and args.out.exists():
            raise FileExistsError("output exists; choose a new path")
        report = diagnose(
            args.index,
            requirements_path=args.requirements,
            final=args.final,
            strict_assets=args.strict_assets,
        )
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            with args.out.open("x", encoding="utf-8", newline="\n") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
                f.write("\n")
        if report["valid"]:
            print(
                f"PASS schema_valid=true semantic_valid=true errors=0 "
                f"validator={report['validator']}"
            )
            return 0
        for item in report["errors"]:
            print(
                f"FAIL [{item['layer']}] path={item['path']} "
                f"keyword={item['keyword']} message={item['message']}",
                file=sys.stderr,
            )
        return 1
    except ImportError:
        print(
            "FAIL optional dependency missing; install requirements-schema.txt in an isolated venv",
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
