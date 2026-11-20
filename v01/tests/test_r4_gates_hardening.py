"""Tests for R4 (B4 fix): sha256 image blob verification, shape-name whitelist blocking hand-drawn
diagrams, placeholder missing notes, attribution spoofing, and real PowerPoint COM deck gate pass."""

from __future__ import annotations

import hashlib
import io
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.util import Inches

from pptv01.contracts import (
    AssetRecord,
    AssetRegistry,
    AssetStatus,
    AssetType,
    ProductionPlan,
    SlidePlan,
    UserInputRequest,
    UserInputRequests,
    UserRequestFallback,
    UserRequestKind,
    UserRequestStatus,
    write_contract,
)
from pptv01.grading.gates import check_gates
from pptv01.h_qa.inspect import inspect_deck
from pptv01.renderers import PowerPointComRenderer, RendererUnavailableError
from tests.conftest import GOOD_A, GOOD_B
from tests.test_r2_com_fake import _make_full_ops

RID = "r4-gates-test"
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def _png_bytes(rgb: tuple[int, int, int], w=120, h=80) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), rgb).save(buf, "PNG")
    return buf.getvalue()


def _setup_run(tmp_path: Path):
    (tmp_path / "assets").mkdir()
    img1 = _png_bytes((20, 80, 180))
    img2 = _png_bytes((180, 40, 40))
    (tmp_path / "assets" / "A001.png").write_bytes(img1)
    (tmp_path / "assets" / "A002.png").write_bytes(img2)

    write_contract(ProductionPlan(run_id=RID, source_utterance="城市热岛效应", **GOOD_A), tmp_path / "production_plan.json")
    sp = SlidePlan(
        run_id=RID,
        status="provisional",
        narrative=GOOD_B["narrative"],
        pages=[{"page_id": "S001", "status": "provisional", **GOOD_B["pages"][0]}],
    )
    write_contract(sp, tmp_path / "slide_plan.json")

    reg = AssetRegistry(
        run_id=RID,
        assets=[
            AssetRecord(
                asset_id="A001",
                status=AssetStatus.acquired,
                type=AssetType.image,
                file_path="assets/A001.png",
                source_url="https://commons.wikimedia.org/wiki/File:PD.png",
                source_name="Wikimedia Commons",
                license_or_terms="CC0",
                sha256=hashlib.sha256(img1).hexdigest(),
                mime="image/png",
                width=120,
                height=80,
                retrieved_at=NOW,
            ),
            AssetRecord(
                asset_id="A002",
                status=AssetStatus.acquired,
                type=AssetType.image,
                file_path="assets/A002.png",
                source_url="https://commons.wikimedia.org/wiki/File:CCBY.png",
                source_name="Wikimedia Commons",
                license_or_terms="CC BY 4.0",
                author="Secret Photographer",
                attribution="Secret Photographer, CC BY 4.0",
                sha256=hashlib.sha256(img2).hexdigest(),
                mime="image/png",
                width=120,
                height=80,
                retrieved_at=NOW,
            ),
        ],
        sources=[],
        summary="2/2",
    )
    write_contract(reg, tmp_path / "asset_registry.json")

    uir = UserInputRequests(
        run_id=RID,
        requests=[
            UserInputRequest(
                request_id="U001",
                kind=UserRequestKind.diagram,
                page_ids=["S001"],
                need="热岛示意图",
                why="D 未找到",
                format_wanted="JSON",
                acceptance=["来源明确"],
                blocking=True,
                fallback=UserRequestFallback.placeholder,
                status=UserRequestStatus.open,
            )
        ],
        summary="1 open (1 blocking)",
    )
    write_contract(uir, tmp_path / "user_input_requests.json")
    return sp, reg, uir


