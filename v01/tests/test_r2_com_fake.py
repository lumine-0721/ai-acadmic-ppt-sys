"""Linux + Windows unit tests for PowerPointComRenderer using tests/fake_com.py."""

from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

from pptv01.contracts import (
    AssetRecord,
    AssetRegistry,
    AssetStatus,
    AssetType,
    DesignElement,
    ElementKind,
    PageDesign,
    PageDesignEntry,
    SlidePlan,
    UserInputRequest,
    UserInputRequests,
    UserRequestFallback,
    UserRequestKind,
    UserRequestStatus,
)
from pptv01.g_build import compile_deck_ops
from pptv01.renderers import DeckDump, PowerPointComRenderer, RendererUnavailableError
from tests.fake_com import FakeExcelApp, FakePowerPointApp

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def _png_bytes(w=200, h=100) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (w, h), (30, 100, 180)).save(b, "PNG")
    return b.getvalue()


def _make_full_ops(tmp_path: Path):
    (tmp_path / "assets").mkdir(exist_ok=True)
    (tmp_path / "inputs").mkdir(exist_ok=True)
    img_bytes = _png_bytes(240, 120)
    (tmp_path / "assets" / "A001.png").write_bytes(img_bytes)

    chart_bytes = json.dumps(
        {"title": "温差", "labels": ["城", "郊"], "values": [3.2, 0.5], "source": "NOAA 2024"},
        ensure_ascii=False,
    ).encode("utf-8")
    (tmp_path / "inputs" / "U002.json").write_bytes(chart_bytes)

    table_bytes = json.dumps(
        {"columns": ["区域", "温度"], "rows": [["城区", "28°C"]], "source": "CMA 2024"},
        ensure_ascii=False,
    ).encode("utf-8")
    (tmp_path / "inputs" / "U003.json").write_bytes(table_bytes)

    diag_bytes = json.dumps(
        {
            "nodes": [{"id": f"n{i}", "label": f"节点{i}", "source": "R001"} for i in range(1, 6)],
            "edges": [{"from": f"n{i}", "to": f"n{i+1}", "label": f"步{i}", "source": "R001"} for i in range(1, 5)],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    (tmp_path / "inputs" / "U004.json").write_bytes(diag_bytes)

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
                provided={"file": "inputs/U002.json", "sha256": hashlib.sha256(chart_bytes).hexdigest(), "source": "NOAA 2024", "license": "CC0"},
            ),
            UserInputRequest(
                request_id="U003", kind=UserRequestKind.table, page_ids=["S002"],
                need="温度表", why="D 未找到", format_wanted="JSON", acceptance=["来源明确"],
                blocking=True, fallback=UserRequestFallback.placeholder, status=UserRequestStatus.provided,
                provided={"file": "inputs/U003.json", "sha256": hashlib.sha256(table_bytes).hexdigest(), "source": "CMA 2024", "license": "CC BY 4.0"},
            ),
            UserInputRequest(
                request_id="U004", kind=UserRequestKind.diagram, page_ids=["S002"],
                need="5 节点流程图", why="D 未找到", format_wanted="JSON", acceptance=["来源明确"],
                blocking=True, fallback=UserRequestFallback.placeholder, status=UserRequestStatus.provided,
                provided={"file": "inputs/U004.json", "sha256": hashlib.sha256(diag_bytes).hexdigest(), "source": "Oke 1982", "license": "CC BY 4.0"},
            ),
        ],
        summary="1 open, 3 provided",
    )
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
    design = PageDesign(
        run_id="fake-com-run",
        pages=[
            PageDesignEntry(
                page_id="S001",
                elements=[
                    DesignElement(element_id="title", kind=ElementKind.title, x_in=0.6, y_in=0.2, w_in=12.0, h_in=0.8, text="第一页：图与表", font_size_pt=28.0, bold=True),
                    DesignElement(element_id="bullets", kind=ElementKind.bullet, x_in=0.6, y_in=1.5, w_in=5.0, h_in=4.0, text="• 要点A", font_size_pt=18.0),
                    DesignElement(element_id="v_img", kind=ElementKind.image, x_in=6.0, y_in=1.5, w_in=3.0, h_in=2.5, asset_id="A001", asset_path="assets/A001.png"),
                    DesignElement(element_id="v_chart", kind=ElementKind.chart, x_in=6.0, y_in=4.2, w_in=6.5, h_in=2.5, request_id="U002", asset_path="inputs/U002.json"),
                    DesignElement(element_id="PLACEHOLDER_U001", kind=ElementKind.placeholder, x_in=9.3, y_in=1.5, w_in=3.2, h_in=2.5, request_id="U001", text="待补充：热岛图标（U001）", font_size_pt=12.0),
                ],
            ),
            PageDesignEntry(
                page_id="S002",
                elements=[
                    DesignElement(element_id="title", kind=ElementKind.title, x_in=0.6, y_in=0.2, w_in=12.0, h_in=0.8, text="第二页：表与流程", font_size_pt=28.0, bold=True),
                    DesignElement(element_id="v_tbl", kind=ElementKind.table, x_in=0.6, y_in=1.5, w_in=5.5, h_in=3.5, request_id="U003", asset_path="inputs/U003.json"),
                    DesignElement(element_id="v_diag", kind=ElementKind.shape, x_in=6.4, y_in=1.5, w_in=6.2, h_in=4.5, role="diagram_json", request_id="U004", asset_path="inputs/U004.json"),
                ],
            ),
        ],
    )
    return compile_deck_ops(sp, design, reg, uir, tmp_path)


