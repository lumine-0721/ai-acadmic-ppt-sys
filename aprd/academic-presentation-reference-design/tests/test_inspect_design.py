#!/usr/bin/env python3
"""Regression and capability tests for APRD inspect_design.py, inspect_pptx.py, and check_package.py."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SKILL_DIR = Path(__file__).resolve().parents[1]
DESIGN_CLI = SKILL_DIR / "scripts" / "inspect_design.py"
DIRECT_CLI = SKILL_DIR / "scripts" / "inspect_pptx.py"
# Committed fixtures live at <repo>/fixtures (rebuild with tools/build_pptx_fixtures.py).
# Override with ACADEMIC_PPT_FIXTURES to point at another fixture tree.
FIXTURES = Path(os.environ.get("ACADEMIC_PPT_FIXTURES") or SKILL_DIR.parents[1] / "fixtures")
DEMO_PPTX = FIXTURES / "aprd" / "source-api-demo.pptx"
LEGACY_PPTX = FIXTURES / "apc" / "out" / "instrument-gain-v002.pptx"


class TestInspectDesign(unittest.TestCase):
    def run_cli(self, script, args):
        cmd = [sys.executable, "-B", "-X", "utf8", str(script)] + [str(a) for a in args]
        return subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")

    def test_inherited_placeholder_geometry_resolved_vs_direct_null(self):
        before_sha = hashlib.sha256(DEMO_PPTX.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            direct_out = Path(tmp) / "direct.json"
            design_out = Path(tmp) / "design.json"
            r1 = self.run_cli(DIRECT_CLI, [DEMO_PPTX, direct_out])
            self.assertEqual(r1.returncode, 0, r1.stderr)
            r2 = self.run_cli(DESIGN_CLI, [DEMO_PPTX, design_out])
            self.assertEqual(r2.returncode, 0, r2.stderr)
            self.assertIn("PASS design_pages=3 library=python-pptx/1.0.2 rendered=false", r2.stdout)

            direct_data = json.loads(direct_out.read_text(encoding="utf-8"))
            design_data = json.loads(design_out.read_text(encoding="utf-8"))

            s1_direct_title = direct_data["slides"][0]["objects"][0]
            self.assertIsNone(s1_direct_title["geometry"])

            s1_design_title = design_data["slides"][0]["design_objects"][0]
            fields = s1_design_title["geometry"]["fields"]
            for dim, expected_emu in [("left", 548640), ("top", 365760), ("width", 8046720), ("height", 914400)]:
                self.assertEqual(fields[dim]["status"], "known")
                self.assertEqual(fields[dim]["value"], expected_emu)
                self.assertEqual(fields[dim]["source"]["level"], "layout")

            s2_objs = design_data["slides"][1]["design_objects"]
            rect = s2_objs[1]
            self.assertEqual(rect["direct_fill"]["color"]["unmodified_srgb"], "DCE6F1")
            theme_box = s2_objs[2]
            run_color = theme_box["style_declarations"][0]["properties"]["color"]
            self.assertEqual(run_color["value"]["kind"], "schemeClr")
            self.assertIsNone(run_color["unmodified_srgb"])
            self.assertEqual(run_color["rendered_color"]["status"], "unknown")

            s3_objs = design_data["slides"][2]["design_objects"]
            grp = next(o for o in s3_objs if o["object_type"] == "grpSp")
            self.assertIn("group_transform_not_composed", grp["unknowns"])
            self.assertEqual(grp["children"][0]["geometry"]["coordinate_space"], "group_local")
            tbl = next(o for o in s3_objs if o["object_type"] == "graphicFrame")
            self.assertIn("content_and_styles_not_interpreted:graphicFrame", tbl["unknowns"])

            r_dup = self.run_cli(DESIGN_CLI, [DEMO_PPTX, design_out])
            self.assertEqual(r_dup.returncode, 1)
            self.assertIn("output exists", r_dup.stderr)

        after_sha = hashlib.sha256(DEMO_PPTX.read_bytes()).hexdigest()
        self.assertEqual(before_sha, after_sha)

    def test_legacy_deck_compatible(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "legacy-design.json"
            res = self.run_cli(DESIGN_CLI, [LEGACY_PPTX, out])
            self.assertEqual(res.returncode, 0, res.stderr)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["page_count"], 2)
            self.assertFalse(data["rendered"])
            self.assertIn("ppt/slideLayouts/slideLayout6.xml", data["layouts"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
