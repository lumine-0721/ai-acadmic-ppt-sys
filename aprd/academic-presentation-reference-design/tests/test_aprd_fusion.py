#!/usr/bin/env python3
"""Comprehensive unit & integration tests for APRD Fusion Engine."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile

SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from aprd_engine.ooxml_resolver import (  # noqa: E402
    NS,
    apply_ooxml_color_modifiers,
    resolve_presentation_structure,
)
from aprd_engine.pattern_generalizer import (  # noqa: E402
    estimate_slot_text_capacity,
)
from aprd_engine.reconstructor_verifier import (  # noqa: E402
    generalize_deck_from_spec,
    replicate_deck_with_slot_payload,
    verify_deck_against_spec,
    wcag_contrast_ratio,
)
from aprd_engine.spec_compiler import (  # noqa: E402
    DesignAssetRegistry,
    build_design_specification,
    compile_ppt_reference_package,
    validate_design_spec_schema,
)

# Committed fixtures live at <repo>/fixtures (rebuild with tools/build_pptx_fixtures.py).
# Override with ACADEMIC_PPT_FIXTURES to point at another fixture tree.
FIXTURES = Path(os.environ.get("ACADEMIC_PPT_FIXTURES") or SKILL_DIR.parents[1] / "fixtures")
DEMO_PPTX = FIXTURES / "aprd" / "source-api-demo.pptx"
LEGACY_PPTX = FIXTURES / "apc" / "out" / "instrument-gain-v002.pptx"
HOSTILE_PPTX = FIXTURES / "aprd" / "hostile-template.pptx"
HANDOFF_V002 = FIXTURES / "apc" / "v002" / "PPT_HANDOFF.json"
# Scratch space for generated packages; defaults to the system temp dir (override with APRD_TEST_TMP).
WORK_TMP = Path(os.environ.get("APRD_TEST_TMP") or Path(tempfile.gettempdir()) / "aprd-tests")


class TestAprdFusionEngine(unittest.TestCase):
    def setUp(self):
        WORK_TMP.mkdir(parents=True, exist_ok=True)

    def test_ooxml_color_modifiers_and_group_affine_resolution(self):
        # 1. Test lumMod + lumOff color modifier resolution
        node = ET.fromstring(
            '<a:schemeClr xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" val="accent1">'
            '<a:lumMod val="75000"/><a:lumOff val="25000"/><a:alpha val="85000"/>'
            "</a:schemeClr>"
        )
        resolved_hex, alpha, mods = apply_ooxml_color_modifiers("1F4E79", node)
        self.assertTrue(resolved_hex.startswith("#") and len(resolved_hex) == 7)
        self.assertNotEqual(resolved_hex, "#1F4E79")
        self.assertEqual(alpha, 0.85)
        self.assertEqual(len(mods), 3)

        # 2. Test full deck structure resolution on source-api-demo.pptx
        before_sha = hashlib.sha256(DEMO_PPTX.read_bytes()).hexdigest()
        resolved = resolve_presentation_structure(DEMO_PPTX, scrub_text=True)
        after_sha = hashlib.sha256(DEMO_PPTX.read_bytes()).hexdigest()
        self.assertEqual(before_sha, after_sha)
        self.assertEqual(resolved["slide_count"], 3)

        # Slide 1 inherited title placeholder should have resolved EMU coordinates
        s1_title = resolved["slides"][0]["objects"][0]
        self.assertEqual(s1_title["world_box_emu"], [548640, 365760, 8046720, 914400])
        self.assertEqual(s1_title["geometry_source"], "layout")

        # Slide 2 theme-colored textbox with brightness=0.25 should resolve to concrete hex
        s2_theme_box = resolved["slides"][1]["objects"][2]
        p0_style = s2_theme_box["text_body"]["paragraphs"][0]["effective_style"]
        self.assertTrue(p0_style["color_hex"].startswith("#"))

        # Slide 3 group shape children should have composed world coordinates
        s3_grp = next(o for o in resolved["slides"][2]["objects"] if o["object_type"] == "grpSp")
        self.assertGreaterEqual(len(s3_grp["children"]), 1)
        child0 = s3_grp["children"][0]
        self.assertEqual(len(child0["world_box_emu"]), 4)
        self.assertTrue(all(v > 0 for v in child0["world_box_emu"]))

    def test_content_scrubbing_and_schema_compliance(self):
        spec = build_design_specification(DEMO_PPTX, ref_id="REF001", scrub_text=True)
        ok, errors, validator = validate_design_spec_schema(spec)
        self.assertTrue(ok, f"Schema errors: {errors}")
        self.assertTrue(spec["validation_metadata"]["schema_valid"])
        self.assertIn("jsonschema", validator)
        self.assertTrue(spec["source_provenance"]["extraction_metadata"]["content_scrubbed"])
        # Ensure raw slide text is not leaked into DESIGN_SPECIFICATION.json
        serialized = json.dumps(spec, ensure_ascii=False)
        self.assertGreaterEqual(len(spec["layout_patterns"]), 1)
        self.assertEqual(len(spec["slide_roles"]), 3)

    def test_slot_capacity_and_visual_conflict_arbitration(self):
        cap = estimate_slot_text_capacity(width_pt=420.0, height_pt=180.0, font_size_pt=15.0)
        self.assertTrue(cap["advisory_only"])
        self.assertGreater(cap["cjk_chars_per_line"], 10)
        self.assertGreater(cap["latin_chars_per_line"], cap["cjk_chars_per_line"])
        self.assertGreaterEqual(cap["max_lines"], 4)

        # Inject a deliberate conflict where visual_annotations claims shape_id 2 is a picture ("pic")
        s1_first_id = str(resolve_presentation_structure(DEMO_PPTX)["slides"][0]["objects"][0]["shape_id"])
        visual_ann = {
            "slides": {
                "1": {
                    "objects": {
                        s1_first_id: {
                            "claimed_object_type": "pic",
                            "role": "MAIN_TITLE",
                            "confidence": 0.91,
                        }
                    }
                }
            }
        }
        spec = build_design_specification(DEMO_PPTX, ref_id="REF001", visual_annotations=visual_ann)
        conflicts = spec["source_traceability"]["conflicts"]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["resolution"], "structural_fact_retained_with_visual_role_note")

    def test_compile_package_and_registry_caching(self):
        with tempfile.TemporaryDirectory(dir=WORK_TMP) as tmp:
            tmp_path = Path(tmp)
            reg_dir = tmp_path / "registry"
            pkg_dir = tmp_path / "PPT_REFERENCE_PACKAGE"

            spec = build_design_specification(LEGACY_PPTX, ref_id="REF001")
            reg = DesignAssetRegistry(reg_dir)
            self.assertIsNone(reg.lookup(LEGACY_PPTX))
            reg.register(LEGACY_PPTX, spec)
            cached = reg.lookup(LEGACY_PPTX)
            self.assertIsNotNone(cached)
            self.assertTrue(cached["cache_hit"])

            res = compile_ppt_reference_package(
                spec=spec,
                source_pptx=LEGACY_PPTX,
                package_dir=pkg_dir,
                handoff_path=HANDOFF_V002,
                design_revision=2,
            )
            self.assertEqual(res["mapped_slides"], 2)
            self.assertTrue((pkg_dir / "DESIGN_SPECIFICATION.json").is_file())
            self.assertTrue((pkg_dir / "layouts.json").is_file())

    def test_level2_replication_and_level3_generalization(self):
        with tempfile.TemporaryDirectory(dir=WORK_TMP) as tmp:
            tmp_path = Path(tmp)
            spec = build_design_specification(DEMO_PPTX, ref_id="REF001")

            # Level 2 Replication with slot replacement
            rep_pptx = tmp_path / "replicated.pptx"
            payloads = {
                "1": {"TITLE_SLOT": "Replicated Academic Title — 2026 Benchmark"},
            }
            rep_res = replicate_deck_with_slot_payload(DEMO_PPTX, spec, rep_pptx, payloads)
            self.assertTrue(rep_pptx.is_file())
            self.assertGreaterEqual(rep_res["slots_updated"], 1)

            # Level 3 Generalization to a brand-new 4-slide Chemistry Fluorescent Probe deck
            gen_pptx = tmp_path / "generalized_probe.pptx"
            new_plan = {
                "deck_title": "Ratiometric Fluorescent Probe for Hypochlorous Acid Imaging",
                "slides": [
                    {
                        "content_role": "Cover",
                        "title": "近红外比率型荧光探针的设计与活体成像研究",
                        "subtitle": "课题组中期进展汇报 · 2026",
                    },
                    {
                        "content_role": "Method",
                        "title": "探针分子 ICT-FRET 双机制协同响应路线",
                        "claims": [
                            "识别基团：吩噻嗪硫醚位点选择性氧化为亚砜",
                            "响应速率：t1/2 < 4.2 s（PBS pH 7.4, 37 °C）",
                        ],
                    },
                    {
                        "content_role": "Results",
                        "title": "荧光光谱滴定显示 I580/I460 比值呈 48 倍线性增强",
                        "slots": {
                            "MAIN_FIGURE_SLOT": {
                                "figure_title": "Fluorescence Titration Spectra (0-50 uM HOCl)",
                                "data_points": ["LOD = 18.4 nM (3σ/k)", "R2 = 0.9982 across 0.05-40 uM"],
                            },
                            "EXPLANATION_SLOT": [
                                "580 nm 发射峰随 HOCl 浓度增加显著上升，460 nm 参比峰保持稳定",
                                "对常见活性氧（H2O2, ONOO-, ·OH）表现出 >35 倍选择性",
                            ],
                        },
                    },
                    {
                        "content_role": "Conclusion",
                        "title": "核心结论与下一步动物炎症模型验证计划",
                        "claims": [
                            "完成了克级合成与光谱表征，检出限达 18.4 nM",
                            "下一步：开展斑马鱼与小鼠腹膜炎原位双通道成像",
                        ],
                    },
                ],
            }
            gen_res = generalize_deck_from_spec(spec, new_plan, gen_pptx)
            self.assertEqual(gen_res["slide_count"], 4)
            v_rep = verify_deck_against_spec(gen_pptx, spec)
            self.assertEqual(v_rep["summary"]["failed"], 0)
            self.assertGreater(v_rep["summary"]["editable_objects"], 8)
            self.assertGreater(wcag_contrast_ratio("#1A202C", "#FFFFFF"), 10.0)

    def test_hostile_zero_slide_template_and_cli_roundtrip(self):
        with tempfile.TemporaryDirectory(dir=WORK_TMP) as tmp:
            tmp_path = Path(tmp)
            spec = build_design_specification(HOSTILE_PPTX, ref_id="REF004")
            self.assertTrue(spec["validation_metadata"]["schema_valid"])
            self.assertEqual(spec["source_provenance"]["extraction_metadata"]["slide_count"], 11)
            self.assertGreaterEqual(len(spec["unsupported_features"]), 1)
            pkg_dir = tmp_path / "hostile_pkg"
            pkg_res = compile_ppt_reference_package(spec, HOSTILE_PPTX, pkg_dir, design_revision=1)
            self.assertGreaterEqual(pkg_res["layout_count"], 5)

            # CLI extract & validate-spec smoke test
            cli_spec = tmp_path / "cli_spec.json"
            cmd = [sys.executable, "-B", "-X", "utf8", str(SCRIPTS_DIR / "aprd_cli.py"), "extract", str(DEMO_PPTX), str(cli_spec)]
            r = subprocess.run(cmd, capture_output=True, encoding="utf-8")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("PASS cache_hit=false", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
