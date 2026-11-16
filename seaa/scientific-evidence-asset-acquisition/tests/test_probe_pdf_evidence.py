#!/usr/bin/env python3
"""Unit and CLI regression tests for SEAA probe_pdf_evidence.py."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SKILL_DIR = Path(__file__).resolve().parents[1]
PROBE_CLI = SKILL_DIR / "scripts" / "probe_pdf_evidence.py"
BASE = Path("C:/Users/86139/Desktop/codex/outputs/copy-first-evolution-20261112")
SYNTH_PDF = BASE / "experiments" / "seaa" / "evidence" / "synthetic-probe.pdf"
PLOS_PDF = BASE / "experiments" / "seaa" / "evidence" / "plos-1003285.pdf"


class TestProbePdfEvidence(unittest.TestCase):
    def run_cli(self, args):
        cmd = [sys.executable, "-B", "-X", "utf8", str(PROBE_CLI)] + [str(a) for a in args]
        return subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")

    def test_synthetic_pdf_text_table_raster_and_empty_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "synth.json"
            img_dir = Path(tmp) / "imgs"
            res = self.run_cli([SYNTH_PDF, "--out", out, "--include-text", "--extract-images-dir", img_dir])
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("PASS pdf_pages=4 inspected=4 text_pages=1", res.stdout)
            report = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(report["total_file_pages"], 4)
            self.assertEqual(report["non_text_file_pages"], [2, 3, 4])
            self.assertFalse(report["governance"]["scientific_claim_verified"])
            self.assertEqual(report["governance"]["rights_status"], "UNKNOWN_REQUIRES_SEPARATE_CLEARANCE")

            p1 = report["pages"][0]
            self.assertEqual(p1["file_page"], 1)
            self.assertEqual(p1["page_read_status"], "extractable_text")
            self.assertEqual(p1["table_count"], 1)
            self.assertEqual(p1["tables"][0]["rows"], [["Item", "Value"], ["Alpha", "7"]])

            self.assertEqual(report["pages"][1]["page_read_status"], "empty_or_vector_only_no_text")
            self.assertEqual(report["pages"][2]["page_read_status"], "raster_only_no_text_layer")
            self.assertEqual(len(report["pages"][2]["extracted_image_files"]), 1)
            self.assertEqual(report["pages"][3]["page_read_status"], "empty_or_vector_only_no_text")

            dup = self.run_cli([SYNTH_PDF, "--out", out])
            self.assertEqual(dup.returncode, 1)
            self.assertIn("output exists", dup.stderr)

    def test_real_open_access_plos_pdf_page_range_and_unverified_doi(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "plos-p1-2.json"
            res = self.run_cli([PLOS_PDF, "--pages", "1-2", "--out", out])
            self.assertEqual(res.returncode, 0, res.stderr)
            report = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(report["total_file_pages"], 4)
            self.assertEqual(report["inspected_file_pages"], [1, 2])
            self.assertEqual(report["observed_read_scope"], "partial_pages_text_extracted")
            self.assertTrue(any("10.1371/journal.pcbi.1003285" in d for d in report["candidate_dois_unverified"]))

            bad = self.run_cli([PLOS_PDF, "--pages", "1-9", "--out", Path(tmp) / "bad.json"])
            self.assertEqual(bad.returncode, 1)
            self.assertIn("out of bounds", bad.stderr)
            self.assertNotIn("Traceback", bad.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
