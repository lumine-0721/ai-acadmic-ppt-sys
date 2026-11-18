#!/usr/bin/env python3
"""Rebuild the committed APRD design package and SEAA evidence package fixtures.

Usage (from the repo root):  python tools/build_package_fixtures.py
Run tools/build_apc_fixtures.py and tools/build_seaa_fixtures.py first (or use tools/build_fixtures.py).

Outputs (both are consistent with fixtures/apc/v002/PPT_HANDOFF.json, project "instrument-gain-smoke"):
  fixtures/aprd/design-v002/PPT_REFERENCE_PACKAGE/   validated by aprd check_package.py
  fixtures/seaa/v002/EVIDENCE_PACKAGE/               validated by seaa check_evidence.py

Everything is invented test data. The package files used to live under C:/Users/86139/Desktop/codex/outputs/.
"""
from __future__ import annotations

import hashlib
import json
import struct
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIX = REPO / "fixtures"
PROJECT = "instrument-gain-smoke"
BASE_REVISION = 2


def dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body.strip() + "\n", encoding="utf-8", newline="\n")


# ------------------------------------------------------------------ APRD design package
def build_design() -> None:
    out = FIX / "aprd" / "design-v002" / "PPT_REFERENCE_PACKAGE"
    write(out / "REFERENCE_REPORT.md", """
# 参考设计报告（合成夹具）
唯一参考：fixtures/apc/out/instrument-gain-v002.pptx（REF001），逐页解析到对象级。
内容为虚构，只用于校验器与编排器测试。
""")
    write(out / "RECONSTRUCTION_GUIDE.md", """
# 复现指南（合成夹具）
按 LAYOUT_LIBRARY.json 的 0-100 归一化槽位重建两种版式：封面式问题页、标题加主图的结果页。
""")
    write(out / "REFERENCE_COVERAGE_REPORT.md", """
# 覆盖报告（合成夹具）
| 页面 | 覆盖 | 版式 |
|---|---|---|
| S001 | COVERED | LAYOUT001 |
| S002 | COVERED | LAYOUT002 |
""")
    # check_package reads rows that start with "| S001 | COVERED |"; the header row is ignored.
    dump(out / "SOURCE_MANIFEST.json", {"references": [{
        "ref_id": "REF001",
        "title": "instrument-gain-v002 (synthetic two-slide reference deck)",
        "source_url_or_path": "fixtures/apc/out/instrument-gain-v002.pptx",
        "file_format": "pptx",
        "editability_status": "editable",
        "usage_notes": "Synthetic fixture deck; geometry is the python-pptx default Title Only layout.",
        "page_count_if_verified": 2,
        "inspected_pages": [1, 2],
        "source_type": "deck",
        "access_status": "opened",
        "inspection_level": "object_parsed",
        "rights_status": "reference_only",
        "selected_role": "primary",
    }]})
    dump(out / "DESIGN_SYSTEM.json", {
        "design_revision": 1,
        "primary_anchor_ref_id": "REF001",
        "canvas": {"width": 9144000, "height": 6858000, "unit": "emu"},
        "provenance": [
            {"parameter": "canvas", "basis": "presentation.xml sldSz of REF001", "ref_id": "REF001", "precision": "exact"},
            {"parameter": "palette", "basis": "default Office theme, not tuned", "ref_id": None, "precision": "derived"},
        ],
        "grid": {"columns": 12, "gutter_percent": 2},
        "margins": {"left_percent": 6, "right_percent": 6, "top_percent": 5, "bottom_percent": 6},
        "colors": {"text": "#1A202C", "background": "#FFFFFF", "accent": "#1F4E79"},
        "typography": {"headline_pt": 32, "body_pt": 18, "caption_pt": 12},
        "spacing": {"paragraph_pt": 6, "block_gap_percent": 3},
        "shapes": {"corner": "square", "line_pt": 1},
        "components": {"title": "single-line headline", "figure": "one main figure with caption"},
        "graphics": {"style": "flat, no shadows"},
        "image_treatment": {"crop": "none", "border": "none"},
        "chart_style": {"axes": "labelled with units", "gridlines": "light"},
        "consistency_rules": ["one headline per slide", "units on every axis"],
        "constraints": ["do not crop axes", "keep figure aspect ratio"],
    })
    common = {
        "alignment_rules": ["left-align headline and body to the same margin"],
        "fixed_constraints": ["headline stays above content"],
        "variable_parameters": ["headline text", "figure"],
        "content_capacity": {"headline_max_chars": 40, "note": "one line at 32 pt"},
        "adaptation_rules": ["overflow goes to speaker notes; never shrink below 24 pt"],
        "scientific_content_compatibility": ["context slides", "one empirical figure per slide"],
        "source_ref_id": "REF001",
        "precision": "exact",
    }
    dump(out / "LAYOUT_LIBRARY.json", {"layouts": [
        {**common, "layout_id": "LAYOUT001", "semantic_role": "problem statement", "source_slide": 1,
         "element_hierarchy": ["headline", "body"],
         "normalized_geometry": [{"slot": "headline", "x": 6, "y": 5, "w": 88, "h": 15},
                                 {"slot": "body", "x": 6, "y": 25, "w": 88, "h": 65}]},
        {**common, "layout_id": "LAYOUT002", "semantic_role": "result with main figure", "source_slide": 2,
         "element_hierarchy": ["headline", "main_figure", "caption"],
         "normalized_geometry": [{"slot": "headline", "x": 6, "y": 5, "w": 88, "h": 15},
                                 {"slot": "main_figure", "x": 6, "y": 25, "w": 88, "h": 55},
                                 {"slot": "caption", "x": 6, "y": 82, "w": 88, "h": 10}]},
    ]})
    dump(out / "SLIDE_STYLE_MAPPING.json", {
        "scope": "project",
        "project_id": PROJECT,
        "base_revision": BASE_REVISION,
        "design_revision": 1,
        "target_slide_ids": ["S001", "S002"],
        "mappings": [
            {"target_slide_id": "S001", "required_content_type": "problem statement",
             "adaptation_instructions": "Use the headline and one body block.",
             "selected_layout_id": "LAYOUT001", "reference_source": "REF001", "coverage": "COVERED",
             "visual_requirements": ["V002"], "layout_risks": [], "unresolved_requirements": []},
            {"target_slide_id": "S002", "required_content_type": "empirical result",
             "adaptation_instructions": "Place the gain curve in the main figure slot with its caption.",
             "selected_layout_id": "LAYOUT002", "reference_source": "REF001", "coverage": "COVERED",
             "visual_requirements": ["V001"], "layout_risks": [], "unresolved_requirements": []},
        ],
    })