def test_b4_name_spoofing_fails_sha256_and_attribution_gates(tmp_path):
    sp, reg, uir = _setup_run(tmp_path)
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    sl = prs.slides.add_slide(prs.slide_layouts[6])
    tb = sl.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(8), Inches(0.8))
    tb.name = "title"
    tb.text_frame.text = "热岛"

    # Attack: embed A002's CC BY image bytes, but spoof its name as A001 (CC0) and omit attribution!
    pic = sl.shapes.add_picture(str(tmp_path / "assets" / "A002.png"), Inches(1), Inches(1.5), Inches(4), Inches(3))
    pic.name = "ASSET_A001__fake"

    ph = sl.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(6), Inches(1.5), Inches(4), Inches(2))
    ph.name = "PLACEHOLDER_U001"
    ph.text_frame.text = "待补充：热岛示意图（U001）"
    sl.notes_slide.notes_text_frame.text = "Page S001\n待补充 U001"
    deck = tmp_path / "deck.pptx"
    prs.save(str(deck))

    rep = check_gates(tmp_path)
    by = {r.gate: r for r in rep.results}
    assert by["no_fabricated_visuals"].status == "fail"
    assert "sha256 mismatch" in by["no_fabricated_visuals"].detail
    # Because attribution_present also resolves by blob sha256, it catches A002's missing credit too!
    assert by["attribution_present"].status == "fail"
    assert "A002" in by["attribution_present"].detail

    # Stage H inspect_deck also catches the sha256 mismatch
    qa_issues = inspect_deck(deck, sp, reg=reg, uir=uir)
    assert any("sha256 mismatch" in i.description for i in qa_issues)


def test_b4_hand_drawn_autoshape_and_connector_diagram_fails_whitelist(tmp_path):
    _setup_run(tmp_path)
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    sl = prs.slides.add_slide(prs.slide_layouts[6])
    tb = sl.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(8), Inches(0.8))
    tb.name = "title"
    tb.text_frame.text = "热岛"

    # Attack: hand-draw an unbound diagram using plain AutoShapes and a Connector
    r1 = sl.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(1), Inches(2), Inches(2), Inches(1))
    r1.name = "Rectangle 1"
    r1.text_frame.text = "步骤一"
    r2 = sl.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(4), Inches(2), Inches(2), Inches(1))
    r2.name = "Rectangle 2"
    r2.text_frame.text = "步骤二"
    sl.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(3), Inches(2.5), Inches(4), Inches(2.5))

    ph = sl.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(7), Inches(1.5), Inches(4), Inches(2))
    ph.name = "PLACEHOLDER_U001"
    ph.text_frame.text = "待补充：热岛示意图（U001）"
    sl.notes_slide.notes_text_frame.text = "U001"
    prs.save(str(tmp_path / "deck.pptx"))

    rep = check_gates(tmp_path)
    by = {r.gate: r for r in rep.results}
    assert by["no_fabricated_visuals"].status == "fail"
    assert "unknown element" in by["no_fabricated_visuals"].detail


def test_b4_placeholder_missing_notes_and_missing_attribution_fail(tmp_path):
    _setup_run(tmp_path)
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    sl = prs.slides.add_slide(prs.slide_layouts[6])
    tb = sl.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(8), Inches(0.8))
    tb.name = "title"
    tb.text_frame.text = "热岛"
    pic = sl.shapes.add_picture(str(tmp_path / "assets" / "A002.png"), Inches(1), Inches(1.5), Inches(4), Inches(3))
    pic.name = "ASSET_A002__photo"
    ph = sl.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(6), Inches(1.5), Inches(4), Inches(2))
    ph.name = "PLACEHOLDER_U001"
    ph.text_frame.text = "待补充：热岛示意图（U001）"
    # Deliberately omit both U001 AND A002 attribution from notes!
    sl.notes_slide.notes_text_frame.text = "empty notes"
    prs.save(str(tmp_path / "deck.pptx"))

    rep = check_gates(tmp_path)
    by = {r.gate: r for r in rep.results}
    assert by["no_fabricated_visuals"].status == "pass"  # genuine A002 sha256 matches
    assert by["placeholders_marked"].status == "fail"
    assert by["attribution_present"].status == "fail"