def test_fake_com_build_export_measure_and_excel_cleanup(tmp_path):
    ops = _make_full_ops(tmp_path)
    fake_app = FakePowerPointApp()
    fake_xl = FakeExcelApp()
    renderer = PowerPointComRenderer(app_factory=lambda: fake_app, excel_factory=lambda: fake_xl)

    info = renderer.preflight()
    assert info.available and info.version == "16.0" and info.excel_available
    assert fake_xl.quit_called

    res = renderer.build(ops, tmp_path)
    assert res.slide_count == 2
    assert res.pptx_path.is_file() and res.dump_path.is_file()

    # Verify Excel workbook was explicitly closed with save=False
    saved_pres = fake_app._saved_presentations[str(res.pptx_path.resolve())]
    chart_shapes = [s for s in saved_pres.Slides(1).Shapes if s.HasChart == -1]
    assert len(chart_shapes) == 1
    assert chart_shapes[0].Chart.ChartData.Workbook.closed is True
    assert chart_shapes[0].Chart.ChartData.Workbook.close_save_arg is False
    assert chart_shapes[0].Chart.source_range == "Sheet1!$A$1:$B$3"

    # Verify deck_dump.json contents
    dump = DeckDump.model_validate_json(res.dump_path.read_text(encoding="utf-8"))
    assert len(dump.slides) == 2
    s1_names = {s.name for s in dump.slides[0].shapes}
    assert "ASSET_A001__v_img" in s1_names
    assert "CHART_INPUT_U002__v_chart" in s1_names
    assert "PLACEHOLDER_U001" in s1_names
    assert any(s.is_placeholder and s.name == "title" for s in dump.slides[0].shapes)

    s2_shapes = dump.slides[1].shapes
    diag_nodes = [s for s in s2_shapes if s.name.startswith("DIAGRAM_INPUT_U004__v_diag_node_")]
    diag_edges = [s for s in s2_shapes if s.is_connector and s.name.startswith("DIAGRAM_INPUT_U004__v_diag_edge_")]
    assert len(diag_nodes) == 5
    assert len(diag_edges) == 4
    assert all(e.begin_connected and e.end_connected for e in diag_edges)

    # Export PNGs + PDF
    exp = renderer.export(res.pptx_path, tmp_path / "render")
    assert [p.name for p in exp.png_paths] == ["slide_001.png", "slide_002.png"]
    assert exp.pdf_path is not None and exp.pdf_path.is_file()

    # Measure live text bounds
    meas = renderer.measure(res.pptx_path, page_ids=ops.page_ids)
    assert len(meas.measurements) >= 3
    assert "Microsoft YaHei" in meas.presentation_fonts


