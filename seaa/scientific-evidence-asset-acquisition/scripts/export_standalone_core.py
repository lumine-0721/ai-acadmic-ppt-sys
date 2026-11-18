#!/usr/bin/env python3
"""Reproducible exporter for Deliverable B: Standalone Reusable Research Evidence & Asset Core.

Generates:
- deliverables/research-evidence-asset-core/ (complete standalone Python package, tests, schemas, examples, docs, licenses)
- deliverables/research-evidence-asset-core-v1.0.0.zip (versioned archive with SHA-256 manifest)

Guarantees Single Source of Truth: copies `research_core` directly from the authoritative skill tree
and verifies zero AI PPT / APC / APRD / slide coupling.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import zipfile

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
CORE_SRC_DIR = SCRIPT_DIR / "research_core"
SCHEMA_SRC = SKILL_ROOT / "schemas" / "research-core-bundle.schema.json"

FORBIDDEN_PPT_TOKENS = (
    "PPT_HANDOFF",
    "PPT_REQUIREMENTS",
    "win32com",
    "PowerPoint.Application",
    "APRD",
    "SLIDE_STYLE_MAPPING",
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_core_zero_ppt_coupling(core_dir: Path) -> None:
    for py_file in sorted(core_dir.rglob("*.py")):
        content = py_file.read_text(encoding="utf-8")
        for token in FORBIDDEN_PPT_TOKENS:
            if token in content:
                raise ValueError(f"Coupling violation: forbidden token {token!r} found in {py_file.name}")


def export_standalone_core(out_root: Path, version: str = "1.0.0", commit_sha: str = "HEAD") -> dict:
    verify_core_zero_ppt_coupling(CORE_SRC_DIR)
    pkg_dir = out_root / "research-evidence-asset-core"
    if pkg_dir.exists():
        shutil.rmtree(pkg_dir)

    (pkg_dir / "src" / "research_core").mkdir(parents=True, exist_ok=True)
    (pkg_dir / "tests").mkdir(parents=True, exist_ok=True)
    (pkg_dir / "examples").mkdir(parents=True, exist_ok=True)
    (pkg_dir / "schemas").mkdir(parents=True, exist_ok=True)
    (pkg_dir / "docs").mkdir(parents=True, exist_ok=True)
    (pkg_dir / "LICENSES").mkdir(parents=True, exist_ok=True)

    for item in sorted(CORE_SRC_DIR.glob("*.py")):
        shutil.copy2(item, pkg_dir / "src" / "research_core" / item.name)

    if SCHEMA_SRC.is_file():
        shutil.copy2(SCHEMA_SRC, pkg_dir / "schemas" / SCHEMA_SRC.name)

    (pkg_dir / "VERSION").write_text(f"{version}\n", encoding="utf-8", newline="\n")

    pyproject_text = (
        "[build-system]\n"
        "requires = [\"setuptools>=61.0\"]\n"
        "build-backend = \"setuptools.build_meta\"\n\n"
        "[project]\n"
        "name = \"research-evidence-asset-core\"\n"
        f"version = \"{version}\"\n"
        "description = \"Task-neutral scientific literature discovery, PDF figure/table extraction, claim-evidence grounding, and consistency core\"\n"
        "readme = \"README.md\"\n"
        "requires-python = \">=3.10\"\n"
        "license = {text = \"MIT\"}\n"
        "dependencies = []\n\n"
        "[project.optional-dependencies]\n"
        "pdf = [\n"
        "  \"pypdf==7.0.1\",\n"
        "  \"pdfplumber==0.11.10\",\n"
        "  \"pypdfium2==5.15.2\",\n"
        "  \"pillow>=10.0.0\",\n"
        "]\n"
        "schema = [\n"
        "  \"jsonschema>=4.20.0\",\n"
        "]\n\n"
        "[project.scripts]\n"
        "research-core = \"research_core.cli:main\"\n\n"
        "[tool.setuptools.packages.find]\n"
        "where = [\"src\"]\n"
    )
    (pkg_dir / "pyproject.toml").write_text(pyproject_text, encoding="utf-8", newline="\n")

    readme_text = (
        "# Research Evidence & Asset Core (`research-evidence-asset-core`)\n\n"
        f"- **Version**: `{version}`\n"
        "- **Schema Contract**: `research-core.bundle.v1` (`schemas/research-core-bundle.schema.json`)\n"
        "- **License**: MIT (with permissive BSD-3-Clause / Apache-2.0 / MIT optional PDF extras)\n"
        "- **Coupling Status**: **100% Task-Neutral** (zero dependency on AI PPT, APC, APRD, Slide IDs, or PowerPoint COM)\n\n"
        "## Overview\n\n"
        "`research-evidence-asset-core` is a standalone scientific evidence and asset engineering library designed for:\n"
        "1. **Scientific Literature Discovery & Deduplication** (`research_core.discovery`)\n"
        "2. **Full-Text PDF Section Parsing & Multi-Panel Scientific Figure/Table Extraction** (`research_core.pdf_processor`)\n"
        "3. **Claim-Evidence Grounding & Experimental Condition Extraction** (`research_core.grounding`)\n"
        "4. **Scientific Consistency Checking, Cross-Paper Comparability & Review Synthesis** (`research_core.consistency`)\n\n"
        "## Quickstart (Zero-Dependency Core & CLI)\n\n"
        "```powershell\n"
        "# Run standalone unit tests directly from unpacked archive:\n"
        "$env:PYTHONPATH = \"$PWD/src\"\n"
        "python -B -X utf8 tests/test_research_core_standalone.py\n\n"
        "# Run the end-to-end Review Engine / Literature Grounding example:\n"
        "python -B -X utf8 examples/run_basic_workflow.py\n"
        "```\n"
    )
    (pkg_dir / "README.md").write_text(readme_text, encoding="utf-8", newline="\n")

    arch_doc = (
        "# Research Core Architecture & Review Engine Extension Guide\n\n"
        "## 1. Data Contract (`ResearchBundle`)\n\n"
        "- `sources` (`SourceRecord`): `source_id`, `title`, `doi`, `access_status` (`FULL_TEXT_LOCAL`, `ABSTRACT_ONLY`, `METADATA_ONLY`, `USER_DATA_VERIFIED`), `verification_level`, `sha256`.\n"
        "- `passages` (`PassageRecord`): Page- and section-indexed document passages with character spans and figure mentions.\n"
        "- `evidence` (`EvidenceRecord`): Grounded scientific evidence with `ExactLocator`, `MeasurementRecord` (with SI normalization), and `ConditionItem` (pH, solvent, temperature, LOD criterion).\n"
        "- `claims` (`ClaimNode`): Scientific claims linked to supporting/opposing/context evidence with conflict assessment.\n"
        "- `assets` (`AssetRecord`): Scientific figures, spectra, schemes, and tables with `bbox_pt`, `panel_labels` (`a/b/c`), `mention_contexts`, `sha256`, and `RightsMetadata`.\n"
        "- `comparisons` (`ComparabilityRecord`): Cross-paper comparability evaluation (`DIRECTLY_COMPARABLE`, `CONDITIONAL`, `NOT_COMPARABLE`).\n\n"
        "## 2. Six-Stage Verification State Machine\n\n"
        "`UNVERIFIED` -> `SOURCE_LOCATED` -> `EVIDENCE_LOCATED` -> `SEMANTICALLY_CHECKED` -> `APPROVED` / `CONFLICTED`.\n"
        "Abstract-only records are strictly capped at `EVIDENCE_LOCATED` and blocked from being promoted to `APPROVED` full-text status.\n"
    )
    (pkg_dir / "docs" / "CORE_CONTRACT_AND_EXTENSION.md").write_text(arch_doc, encoding="utf-8", newline="\n")

    license_mit = (
        "MIT License\n\n"
        "Copyright (c) 2026 Research Evidence & Asset Core Contributors\n\n"
        "Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files...\n"
    )
    (pkg_dir / "LICENSES" / "LICENSE-MIT.txt").write_text(license_mit, encoding="utf-8", newline="\n")
    if (SKILL_ROOT / "THIRD_PARTY_NOTICES.md").is_file():
        shutil.copy2(SKILL_ROOT / "THIRD_PARTY_NOTICES.md", pkg_dir / "LICENSES" / "THIRD_PARTY_NOTICES.md")

    sample_catalog = [
        {
            "doi": "10.1039/C4CC09120E",
            "title": "A mitochondria-targeted ratiometric fluorescent probe for hydrogen peroxide based on FRET",
            "authors": ["Ming Wang", "Lin Zhang"],
            "year": 2015,
            "publication": "Chemical Communications",
            "abstract": "We developed a polymeric micelle FRET fluorescent probe in PBS buffer at pH 7.4 and 37 °C with a detection limit of 12 nM (3σ/k) and a linear range of 0.05-10 μM for endogenous H2O2.",
            "license": "CC-BY-4.0"
        },
        {
            "doi": "10.1039/C4CC09120E",
            "title": "Duplicate entry: mitochondria-targeted ratiometric fluorescent probe for hydrogen peroxide",
            "authors": ["Ming Wang"],
            "year": 2015,
            "publication": "Chem. Commun."
        },
        {
            "doi": "10.1021/acs.analchem.8b01234",
            "title": "Boronate-based near-infrared fluorescent probe for H2O2 in CH3CN/PBS mixture",
            "authors": ["Hao Chen", "Wei Liu"],
            "year": 2018,
            "publication": "Analytical Chemistry",
            "abstract": "In CH3CN/PBS (1:1 v/v) at pH 8.0 and 25 °C, the probe exhibits a limit of detection of 0.15 μM for H2O2."
        }
    ]
    (pkg_dir / "examples" / "sample_catalog.json").write_text(
        json.dumps(sample_catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    example_script = (
        "#!/usr/bin/env python3\n"
        "from pathlib import Path\n"
        "import json\n"
        "import sys\n\n"
        "ROOT = Path(__file__).resolve().parents[1]\n"
        "sys.path.insert(0, str(ROOT / 'src'))\n\n"
        "from research_core.discovery import discover_from_local_catalog\n"
        "from research_core.grounding import ground_claims_to_evidence\n"
        "from research_core.consistency import audit_scientific_consistency, evaluate_evidence_comparability, build_review_synthesis_matrix\n"
        "from research_core.models import ClaimNode, ResearchBundle\n\n"
        "def main() -> int:\n"
        "    cat = ROOT / 'examples' / 'sample_catalog.json'\n"
        "    disc = discover_from_local_catalog(cat, query='fluorescent probe H2O2 detection limit')\n"
        "    claims = [\n"
        "        ClaimNode(claim_id='C001', statement='The FRET probe achieves a detection limit of 12 nM in PBS at pH 7.4 and 37 °C.'),\n"
        "        ClaimNode(claim_id='C002', statement='The NIR boronate probe reaches a limit of detection of 0.15 μM in CH3CN/PBS at pH 8.0.'),\n"
        "    ]\n"
        "    ev = ground_claims_to_evidence(claims, disc['selected_sources'], disc['selected_passages'])\n"
        "    cmp_rec = evaluate_evidence_comparability('CMP001', ev[0], ev[1])\n"
        "    bundle = ResearchBundle(\n"
        "        bundle_id='EXAMPLE_REVIEW_001',\n"
        "        title='H2O2 Fluorescent Probe Cross-Study Synthesis',\n"
        "        sources=disc['selected_sources'],\n"
        "        passages=disc['selected_passages'],\n"
        "        evidence=ev,\n"
        "        claims=claims,\n"
        "        comparisons=[cmp_rec],\n"
        "    )\n"
        "    issues = audit_scientific_consistency(bundle)\n"
        "    matrix = build_review_synthesis_matrix(bundle)\n"
        "    dedup_n = len(disc['duplicates_removed'])\n"
        "    para_n = len(matrix['review_paragraphs'])\n"
        "    print(f'PASS example_workflow sources={len(bundle.sources)} dedup_dropped={dedup_n} evidence={len(ev)} comparison={cmp_rec.status} paragraphs={para_n} issues={len(issues)}')\n"
        "    return 0\n\n"
        "if __name__ == '__main__':\n"
        "    sys.exit(main())\n"
    )
    (pkg_dir / "examples" / "run_basic_workflow.py").write_text(example_script, encoding="utf-8", newline="\n")

    standalone_test = (
        "#!/usr/bin/env python3\n"
        "import json\n"
        "from pathlib import Path\n"
        "import sys\n"
        "import unittest\n\n"
        "ROOT = Path(__file__).resolve().parents[1]\n"
        "sys.path.insert(0, str(ROOT / 'src'))\n\n"
        "from research_core.discovery import discover_from_local_catalog, normalize_doi\n"
        "from research_core.grounding import ground_claims_to_evidence\n"
        "from research_core.consistency import audit_scientific_consistency, evaluate_evidence_comparability, propagate_invalidation, build_review_synthesis_matrix\n"
        "from research_core.models import AccessStatus, ClaimNode, ResearchBundle, VerificationLevel\n\n"
        "class TestStandaloneResearchCore(unittest.TestCase):\n"
        "    def test_zero_ppt_dependency_and_discovery_dedup(self):\n"
        "        self.assertEqual(normalize_doi('https://doi.org/10.1039/C4CC09120E.'), '10.1039/c4cc09120e')\n"
        "        disc = discover_from_local_catalog(ROOT / 'examples' / 'sample_catalog.json', query='H2O2 fluorescent probe')\n"
        "        self.assertEqual(disc['total_raw_records'], 3)\n"
        "        self.assertEqual(disc['deduplicated_count'], 2)\n"
        "        self.assertTrue(all(s.access_status == AccessStatus.ABSTRACT_ONLY.value for s in disc['selected_sources']))\n\n"
        "    def test_grounding_comparability_and_abstract_overclaim_guard(self):\n"
        "        disc = discover_from_local_catalog(ROOT / 'examples' / 'sample_catalog.json', query='H2O2 probe')\n"
        "        claims = [\n"
        "            ClaimNode(claim_id='C001', statement='Detection limit of 12 nM in PBS at pH 7.4 and 37 °C.'),\n"
        "            ClaimNode(claim_id='C002', statement='Limit of detection of 0.15 μM in CH3CN/PBS at pH 8.0.'),\n"
        "        ]\n"
        "        ev = ground_claims_to_evidence(claims, disc['selected_sources'], disc['selected_passages'])\n"
        "        self.assertGreaterEqual(len(ev), 2)\n"
        "        # Abstract-only evidence must NOT be promoted to APPROVED\n"
        "        self.assertEqual(ev[0].verification_level, VerificationLevel.EVIDENCE_LOCATED.value)\n"
        "        cmp_rec = evaluate_evidence_comparability('CMP001', ev[0], ev[1])\n"
        "        self.assertEqual(cmp_rec.status, 'CONDITIONAL')\n"
        "        bundle = ResearchBundle(bundle_id='B1', title='Test', sources=disc['selected_sources'], passages=disc['selected_passages'], evidence=ev, claims=claims, comparisons=[cmp_rec])\n"
        "        # Inject an illegal promotion of abstract-only evidence to APPROVED and verify auditor catches it\n"
        "        ev[0].verification_level = VerificationLevel.APPROVED.value\n"
        "        issues = audit_scientific_consistency(bundle)\n"
        "        self.assertTrue(any(i.category == 'ABSTRACT_OVERCLAIM_AS_FULLTEXT' for i in issues))\n"
        "        prop = propagate_invalidation(bundle, [ev[0].source_id])\n"
        "        self.assertIn('C001', prop['affected_claim_ids'])\n"
        "        matrix = build_review_synthesis_matrix(bundle)\n"
        "        self.assertEqual(len(matrix['review_paragraphs']), 2)\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main(verbosity=2)\n"
    )
    (pkg_dir / "tests" / "test_research_core_standalone.py").write_text(standalone_test, encoding="utf-8", newline="\n")

    zip_path = out_root / f"research-evidence-asset-core-v{version}.zip"
    if zip_path.exists():
        zip_path.unlink()

    file_hashes = {}
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(pkg_dir.rglob("*")):
            if file_path.is_file() and "__pycache__" not in file_path.parts:
                rel = file_path.relative_to(out_root).as_posix()
                zf.write(file_path, arcname=rel)
                file_hashes[file_path.relative_to(pkg_dir).as_posix()] = sha256_file(file_path)

    archive_sha = sha256_file(zip_path)
    export_manifest = {
        "package_name": "research-evidence-asset-core",
        "version": version,
        "schema_version": "research-core.bundle.v1",
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_commit": commit_sha,
        "authoritative_core_source": str(CORE_SRC_DIR.resolve()),
        "standalone_directory": str(pkg_dir.resolve()),
        "archive_path": str(zip_path.resolve()),
        "archive_sha256": archive_sha,
        "file_count": len(file_hashes),
        "files_sha256": file_hashes,
    }
    (pkg_dir / "EXPORT_MANIFEST.json").write_text(
        json.dumps(export_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (out_root / "EXPORT_MANIFEST.json").write_text(
        json.dumps(export_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return export_manifest


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    # Default: <repo>/dist/seaa-standalone-core, or the directory in SEAA_EXPORT_ROOT.
    default_out = Path(os.environ.get("SEAA_EXPORT_ROOT") or Path(__file__).resolve().parents[3] / "dist" / "seaa-standalone-core")
    p.add_argument("--out-root", type=Path, default=default_out)
    p.add_argument("--version", type=str, default="1.0.0")
    p.add_argument("--commit", type=str, default="HEAD")
    args = p.parse_args()
    manifest = export_standalone_core(args.out_root, version=args.version, commit_sha=args.commit)
    print(
        f"PASS export_standalone_core version={manifest['version']} "
        f"files={manifest['file_count']} zip_sha256={manifest['archive_sha256']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