@pytest.mark.windows_com
def test_r4_hardened_gates_pass_on_real_powerpoint_com_deck(tmp_path):
    renderer = PowerPointComRenderer()
    renderer.preflight()

    ops = _make_full_ops(tmp_path)
    # Write contracts so check_gates validates everything end-to-end against real COM deck.pptx + deck_dump.json
    write_contract(ProductionPlan(run_id="fake-com-run", source_utterance="城市热岛效应", **GOOD_A), tmp_path / "production_plan.json")
    sp = SlidePlan.model_validate(
        {
            "run_id": "fake-com-run",
            "status": "provisional",
            "narrative": "测试",
            "pages": [
                {"page_id": "S001", "title": "第一页：图与表", "goal": "g", "key_points": ["要点A"], "content_summary": "仅备注", "visual_intent": "v", "asset_needs": [], "status": "provisional"},
                {"page_id": "S002", "title": "第二页：表与流程", "goal": "g", "key_points": ["要点B"], "content_summary": "仅备注2", "visual_intent": "v", "asset_needs": [], "status": "provisional"},
            ],
        }
    )
    write_contract(sp, tmp_path / "slide_plan.json")
    img_bytes = (tmp_path / "assets" / "A001.png").read_bytes()
    reg = AssetRegistry(
        run_id="fake-com-run",
        assets=[
            AssetRecord(
                asset_id="A001",
                status=AssetStatus.acquired,
                type=AssetType.image,
                file_path="assets/A001.png",
                source_url="https://commons.wikimedia.org/wiki/File:Test.png",
                source_name="Wikimedia Commons",
                license_or_terms="CC BY 4.0",
                author="Tester",
                attribution="Tester, CC BY 4.0",
                sha256=hashlib.sha256(img_bytes).hexdigest(),
                mime="image/png",
                width=240,
                height=120,
                retrieved_at=NOW,
            )
        ],
        sources=[],
        summary="1/1",
    )
    write_contract(reg, tmp_path / "asset_registry.json")
    uir = UserInputRequests(
        run_id="fake-com-run",
        requests=[
            UserInputRequest(
                request_id="U001", kind=UserRequestKind.icon, page_ids=["S001"],
                need="热岛图标", why="D 未找到", format_wanted="PNG", acceptance=["许可明确"],
                blocking=False, fallback=UserRequestFallback.placeholder, status=UserRequestStatus.open,
            ),
            UserInputRequest(
                request_id="U002", kind=UserRequestKind.chart, page_ids=["S001"],
                need="温差图", why="D 未找到", format_wanted="JSON", acceptance=["来源明确"],
                blocking=True, fallback=UserRequestFallback.placeholder, status=UserRequestStatus.provided,
                provided={"file": "inputs/U002.json", "sha256": hashlib.sha256((tmp_path / "inputs" / "U002.json").read_bytes()).hexdigest(), "source": "NOAA 2024", "license": "CC0"},
            ),
            UserInputRequest(
                request_id="U003", kind=UserRequestKind.table, page_ids=["S002"],
                need="温度表", why="D 未找到", format_wanted="JSON", acceptance=["来源明确"],
                blocking=True, fallback=UserRequestFallback.placeholder, status=UserRequestStatus.provided,
                provided={"file": "inputs/U003.json", "sha256": hashlib.sha256((tmp_path / "inputs" / "U003.json").read_bytes()).hexdigest(), "source": "CMA 2024", "license": "CC BY 4.0"},
            ),
            UserInputRequest(
                request_id="U004", kind=UserRequestKind.diagram, page_ids=["S002"],
                need="5 节点流程图", why="D 未找到", format_wanted="JSON", acceptance=["来源明确"],
                blocking=True, fallback=UserRequestFallback.placeholder, status=UserRequestStatus.provided,
                provided={"file": "inputs/U004.json", "sha256": hashlib.sha256((tmp_path / "inputs" / "U004.json").read_bytes()).hexdigest(), "source": "Oke 1982", "license": "CC BY 4.0"},
            ),
        ],
        summary="1 open, 3 provided",
    )
    write_contract(uir, tmp_path / "user_input_requests.json")

    res = renderer.build(ops, tmp_path)
    assert res.pptx_path.is_file() and res.dump_path.is_file()

    rep = check_gates(tmp_path)
    by = {r.gate: r for r in rep.results}
    for gate_name in ("pptx_opens", "native_content_not_screenshots", "no_fabricated_visuals", "placeholders_marked", "attribution_present"):
        assert by[gate_name].status == "pass", f"{gate_name} failed: {by[gate_name].detail}"
