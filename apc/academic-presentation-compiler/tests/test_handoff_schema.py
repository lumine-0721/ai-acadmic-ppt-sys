#!/usr/bin/env python3
"""Unit and CLI regression tests for APC check_handoff_schema.py and check_handoff.py."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SKILL_DIR = Path(__file__).resolve().parents[1]
SCHEMA_CLI = SKILL_DIR / "scripts" / "check_handoff_schema.py"
STD_CLI = SKILL_DIR / "scripts" / "check_handoff.py"
FIXTURES = SKILL_DIR / "tests" / "fixtures"
# Committed fixtures live at <repo>/fixtures (rebuild with tools/build_apc_fixtures.py).
# Override with ACADEMIC_PPT_FIXTURES to point at another fixture tree.
FIXTURE_ROOT = Path(os.environ.get("ACADEMIC_PPT_FIXTURES") or SKILL_DIR.parents[1] / "fixtures")
LEGACY = FIXTURE_ROOT / "apc"  # holds v001/ and v002/ (PPT_REQUIREMENTS.md + PPT_HANDOFF.json)


class TestHandoffSchema(unittest.TestCase):
    def run_cli(self, script, args):
        cmd = [sys.executable, "-B", "-X", "utf8", str(script)] + [str(a) for a in args]
        return subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")

    def test_legacy_v001_and_v002_pass(self):
        for ver, final_flag in [("v001", []), ("v002", ["--final", "--strict-assets"])]:
            req = LEGACY / ver / "PPT_REQUIREMENTS.md"
            idx = LEGACY / ver / "PPT_HANDOFF.json"
            res = self.run_cli(SCHEMA_CLI, [idx, "--requirements", req, *final_flag])
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("PASS schema_valid=true semantic_valid=true errors=0", res.stdout)

    def test_extension_fields_and_retired_records_preserved(self):
        idx = json.loads((LEGACY / "v002" / "PPT_HANDOFF.json").read_text(encoding="utf-8"))
        idx["future_top_level_extension"] = {"note": "allowed"}
        idx["records"]["M001"]["custom_tag"] = "forward-compatible"
        idx["records"]["R099"] = {
            "summary": "Retired task",
            "depends_on": [],
            "retired": True,
        }
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "extended.json"
            out = Path(tmp) / "diag.json"
            p.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")
            res = self.run_cli(SCHEMA_CLI, [p, "--out", out])
            self.assertEqual(res.returncode, 0, res.stderr)
            report = json.loads(out.read_text(encoding="utf-8"))
            self.assertTrue(report["valid"])
            self.assertEqual(report["error_count"], 0)
            res_overwrite = self.run_cli(SCHEMA_CLI, [p, "--out", out])
            self.assertEqual(res_overwrite.returncode, 1)
            self.assertIn("output exists", res_overwrite.stderr)

    def test_multiple_structural_errors_reported_together(self):
        res = self.run_cli(SCHEMA_CLI, [FIXTURES / "multiple-errors.json"])
        self.assertEqual(res.returncode, 1)
        self.assertGreaterEqual(res.stderr.count("FAIL [schema]"), 3, res.stderr)

    def test_malformed_json_and_duplicate_keys_clean_failure(self):
        for name in ("syntax-error.json", "duplicate-key.json"):
            res = self.run_cli(SCHEMA_CLI, [FIXTURES / name])
            self.assertEqual(res.returncode, 1)
            self.assertIn("FAIL [parse]", res.stderr)
            self.assertNotIn("Traceback", res.stderr)

    def test_semantic_only_failure_caught_when_schema_passes(self):
        bad_sem = LEGACY / "v002" / "missing-support.json"
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "sem-diag.json"
            res = self.run_cli(SCHEMA_CLI, [bad_sem, "--out", out])
            self.assertEqual(res.returncode, 1)
            report = json.loads(out.read_text(encoding="utf-8"))
            self.assertTrue(report["schema_valid"])
            self.assertFalse(report["semantic_valid"])
            self.assertFalse(report["valid"])
            self.assertEqual(report["errors"][0]["layer"], "semantic")


if __name__ == "__main__":
    unittest.main(verbosity=2)