def test_fake_com_never_touches_or_closes_pre_existing_user_presentations(tmp_path):
    ops = _make_full_ops(tmp_path)
    fake_app = FakePowerPointApp(pre_existing_user_docs=2)
    renderer = PowerPointComRenderer(app_factory=lambda: fake_app, excel_factory=FakeExcelApp)

    res = renderer.build(ops, tmp_path)
    assert res.slide_count == 2
    # Both pre-existing user presentations remain open & untouched, and Quit() was NOT called!
    assert len(fake_app.user_presentations) == 2
    assert all(not p.closed for p in fake_app.user_presentations)
    assert fake_app.Presentations.Count == 2
    assert fake_app.quit_called is False


def test_preflight_raises_renderer_unavailable_when_com_missing():
    def _boom():
        raise RuntimeError("COM server unavailable")

    renderer = PowerPointComRenderer(app_factory=_boom)
    with pytest.raises(RendererUnavailableError, match="PowerPoint COM"):
        renderer.preflight()


def test_fake_com_keeps_bg_canvas_behind_secondary_bg_bands(tmp_path):
    """bg_canvas must stay behind z=0 panels (bg_bleed_band, bg_card_0) which stay behind title."""
    sp = SlidePlan(
        run_id="zorder-test",
        status="provisional",
        narrative="ZOrder test.",
        pages=[
            {
                "page_id": "S001",
                "title": "全幅与卡片层叠测试",
                "goal": "测试背景与卡片Z序",
                "key_points": ["要点A", "要点B"],
                "content_summary": "不渲染",
                "visual_intent": "卡片",
                "asset_needs": [],
                "status": "provisional",
            }
        ],
    )
    design = PageDesign(
        run_id="zorder-test",
        theme_id="architectural-stone",
        pages=[
            PageDesignEntry(
                page_id="S001",
                layout_id="full-bleed",
                layout_name="full-bleed",
                elements=[
                    DesignElement(element_id="bg_canvas", kind=ElementKind.shape, x_in=0.0, y_in=0.0, w_in=13.333, h_in=7.5, z_index=0, fill_hex="#F7F5F0", line_hex="#F7F5F0", role="canvas_bg"),
                    DesignElement(element_id="bg_bleed_band", kind=ElementKind.shape, x_in=0.0, y_in=4.85, w_in=13.333, h_in=2.65, z_index=0, fill_hex="#1C1917", line_hex="#1C1917", role="bleed_editorial_band"),
                    DesignElement(element_id="bg_card_0", kind=ElementKind.shape, x_in=0.65, y_in=1.60, w_in=3.80, h_in=1.30, z_index=0, fill_hex="#FFFFFF", line_hex="#CFC8BA", role="card_panel_0"),
                    DesignElement(element_id="title", kind=ElementKind.title, x_in=0.65, y_in=5.05, w_in=5.40, h_in=0.90, z_index=2, text="全幅与卡片层叠测试", font="SimSun", font_size_pt=28.0, bold=True, color_hex="#FAF8F5"),
                ],
            )
        ],
    )
    reg = AssetRegistry(run_id="zorder-test", assets=[], sources=[], summary="0/0")
    uir = UserInputRequests(run_id="zorder-test", requests=[], summary="0 open")
    ops = compile_deck_ops(sp, design, reg, uir, tmp_path, write_file=True)

    fake_app = FakePowerPointApp()
    renderer = PowerPointComRenderer(app_factory=lambda: fake_app, excel_factory=FakeExcelApp)
    res = renderer.build(ops, tmp_path)
    dump = DeckDump.model_validate_json(res.dump_path.read_text(encoding="utf-8"))
    names = [s.name for s in dump.slides[0].shapes]
    assert names.index("bg_canvas") < names.index("bg_bleed_band"), names
    assert names.index("bg_canvas") < names.index("bg_card_0"), names
    assert names.index("bg_bleed_band") < names.index("title"), names
    assert names.index("bg_card_0") < names.index("title"), names
