#!/usr/bin/env python3
"""Comprehensive unit and integration tests for SEAA Open-Source Fusion & Reusable Research Core."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import check_evidence
from export_standalone_core import FORBIDDEN_PPT_TOKENS, verify_core_zero_ppt_coupling
from research_core.consistency import (
    audit_scientific_consistency,
    build_review_synthesis_matrix,
    evaluate_evidence_comparability,
    propagate_invalidation,
)
from research_core.discovery import discover_from_local_catalog, normalize_doi, title_similarity
from research_core.grounding import (
    extract_experimental_conditions,
    extract_measurements_from_text,
    ground_claims_to_evidence,
)
from research_core.models import (
    AccessStatus,
    AssetStatus,
    ClaimNode,
    ResearchBundle,
    RightsStatus,
    VerificationLevel,
)
from research_core.pdf_processor import parse_scientific_pdf
from seaa_adapter import compile_evidence_package_from_bundle

WORK_TMP_ROOT = Path("C:/Users/86139/Desktop/codex/_review_tmp")
WORK_TMP_ROOT.mkdir(parents=True, exist_ok=True)
os.environ["TMP"] = str(WORK_TMP_ROOT)
os.environ["TEMP"] = str(WORK_TMP_ROOT)

CORE_LIT_JSON = Path("C:/Users/86139/Desktop/codex/outputs/01a075cf-062b-7c21-b553-8ba869c5e511/核心文献元数据.json")
PLOS_PDF = Path("C:/Users/86139/Desktop/codex/outputs/copy-first-evolution-20261112/experiments/seaa/evidence/plos-1003285.pdf")


def build_synthetic_multipanel_scientific_pdf(pdf_path: Path) -> None:
    """Generate a deterministic valid PDF with a multi-panel Figure 1 (a, b), caption, and mention using pure Python."""
    stream_lines = [
        "BT /F1 13 Tf 50 745 Td (Ratiometric Fluorescent Probe for Mitochondrial H2O2 Detection) Tj ET",
        "BT /F1 9 Tf 50 728 Td (DOI: 10.1039/c4cc09120e | Open Access under Creative Commons Attribution CC BY 4.0) Tj ET",
        "BT /F1 11 Tf 50 700 Td (1. Abstract) Tj ET",
        "BT /F1 10 Tf 50 682 Td (We report a boronate-based FRET polymeric micelle probe in PBS buffer at pH 7.4 and 37 C.) Tj ET",
        "BT /F1 10 Tf 50 666 Td (The probe achieves a limit of detection of 12 nM and a linear range of 0.05 to 10 uM.) Tj ET",
        "BT /F1 11 Tf 50 635 Td (2. Results and Discussion) Tj ET",
        "BT /F1 10 Tf 50 617 Td (As shown in Figure 1, addition of H2O2 in PBS at pH 7.4 triggers a 18.5-fold ratiometric) Tj ET",
        "BT /F1 10 Tf 50 601 Td (fluorescence enhancement within 15 min with a limit of detection of 12 nM, while interfering ROS species show negligible response.) Tj ET",
        "BT /F1 10 Tf 60 560 Td ((a)) Tj ET",
        "80 440 180 125 re S",
        "90 455 m 245 550 l S",
        "BT /F1 8 Tf 115 428 Td (Wavelength nm) Tj ET",
        "BT /F1 10 Tf 290 560 Td ((b)) Tj ET",
        "310 440 180 125 re S",
        "320 460 m 475 545 l S",
        "BT /F1 8 Tf 345 428 Td (H2O2 Concentration uM) Tj ET",
        "BT /F1 9 Tf 50 402 Td (Figure 1: (a) Fluorescence emission spectra in PBS pH 7.4; (b) Linear calibration curve LOD = 12 nM.) Tj ET",
    ]
    stream_data = "\n".join(stream_lines).encode("ascii")
    objs = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n",
        b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
        f"5 0 obj\n<< /Length {len(stream_data)} >>\nstream\n".encode("ascii") + stream_data + b"\nendstream\nendobj\n",
        b"6 0 obj\n<< /Title (Ratiometric Fluorescent Probe for Mitochondrial H2O2 Detection) /Author (Ming Wang; Lin Zhang) /Subject (Analytical Chemistry CC-BY-4.0) >>\nendobj\n",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objs:
        offsets.append(len(out))
        out.extend(obj)
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode("ascii"))
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    out.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R /Info 6 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("ascii"))
    pdf_path.write_bytes(bytes(out))


class TestSEAAFusionAndResearchCore(unittest.TestCase):
    def test_01_core_zero_ppt_coupling(self):
        core_dir = SCRIPTS_DIR / "research_core"
        verify_core_zero_ppt_coupling(core_dir)
        for py_file in core_dir.glob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            for tok in FORBIDDEN_PPT_TOKENS:
                self.assertNotIn(tok, text)

    def test_02_literature_discovery_dedup_and_abstract_only_isolation(self):
        self.assertEqual(normalize_doi("https://doi.org/10.1039/C4CC09120E."), "10.1039/c4cc09120e")
        self.assertGreater(title_similarity("Fluorescent probe for H2O2 in mitochondria", "Fluorescent probe for H2O2 in live mitochondria"), 0.75)
        res = discover_from_local_catalog(CORE_LIT_JSON, query="H2O2 FRET 聚合物胶束 线粒体", max_results=10)
        self.assertGreaterEqual(res["total_raw_records"], 10)
        self.assertGreaterEqual(len(res["selected_sources"]), 3)
        # Every catalog record without a local PDF must be strictly marked ABSTRACT_ONLY or METADATA_ONLY
        for src in res["selected_sources"]:
            self.assertIn(src.access_status, (AccessStatus.ABSTRACT_ONLY.value, AccessStatus.METADATA_ONLY.value))

    def test_03_pdf_section_multipanel_figure_and_table_extraction(self):
        with tempfile.TemporaryDirectory(dir=WORK_TMP_ROOT) as tmp:
            tmp_path = Path(tmp)
            synth_pdf = tmp_path / "multipanel_probe.pdf"
            build_synthetic_multipanel_scientific_pdf(synth_pdf)
            assets_out = tmp_path / "extracted_assets"
            parsed = parse_scientific_pdf(synth_pdf, source_id="SRC001", output_assets_dir=assets_out)

            src = parsed["source"]
            self.assertEqual(src.access_status, AccessStatus.FULL_TEXT_LOCAL.value)
            self.assertEqual(src.doi, "10.1039/c4cc09120e")
            self.assertEqual(src.license_name, "CC-BY-4.0")
            self.assertGreaterEqual(len(parsed["passages"]), 2)

            fig_assets = [a for a in parsed["assets"] if a.figure_or_table_id == "Figure 1"]
            self.assertEqual(len(fig_assets), 1)
            fig1 = fig_assets[0]
            self.assertTrue(fig1.is_multi_panel)
            self.assertEqual(fig1.panel_labels, ["a", "b"])
            self.assertEqual(fig1.status, AssetStatus.ACQUIRED.value)
            self.assertEqual(fig1.rights.status, RightsStatus.ALLOWED.value)
            self.assertTrue(Path(fig1.path).is_file())
            self.assertGreaterEqual(len(fig1.mention_contexts), 1)

            # Also test real PLOS PDF table extraction
            plos_parsed = parse_scientific_pdf(PLOS_PDF, source_id="SRC002", pages_spec=[1], output_assets_dir=tmp_path / "plos_assets")
            table_assets = [a for a in plos_parsed["assets"] if a.asset_kind == "data_table"]
            self.assertGreaterEqual(len(table_assets), 1)

    def test_04_claim_evidence_grounding_and_consistency_auditor(self):
        with tempfile.TemporaryDirectory(dir=WORK_TMP_ROOT) as tmp:
            tmp_path = Path(tmp)
            synth_pdf = tmp_path / "multipanel_probe.pdf"
            build_synthetic_multipanel_scientific_pdf(synth_pdf)
            parsed = parse_scientific_pdf(synth_pdf, source_id="SRC001", output_assets_dir=tmp_path / "assets")

            claims = [
                ClaimNode(
                    claim_id="C001",
                    statement="The boronate FRET probe in PBS at pH 7.4 and 37 °C achieves a limit of detection of 12 nM.",
                ),
            ]
            ev_list = ground_claims_to_evidence(claims, [parsed["source"]], parsed["passages"], assets=parsed["assets"])
            self.assertGreaterEqual(len(ev_list), 1)
            ev0 = ev_list[0]
            self.assertEqual(ev0.verification_level, VerificationLevel.APPROVED.value)
            self.assertTrue(any(m.value == 12 and m.unit == "nM" for m in ev0.measurements))
            self.assertTrue(any("pH 7.4" in c for c in ev0.conditions))
            self.assertGreaterEqual(len(ev0.asset_ids), 1)

            bundle = ResearchBundle(
                bundle_id="TEST_BUNDLE_01",
                title="Grounding Test",
                sources=[parsed["source"]],
                passages=parsed["passages"],
                evidence=ev_list,
                claims=claims,
                assets=parsed["assets"],
            )
            issues = audit_scientific_consistency(bundle, base_dir=tmp_path)
            self.assertEqual(sum(1 for i in issues if i.severity == "P0_BLOCKER"), 0)

            # Inject value mismatch in claim (claiming 0.5 nM when evidence says 12 nM)
            bundle.claims[0].statement = "The boronate FRET probe achieves a limit of detection of 0.5 nM."
            issues_mismatch = audit_scientific_consistency(bundle, base_dir=tmp_path)
            self.assertTrue(any(i.category == "CLAIM_EVIDENCE_VALUE_MISMATCH" for i in issues_mismatch))

    def test_05_seaa_adapter_compiles_valid_evidence_package_with_handoff(self):
        with tempfile.TemporaryDirectory(dir=WORK_TMP_ROOT) as tmp:
            tmp_path = Path(tmp)
            synth_pdf = tmp_path / "multipanel_probe.pdf"
            build_synthetic_multipanel_scientific_pdf(synth_pdf)
            parsed = parse_scientific_pdf(synth_pdf, source_id="SRC001", output_assets_dir=tmp_path / "assets")

            handoff = {
                "project_id": "PROJ_FLUOR_2026",
                "revision": 1,
                "slide_order": ["S001"],
                "records": {
                    "C001": {"statement": "The boronate FRET probe achieves a limit of detection of 12 nM in PBS at pH 7.4.", "origin": "literature", "retired": False},
                    "R001": {"question": "What is the LOD and condition of the FRET probe?", "retired": False},
                    "V001": {"kind": "empirical", "retired": False},
                    "S001": {"depends_on": ["C001", "R001", "V001"], "retired": False},
                },
            }
            handoff_file = tmp_path / "PPT_HANDOFF.json"
            handoff_file.write_text(json.dumps(handoff, ensure_ascii=False, indent=2), encoding="utf-8")

            claims = [ClaimNode(claim_id="C001", statement=handoff["records"]["C001"]["statement"])]
            ev_list = ground_claims_to_evidence(claims, [parsed["source"]], parsed["passages"], assets=parsed["assets"])
            for e in ev_list:
                e.task_ids = ["R001"]
                e.extra["slide_ids"] = ["S001"]
            for a in parsed["assets"]:
                a.extra["slide_ids"] = ["S001"]

            bundle = ResearchBundle(
                bundle_id="PROJ_FLUOR_2026",
                title="Fluorescent Probe Deck Evidence",
                revision=1,
                base_revision=1,
                sources=[parsed["source"]],
                passages=parsed["passages"],
                evidence=ev_list,
                claims=claims,
                assets=parsed["assets"],
            )
            pkg_dir = tmp_path / "EVIDENCE_PACKAGE"
            report = compile_evidence_package_from_bundle(bundle, pkg_dir, handoff_path=handoff_file)
            self.assertEqual(report["counts"]["ready_slides"], 1)

            # Verify with original unmodified check_evidence.py in production mode
            checked = check_evidence.check(pkg_dir, handoff=handoff_file, production=True)
            self.assertEqual(checked["header"]["project_id"], "PROJ_FLUOR_2026")


if __name__ == "__main__":
    unittest.main(verbosity=2)
