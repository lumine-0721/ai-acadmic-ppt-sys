"""Stage F (Page Design + Pillow schematic preview) unit tests."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from pptv01.contracts import (
    AssetRegistry,
    CANVAS_H_IN,
    CANVAS_W_IN,
    ElementKind,
    PageDesign,
    PageDesignEntry,
    ProductionPlan,
    SlideAssetMapping,
    SlidePlan,
    StageStatus,
    UserInputRequests,
    load_contract,
)
from pptv01.f_layout import (
    LayoutRuleError,
    estimate_slot_capacity,
    fit_text_to_box,
    run_stage_f,
    validate_page_geometry,
    wrap_mixed_text,
)
from pptv01.runner import run_pipeline
from tests.conftest import GOOD_A, GOOD_B, GOOD_C
from tests.fakes import FakeClient

RID = "20260101-000000-abcd"


def _png(w=800, h=1200) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (w, h), (20, 80, 140)).save(b, "PNG")
    return b.getvalue()


def test_capacity_estimation_and_overflow_fitting_cjk_and_latin():
    cap = estimate_slot_capacity(w_in=4.0, h_in=2.0, font_size_pt=20.0)
    assert cap.cjk_chars_per_line < cap.latin_chars_per_line
    assert cap.max_lines >= 4

    long_mixed = (
        "城市热岛效应 (Urban Heat Island, UHI) 是指城市中心区域的温度明显高于周边乡村地区的现象；"
        "主要影响因素包括不透水下垫面比例增加、建筑密集阻碍通风、植被蒸腾减少以及工业与交通人为排热。"
    )
    wrapped = wrap_mixed_text(long_mixed, w_in=3.5, font_size_pt=22.0)
    assert len(wrapped) >= 4
    assert "".join(wrapped).replace(" ", "") == long_mixed.replace(" ", "")  # no silent truncation

    fit = fit_text_to_box(long_mixed, w_in=3.8, h_in=1.6, font_size_pt=22.0, min_font_pt=12.0)
    assert fit.adjusted is True
    assert fit.font_size_pt < 22.0 and fit.font_size_pt >= 12.0
    assert not fit.still_overflows
    assert "font reduced" in fit.note or "split clauses" in fit.note
    # Verify all characters are preserved without truncation
    assert "人为排热" in fit.text and "Urban Heat Island" in fit.text


def test_geometry_validation_catches_bounds_font_floor_and_overlap():
    bad_bounds = PageDesignEntry(
        page_id="S001",
        elements=[
            {"element_id": "t1", "kind": "title", "x_in": 0.5, "y_in": 0.5, "w_in": 4.0, "h_in": 1.0, "text": "Hi", "font_size_pt": 24},
            {"element_id": "b1", "kind": "bullet", "x_in": 0.5, "y_in": 2.0, "w_in": 4.0, "h_in": 2.0, "text": "A", "font_size_pt": 10},  # < 12pt floor
            {"element_id": "b2", "kind": "body", "x_in": 2.0, "y_in": 2.2, "w_in": 4.0, "h_in": 2.0, "text": "B", "font_size_pt": 14},    # overlaps b1!
        ],
    )
    probs = validate_page_geometry(bad_bounds)
    assert any("floor" in p for p in probs)
    assert any("overlap" in p for p in probs)


def test_stage_f_adapts_aspect_ratio_places_placeholders_and_writes_schematic_preview(tmp_path):
    pp = ProductionPlan(run_id=RID, source_utterance="帮我做一个城市热岛效应的 PPT。", **GOOD_A)
    sp = SlidePlan(
        run_id=RID,
        status="provisional",
        narrative=GOOD_B["narrative"],
        pages=[{"page_id": f"S{i:03d}", "status": "provisional", **p} for i, p in enumerate(GOOD_B["pages"], 1)],
    )
    (tmp_path / "assets").mkdir()
    portrait = _png(600, 1200)  # tall 1:2 portrait image
    (tmp_path / "assets" / "A001.png").write_bytes(portrait)
    sha = hashlib.sha256(portrait).hexdigest()
    reg = AssetRegistry(
        run_id=RID,
        assets=[
            {
                "asset_id": "A001",
                "status": "acquired",
                "type": "image",
                "file_path": "assets/A001.png",
                "source_url": "https://commons.wikimedia.org/wiki/File:Tall.png",
                "source_name": "Wikimedia Commons",
                "license_or_terms": "CC BY 4.0",
                "author": "Tester",
                "attribution": "Tester, CC BY 4.0, via Wikimedia Commons (https://commons.wikimedia.org/wiki/File:Tall.png)",
                "identifier": "File:Tall.png",
                "sha256": sha,
                "mime": "image/png",
                "width": 600,
                "height": 1200,
                "retrieved_at": "2026-01-01T00:00:00+08:00",
                "candidates": [{"title": "File:Tall.png", "decision": "selected", "reason": "ok"}],
            },
            {"asset_id": "A002", "status": "missing", "type": "diagram", "notes": "missing diagram"},
        ],
    )
    uir = UserInputRequests(
        run_id=RID,
        requests=[{
            "request_id": "U001",
            "kind": "diagram",
            "asset_id": "A002",
            "page_ids": ["S002"],
            "need": "画出三类热岛成因机制图",
            "why": "D 未找到",
            "format_wanted": "JSON 或 PNG",
            "acceptance": ["来源明确"],
            "blocking": False,
            "fallback": "placeholder",
            "status": "open",
        }],
        summary="1 open (0 blocking)",
    )
    mapping = SlideAssetMapping(
        run_id=RID,
        mappings=[{"page_id": "S001", "asset_id": "A001", "usage": "置于 S001 右侧展示纵向热像分布", "accepted": True}],
        unused_assets=[],
    )

    design = run_stage_f(pp, sp, reg, uir, mapping, tmp_path)
    loaded = load_contract(PageDesign, tmp_path / "page_design.json")
    assert len(loaded.pages) == 2

    p1, p2 = design.pages
    assert p1.canvas_width_in == CANVAS_W_IN and p1.canvas_height_in == CANVAS_H_IN
    assert "aspect ratio 600x1200" in p1.notes
    img_el = next(e for e in p1.elements if e.kind is ElementKind.image)
    assert img_el.asset_id == "A001" and img_el.asset_path == "assets/A001.png"
    assert abs((img_el.w_in / img_el.h_in) - 0.5) < 0.05  # preserved 1:2 portrait ratio

    # S002 must contain PLACEHOLDER_U001
    ph_el = next(e for e in p2.elements if e.kind is ElementKind.placeholder)
    assert ph_el.element_id == "PLACEHOLDER_U001"
    assert "待补充：" in ph_el.text and "U001" in ph_el.text

    # Schematic preview exists and is marked as schematic
    assert p1.preview_path == "previews/S001_schematic.png"
    prev = tmp_path / p1.preview_path
    assert prev.is_file()
    with Image.open(prev) as im:
        assert im.size == (1333, 750)


def test_stage_f_enforces_user_concise_text_and_page_limit_constraints(tmp_path):
    pp = ProductionPlan(
        run_id=RID,
        source_utterance="做一个机器学习综述 PPT，不超过 2 页，少字，每页重点突出。",
        **{**GOOD_A, "constraints": ["不超过 2 页", "少字"]},
    )
    dense_b = json.loads(json.dumps(GOOD_B))
    dense_b["pages"][0]["key_points"] = [
        "深度学习在岩土工程中的参数反演与本构建模已经得到广泛探索，能够有效处理复杂非线性边界问题",
        "监测数据多源异构与传感器噪声会对预测稳定性产生显著干扰，需要结合物理先验约束",
        "小样本工况下的泛化能力不足与模型可解释性缺失仍是工程落地的核心瓶颈",
    ]
    sp = SlidePlan(
        run_id=RID,
        status="provisional",
        narrative=dense_b["narrative"],
        pages=[{"page_id": f"S{i:03d}", "status": "provisional", **p} for i, p in enumerate(dense_b["pages"], 1)],
    )
    reg = AssetRegistry(run_id=RID, assets=[])
    mapping = SlideAssetMapping(run_id=RID, mappings=[], unused_assets=[])
    design = run_stage_f(pp, sp, reg, None, mapping, tmp_path)
    assert len(design.pages) == 2
    p1 = design.pages[0]
    assert "condensed text" in p1.notes
    # No verbose summary box added when concise-text constraint is active
    assert not any(e.element_id == "summary" for e in p1.elements)

    # Page-limit violation fails cleanly
    pp_bad = ProductionPlan(
        run_id=RID,
        source_utterance="做一个 PPT，不超过 1 页",
        **{**GOOD_A, "constraints": ["不超过 1 页"]},
    )
    with pytest.raises(LayoutRuleError, match="page limit violated"):
        run_stage_f(pp_bad, sp, reg, None, mapping, tmp_path)


def test_runner_integrates_stage_f(tmp_path):
    report, run_dir = run_pipeline(
        "帮我做一个城市热岛效应的 PPT。",
        tmp_path,
        FakeClient([GOOD_A, GOOD_B, GOOD_C]),
        run_id="t-f",
    )
    st = {s.stage: s for s in report.stages}
    assert st["F"].status is StageStatus.done
    assert "page_design.json" in st["F"].output_files
    assert any(f.startswith("previews/") and f.endswith("_schematic.png") for f in st["F"].output_files)
    load_contract(PageDesign, run_dir / "page_design.json")
