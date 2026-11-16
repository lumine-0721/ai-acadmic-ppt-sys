"""Locate installed APC, APRD, and SEAA trees without guessing new entry points."""

from __future__ import annotations

import os
from pathlib import Path

from academic_ppt_orchestrator.hashing import sha256_file


def _env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).resolve()


def module_roots() -> dict[str, Path]:
    return {
        "apc": _env_path(
            "APC_ROOT",
            r"F:/codex/projects/academic-ppt/apc/academic-presentation-compiler",
        ),
        "aprd": _env_path(
            "APRD_ROOT",
            r"F:/codex/projects/academic-ppt/aprd/academic-presentation-reference-design",
        ),
        "seaa": _env_path(
            "SEAA_ROOT",
            r"F:/codex/projects/academic-ppt/seaa/scientific-evidence-asset-acquisition",
        ),
        "aprd_fusion": _env_path(
            "APRD_FUSION_ROOT",
            r"F:/codex/projects/academic-ppt/aprd/academic-presentation-reference-design",
        ),
    }


def fingerprint(path: Path) -> dict:
    if not path.is_file():
        return {"path": str(path), "present": False, "sha256": None, "bytes": 0}
    return {
        "path": str(path),
        "present": True,
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }


def module_manifest() -> dict:
    roots = module_roots()
    entries = {
        "apc": [
            "scripts/check_handoff.py",
            "scripts/check_handoff_schema.py",
            "schemas/ppt-handoff.schema.json",
        ],
        "aprd": [
            "scripts/check_package.py",
            "scripts/inspect_pptx.py",
            "scripts/inspect_design.py",
        ],
        "seaa": [
            "scripts/check_evidence.py",
            "scripts/seaa_cli.py",
            "scripts/audit_files.py",
            "scripts/probe_pdf_evidence.py",
        ],
    }
    described = {}
    for module_id, relatives in entries.items():
        root = roots[module_id]
        described[module_id] = {
            "root": str(root),
            "root_present": root.is_dir(),
            "git": False,
            "version_note": "Installed skill tree has no git commit. Identity is the SHA-256 of the entry scripts.",
            "entries": {rel: fingerprint(root / rel) for rel in relatives},
        }
    fusion = roots["aprd_fusion"]
    described["aprd_fusion"] = {
        "root": str(fusion),
        "root_present": fusion.is_dir(),
        "installed_as_skill": False,
        "entries": {
            "scripts/aprd_cli.py": fingerprint(fusion / "scripts" / "aprd_cli.py"),
        },
        "version_note": "Workspace fusion tree from 2026-11-13. Not copied over the installed APRD skill.",
    }
    return described
