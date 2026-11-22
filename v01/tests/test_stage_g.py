"""Stage G (python-pptx native deck generation) + hard gate unit tests."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest
from PIL import Image
from pptx import Presentation
from pptx.enum.dml import MSO_LINE_DASH_STYLE
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.util import Inches

from pptv01.contracts import (
    AssetRegistry,
    PageDesign,
    ProductionPlan,
    SlideAssetMapping,
    SlidePlan,
    StageStatus,
    UserInputRequests,
    UserRequestStatus,
    load_contract,
    write_contract,
)
from pptv01.f_layout import run_stage_f
from pptv01.g_pptx import AttributionMissingError, run_stage_g
from pptv01.grading.gates import check_gates
from pptv01.runner import run_pipeline
from tests.conftest import GOOD_A, GOOD_B, GOOD_C, GOOD_TH
from tests.fakes import FakeClient

RID = "20260101-000000-abcd"


def _png(w=800, h=600) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (w, h), (35, 110, 175)).save(b, "PNG")
    return b.getvalue()


def test_stage_g_builds_native_pptx_with_images_charts_tables_placeholders_and_notes(tmp_path):
    pp = ProductionPlan(run_id=RID, source_utterance="帮我做一个城市热岛效应的 PPT。", **GOOD_A)
    sp = SlidePlan(
        run_id=RID,
        status="provisional",
        narrative=GOOD_B["narrative"],
        pages=[{"page_id": f"S{i:03d}", "status": "provisional", **p} for i, p in enumerate(GOOD_B["pages"], 1)],
    )
    write_contract(pp, tmp_path / "production_plan.json")
    write_contract(sp, tmp_path / "slide_plan.json")

    (tmp_path / "assets").mkdir()
    img_bytes = _png()
    (tmp_path / "assets" / "A001.png").write_bytes(img_bytes)
    sha = hashlib.sha256(img_bytes).hexdigest()

    (tmp_path / "inputs").mkdir()
    chart_json = {
        "title": "城乡温差对比",
        "labels": ["城区", "郊区"],
        "values": [31.5, 27.2],
        "source": "NOAA Urban Station Dataset 2024",
    }
    chart_bytes = json.dumps(chart_json, ensure_ascii=False).encode("utf-8")
    (tmp_path / "inputs" / "U001.json").write_bytes(chart_bytes)
    chart_sha = hashlib.sha256(chart_bytes).hexdigest()

    reg = AssetRegistry(
        run_id=RID,
        assets=[
            {
                "asset_id": "A001",
                "status": "acquired",
                "type": "image",
                "file_path": "assets/A001.png",
                "source_url": "https://commons.wikimedia.org/wiki/File:UHI.png",
                "source_name": "Wikimedia Commons",
                "license_or_terms": "CC BY 2.0",
                "author": "Alice Chen",
                "attribution": "Alice Chen, CC BY 2.0, via Wikimedia Commons (https://commons.wikimedia.org/wiki/File:UHI.png)",
                "identifier": "File:UHI.png",
                "sha256": sha,
                "mime": "image/png",
                "width": 800,
                "height": 600,
                "retrieved_at": "2026-01-01T00:00:00+08:00",
                "candidates": [{"title": "File:UHI.png", "decision": "selected", "reason": "ok"}],
            },
            {"asset_id": "A002", "status": "missing", "type": "chart_data", "notes": "no data in D"},
            {"asset_id": "A003", "status": "missing", "type": "diagram", "notes": "missing diagram"},
        ],
    )
    write_contract(reg, tmp_path / "asset_registry.json")

    uir = UserInputRequests(
        run_id=RID,
        requests=[
            {
                "request_id": "U001",
                "kind": "chart",
                "asset_id": "A002",
                "page_ids": ["S002"],
                "need": "城郊温度柱状对比数据",
                "why": "D 无结构化数据源",
                "format_wanted": "JSON",
                "acceptance": ["含来源"],
                "blocking": True,
                "fallback": "placeholder",
                "status": "provided",
                "provided": {
                    "file": "inputs/U001.json",
                    "sha256": chart_sha,
                    "source": "NOAA Urban Station Dataset 2024",
                    "license": "CC0",
                },
            },
            {
                "request_id": "U002",
                "kind": "diagram",
                "asset_id": "A003",
                "page_ids": ["S002"],
                "need": "热岛成因机制流程图",
                "why": "D 未找到",
                "format_wanted": "JSON 或 PNG",
                "acceptance": ["来源明确"],
                "blocking": False,
                "fallback": "placeholder",
                "status": "open",
            },
        ],
        summary="1 open (0 blocking), 1 provided",
    )
    write_contract(uir, tmp_path / "user_input_requests.json")

    mapping = SlideAssetMapping(
        run_id=RID,
        mappings=[
            {"page_id": "S001", "asset_id": "A001", "usage": "置于 S001 展示热像分布图", "accepted": True},
            {"page_id": "S002", "asset_id": "A002", "request_id": "U001", "usage": "绘制 S002 城郊温差柱状图", "accepted": True},
        ],
        unused_assets=[],
    )

    design = run_stage_f(pp, sp, reg, uir, mapping, tmp_path)
    deck_path = run_stage_g(slide_plan=sp, design=design, reg=reg, uir=uir, run_dir=tmp_path)
    assert deck_path.is_file()

    prs = Presentation(str(deck_path))
    assert len(prs.slides) == 2

    # Slide 1 has native textboxes + bound picture + CC BY attribution in notes
    s1 = prs.slides[0]
    assert any(sh.has_text_frame and "什么是城市热岛" in sh.text_frame.text for sh in s1.shapes)
    pics = [sh for sh in s1.shapes if sh.shape_type is MSO_SHAPE_TYPE.PICTURE]
    assert len(pics) == 1 and pics[0].name.startswith("ASSET_A001__")
    s1_notes = s1.notes_slide.notes_text_frame.text
    assert "Alice Chen" in s1_notes and "CC BY 2.0" in s1_notes

    # Slide 2 has native chart bound to U001 + dashed PLACEHOLDER_U002 + notes
    s2 = prs.slides[1]
    assert any(sh.has_chart and "CHART_INPUT_U001" in sh.name for sh in s2.shapes)
    ph = next(sh for sh in s2.shapes if sh.name == "PLACEHOLDER_U002")
    assert ph.line.dash_style is MSO_LINE_DASH_STYLE.DASH
    assert "待补充：" in ph.text_frame.text and "U002" in ph.text_frame.text
    assert "U002" in s2.notes_slide.notes_text_frame.text

    # user_input_requests updated to placeholder_rendered for U002
    uir_after = load_contract(UserInputRequests, tmp_path / "user_input_requests.json")
    assert uir_after.requests[1].status is UserRequestStatus.placeholder_rendered

    # Hard gates pass on this generated deck!
    gates = check_gates(tmp_path)
    by = {g.gate: g for g in gates.results}
    assert by["pptx_opens"].status == "pass"
    assert by["native_content_not_screenshots"].status == "pass"
    assert by["no_fabricated_visuals"].status == "pass"
    assert by["placeholders_marked"].status == "pass"
    assert by["attribution_present"].status == "pass"


def test_stage_g_and_gates_fail_when_cc_by_attribution_missing(tmp_path):
    pp = ProductionPlan(run_id=RID, source_utterance="帮我做一个城市热岛效应的 PPT。", **GOOD_A)
    sp = SlidePlan(
        run_id=RID,
        status="provisional",
        narrative=GOOD_B["narrative"],
        pages=[{"page_id": "S001", "status": "provisional", **GOOD_B["pages"][0]}],
    )
    (tmp_path / "assets").mkdir()
    img = _png()
    (tmp_path / "assets" / "A001.png").write_bytes(img)
    sha = hashlib.sha256(img).hexdigest()
    # Bypass AssetRecord validator using model_construct to simulate missing attribution on a CC BY asset
    bad_record = AssetRegistry.model_fields["assets"]  # noqa: F841
    from pptv01.contracts import AssetRecord
    rec = AssetRecord.model_construct(
        asset_id="A001",
        status="acquired",
        type="image",
        file_path="assets/A001.png",
        source_url="https://commons.wikimedia.org/wiki/File:X.png",
        source_name="Wikimedia Commons",
        license_or_terms="CC BY 4.0",
        author=None,
        attribution=None,
        sha256=sha,
        mime="image/png",
        width=800,
        height=600,
    )
    reg = AssetRegistry.model_construct(schema_version="0.1", run_id=RID, assets=[rec], sources=[], summary="")
    design = PageDesign(
        run_id=RID,
        pages=[{
            "page_id": "S001",
            "elements": [
                {"element_id": "title", "kind": "title", "x_in": 0.5, "y_in": 0.4, "w_in": 12.0, "h_in": 0.8, "text": "Title", "font_size_pt": 28},
                {"element_id": "img1", "kind": "image", "x_in": 1.0, "y_in": 1.5, "w_in": 5.0, "h_in": 4.0, "asset_id": "A001", "asset_path": "assets/A001.png"},
            ],
        }],
    )
    with pytest.raises(AttributionMissingError):
        run_stage_g(sp, design, reg, None, tmp_path)


def test_native_content_not_screenshots_gate_rejects_full_slide_screenshot(tmp_path):
    write_contract(ProductionPlan(run_id=RID, source_utterance="test", **GOOD_A), tmp_path / "production_plan.json")
    write_contract(
        SlidePlan(run_id=RID, status="provisional", narrative="n", pages=[{"page_id": "S001", "status": "provisional", **GOOD_B["pages"][0]}]),
        tmp_path / "slide_plan.json",
    )
    shot = tmp_path / "shot.png"
    shot.write_bytes(_png(1333, 750))
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    s = prs.slides.add_slide(prs.slide_layouts[6])
    s.shapes.add_picture(str(shot), Inches(0), Inches(0), Inches(13.333), Inches(7.5))
    prs.save(str(tmp_path / "deck.pptx"))

    rep = check_gates(tmp_path)
    by = {r.gate: r for r in rep.results}
    assert by["pptx_opens"].status == "pass"
    assert by["native_content_not_screenshots"].status == "fail"


def test_runner_integrates_stage_g_and_marks_placeholders(tmp_path):
    report, run_dir = run_pipeline(
        "帮我做一个城市热岛效应的 PPT。",
        tmp_path,
        FakeClient([GOOD_A, GOOD_B, GOOD_C, GOOD_TH]),
        run_id="t-g",
    )
    st = {s.stage: s for s in report.stages}
    assert st["G"].status is StageStatus.done and "deck.pptx" in st["G"].output_files
    assert (run_dir / "deck.pptx").is_file()
    assert report.user_input_requests is not None
    assert report.user_input_requests.placeholder_count >= 1
    assert report.user_input_requests.open_count == 0