# ------------------------------------------------------------------ SEAA evidence package
def tiny_png(size: int = 16) -> bytes:
    """A valid grey-gradient PNG, built with zlib only so the bytes are reproducible."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + bytes((x * 255 // (size - 1)) for x in range(size)) for _ in range(size))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def build_evidence() -> None:
    out = FIX / "seaa" / "v002" / "EVIDENCE_PACKAGE"
    png = tiny_png()
    png_path = out / "assets" / "gain-curve.png"
    png_path.parent.mkdir(parents=True, exist_ok=True)
    png_path.write_bytes(png)
    sha = hashlib.sha256(png).hexdigest()
    write(out / "00_RESEARCH_OVERVIEW.md", """
# 证据概览（合成夹具）
项目 instrument-gain-smoke：只有一条用户自有证据 E001，支撑 C002。没有外部文献。
""")
    write(out / "04_RETRIEVAL_LOG.md", """
# 检索记录（合成夹具）
R001：确认结论不依赖外部文献，未执行任何在线检索。
""")
    write(out / "05_GAPS_AND_CONFLICTS.md", """
# 缺口与冲突（合成夹具）
当前无缺口、无冲突证据。
""")
    write(out / "06_HANDOFF_TO_PROMPT3.md", """
# 交接摘要（合成夹具）
| 页面 | 状态 | 说明 |
|---|---|---|
| S001 | READY | 背景页，无需素材 |
| S002 | READY | 结果页，E001 与 A001 已核 |
""")
    dump(out / "01_SOURCE_MANIFEST.json", {"sources": [{
        "source_id": "SRC001",
        "title": "Instrument gain calibration table (synthetic user data)",
        "url_or_path": "user-data/instrument-gain-calibration.csv",
        "usage_notes": "User-owned data; values are invented for tests.",
        "origin": "user_raw",
        "read_scope": "data",
        "verification_status": "VERIFIED",
        "metadata": {"authors": ["fixture"], "year": 2026, "doi": None, "publication": None, "peer_reviewed": None},
    }]})
    dump(out / "03_ASSET_MANIFEST.json", {"assets": [{
        "asset_id": "A001",
        "source_id": "SRC001",
        "evidence_ids": ["E001"],
        "slide_ids": ["S002"],
        "status": "ACQUIRED",
        "source_locator": "Table 1, rows 1-12",
        "path": "assets/gain-curve.png",
        "sha256": sha,
        "format": "png",
        "caption": "设定增益与实测输出（合成数据，仅作夹具）",
        "conditions": ["synthetic data", "gain 1-100x"],
        "rights": {"status": "ALLOWED", "basis": "user-owned data", "attribution": ""},
        "scientific_immutable": ["axis ranges", "units"],
        "transformation": {"kind": "original", "basis": "tiny placeholder image, no data re-plotted"},
    }]})
    dump(out / "02_EVIDENCE_MANIFEST.json", {
        "project_id": PROJECT,
        "base_revision": BASE_REVISION,
        "package_revision": 1,
        "mode": "GIVEN_FIRST_AUDIT",
        "scope": "full",
        "target_slide_ids": ["S001", "S002"],
        "claim_ids": ["C001", "C002"],
        "evidence": [{
            "evidence_id": "E001",
            "source_id": "SRC001",
            "origin": "user",
            "status": "VERIFIED",
            "verification_scope": "calibration table read in full",
            "locator": "Table 1, rows 1-12",
            "subject": "instrument gain linearity",
            "method": "linear least-squares fit of measured output against set gain",
            "finding": "Measured output is linear in set gain from 1x to 100x (R2 = 0.999, invented value).",
            "measurements": [{"value": 0.999, "unit": "1", "definition": "R2 of the linear fit"}],
            "conditions": ["room temperature", "fixed input amplitude"],
            "controls": ["repeat measurement at each gain setting"],
            "limitations": ["single instrument", "range limited to 1-100x"],
            "alternative_explanations": ["detector saturation would bend the curve above 100x"],
            "applicability": "Only this instrument within the tested range.",
            "claim_links": [{"claim_id": "C002", "relation": "supports"}],
            "slide_ids": ["S002"],
            "task_ids": ["R001"],
        }],
        "tasks": [{"task_id": "R001", "status": "DONE", "evidence_ids": ["E001"], "asset_ids": ["A001"],
                   "resolution": "Answered from the user's own calibration table; no literature needed."}],
        "comparisons": [],
        "slides": [
            {"slide_id": "S001", "status": "READY",
             "scientific_message": "增益必须在使用前标定",
             "asset_rationale": "背景页，不需要实验素材",
             "claims": [{"claim_id": "C001", "purpose": "discuss", "disclosure": "背景说明，不是实验结论"}],
             "evidence_ids": [], "asset_ids": [], "visual_ids": ["V002"], "asset_requirement": "none",
             "must_preserve": [], "prohibited_inferences": [], "gaps": [], "comparison_ids": []},
            {"slide_id": "S002", "status": "READY",
             "scientific_message": "1-100 倍范围内增益线性",
             "asset_rationale": "增益曲线直接展示线性关系",
             "claims": [{"claim_id": "C002", "purpose": "establish", "disclosure": ""}],
             "evidence_ids": ["E001"], "asset_ids": ["A001"], "visual_ids": ["V001"],
             "asset_requirement": "required",
             "must_preserve": ["axis ranges", "units"], "prohibited_inferences": ["extrapolation above 100x"],
             "gaps": [], "comparison_ids": []},
        ],
    })


def main() -> None:
    build_design()
    build_evidence()
    print("wrote package fixtures under", FIX)


if __name__ == "__main__":
    main()
