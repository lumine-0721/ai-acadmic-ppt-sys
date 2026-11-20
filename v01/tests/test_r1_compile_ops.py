"""Tests for R1: DeckOps contract, DeckRenderer protocol, compile_ops.py, and honest compile-only/unavailable runner."""

from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

from pptv01.contracts import (
    AssetRecord,
    AssetRegistry,
    AssetStatus,
    AssetType,
    DeckOp,
    DeckOpKind,
    DeckOps,
    DesignElement,
    ElementKind,
    PageDesign,
    PageDesignEntry,
    RunOverall,
    SlidePlan,
    StageStatus,
    UserInputRequest,
    UserInputRequests,
    UserRequestFallback,
    UserRequestKind,
    UserRequestStatus,
    load_contract,
)
from pptv01.g_build import (
    ARTIFACT_OPS,
    AttributionMissingError,
    DiagramOverflowError,
    UnboundVisualError,
    compile_deck_ops,
)
from pptv01.renderers import (
    BuildResult,
    ExportResult,
    MeasureResult,
    RendererInfo,
    RendererUnavailableError,
)
from pptv01.runner import run_pipeline
from tests.conftest import GOOD_A, GOOD_B, GOOD_C
from tests.fakes import FakeClient

U = "帮我做一个城市热岛效应的 PPT。"
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def _png_bytes(w=120, h=80) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (30, 100, 180)).save(buf, "PNG")
    return buf.getvalue()


def _make_slide_plan(page_ids=("S001",)) -> SlidePlan:
    return SlidePlan.model_validate(
        {
            "run_id": "r1-test",
            "status": "provisional",
            "narrative": "结构清晰。",
            "pages": [
                {
                    "page_id": pid,
                    "title": f"页面 {pid}",
                    "goal": "说明核心观点",
                    "key_points": ["要点一", "要点二"],
                    "content_summary": f"规划提示：{pid} 应突出核心数据",
                    "visual_intent": "图示",
                    "asset_needs": [],
                    "status": "provisional",
                }
                for pid in page_ids
            ],
        }
    )


# ------------------------------------------------------------------ 1. DeckOps contract validation
def test_deck_ops_contract_rejects_unbound_visuals_and_bad_paths(fx):
    d = fx("deck_ops")
    ops = DeckOps.model_validate(d)
    assert ops.page_ids == ["S001"]

    # Unbound add_picture -> rejected
    bad = json.loads(json.dumps(d))
    bad["ops"][3]["asset_id"] = None
    bad["ops"][3]["request_id"] = None
    with pytest.raises(ValidationError, match="must be bound"):
        DeckOps.model_validate(bad)

    # Visual op with empty source_refs -> rejected
    bad2 = json.loads(json.dumps(d))
    bad2["ops"][3]["source_refs"] = []
    with pytest.raises(ValidationError, match="source_refs"):
        DeckOps.model_validate(bad2)

    # Absolute file_path -> rejected
    bad3 = json.loads(json.dumps(d))
    bad3["ops"][3]["params"]["file_path"] = "/tmp/evil.png"
    with pytest.raises(ValidationError):
        DeckOps.model_validate(bad3)

    # Unbound connector -> rejected
    with pytest.raises(ValidationError, match="must be bound"):
        DeckOp(
            op_id="OP0099",
            page_id="S001",
            kind=DeckOpKind.add_connector,
            params={"from_element_id": "a", "to_element_id": "b"},
        )

    # Placeholder missing U### in text -> rejected
    with pytest.raises(ValidationError, match="待补充"):
        DeckOp(
            op_id="OP0098",
            page_id="S001",
            kind=DeckOpKind.add_placeholder_box,
            element_id="PLACEHOLDER_U001",
            request_id="U001",
            params={"shape_name": "PLACEHOLDER_U001", "text": "missing stuff"},
        )


# ------------------------------------------------------------------ 2. Golden deterministic compile_ops test
def test_compile_deck_ops_deterministic_golden_all_element_types(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "inputs").mkdir()

    img_bytes = _png_bytes()
    img_sha = hashlib.sha256(img_bytes).hexdigest()
    (tmp_path / "assets" / "A001.png").write_bytes(img_bytes)

    chart_data = json.dumps(
        {
            "title": "城郊温差 (°C)",
            "labels": ["春季", "夏季", "秋季"],
            "values": [1.8, 3.4, 2.1],
            "source": "NOAA Urban Climate Dataset 2024",
        },
        ensure_ascii=False,
    ).encode("utf-8")
    (tmp_path / "inputs" / "U001.json").write_bytes(chart_data)

    table_data = json.dumps(
        {
            "columns": ["指标", "城区", "郊区"],
            "rows": [["日均温", "28.5°C", "25.1°C"]],
            "source": "China Meteorological Yearbook 2024",
        },
        ensure_ascii=False,
    ).encode("utf-8")
    (tmp_path / "inputs" / "U002.json").write_bytes(table_data)

    reg = AssetRegistry(
        run_id="r1-test",
        assets=[
            AssetRecord(
                asset_id="A001",
                status=AssetStatus.acquired,
                type=AssetType.image,
                file_path="assets/A001.png",
                source_url="https://commons.wikimedia.org/wiki/File:UHI.png",
                source_name="Wikimedia Commons",
                license_or_terms="CC BY-SA 4.0",
                author="Jane Doe",
                attribution="Jane Doe, CC BY-SA 4.0, Wikimedia Commons",
                sha256=img_sha,
                mime="image/png",
                width=120,
                height=80,
                retrieved_at=NOW,
            )
        ],
        sources=[],
        summary="1/1 acquired",
    )
    uir = UserInputRequests(
        run_id="r1-test",
        requests=[
            UserInputRequest(
                request_id="U001",
                kind=UserRequestKind.chart,
                page_ids=["S001"],
                need="城郊温差柱状图",
                why="D 未找到现成图表数据",
                format_wanted="JSON",
                acceptance=["来源明确"],
                blocking=True,
                fallback=UserRequestFallback.placeholder,
                status=UserRequestStatus.provided,
                provided={
                    "file": "inputs/U001.json",
                    "sha256": hashlib.sha256(chart_data).hexdigest(),
                    "source": "NOAA Urban Climate Dataset 2024",
                    "license": "CC0",
                },
            ),
            UserInputRequest(
                request_id="U002",
                kind=UserRequestKind.table,
                page_ids=["S002"],
                need="城郊指标对比表",
                why="D 未找到现成表格",
                format_wanted="JSON",
                acceptance=["来源明确"],
                blocking=True,
                fallback=UserRequestFallback.placeholder,
                status=UserRequestStatus.provided,
                provided={
                    "file": "inputs/U002.json",
                    "sha256": hashlib.sha256(table_data).hexdigest(),
                    "source": "China Meteorological Yearbook 2024",
                    "license": "CC BY 4.0",
                },
            ),
            UserInputRequest(
                request_id="U003",
                kind=UserRequestKind.diagram,
                page_ids=["S002"],
                need="待补流程图",
                why="D 未找到可标来源的现成图；不允许凭空绘制",
                format_wanted="JSON",
                acceptance=["来源明确"],
                blocking=False,
                fallback=UserRequestFallback.placeholder,
                status=UserRequestStatus.open,
            ),
        ],
        summary="1 open (0 blocking), 2 provided",
    )
    sp = _make_slide_plan(("S001", "S002"))
    design = PageDesign(
        run_id="r1-test",
        pages=[
            PageDesignEntry(
                page_id="S001",
                layout_name="split_text_left",
                elements=[
                    DesignElement(element_id="title", kind=ElementKind.title, x_in=0.6, y_in=0.2, w_in=12.0, h_in=0.8, z_index=2, text="页面 S001", font_size_pt=28.0, bold=True),
                    DesignElement(element_id="bullets", kind=ElementKind.bullet, x_in=0.6, y_in=1.5, w_in=5.5, h_in=4.5, z_index=2, text="• 要点一\n• 要点二", font_size_pt=18.0),
                    DesignElement(element_id="vis_img", kind=ElementKind.image, x_in=6.5, y_in=1.5, w_in=3.0, h_in=2.5, z_index=2, asset_id="A001", asset_path="assets/A001.png"),
                    DesignElement(element_id="vis_chart", kind=ElementKind.chart, x_in=6.5, y_in=4.2, w_in=6.0, h_in=2.5, z_index=2, request_id="U001", asset_path="inputs/U001.json"),
                ],
                notes="S001 layout ok",
            ),
            PageDesignEntry(
                page_id="S002",
                layout_name="top_points_multi_visual",
                elements=[
                    DesignElement(element_id="title", kind=ElementKind.title, x_in=0.6, y_in=0.2, w_in=12.0, h_in=0.8, z_index=2, text="页面 S002", font_size_pt=28.0, bold=True),
                    DesignElement(element_id="vis_tbl", kind=ElementKind.table, x_in=0.6, y_in=1.5, w_in=6.0, h_in=3.5, z_index=2, request_id="U002", asset_path="inputs/U002.json"),
                    DesignElement(element_id="PLACEHOLDER_U003", kind=ElementKind.placeholder, x_in=7.0, y_in=1.5, w_in=5.5, h_in=3.5, z_index=2, request_id="U003", text="待补充：待补流程图（U003）", font_size_pt=13.0),
                ],
                notes="S002 layout ok",
            ),
        ],
    )

    ops1 = compile_deck_ops(sp, design, reg, uir, tmp_path)
    raw1 = (tmp_path / ARTIFACT_OPS).read_text(encoding="utf-8")
    ops2 = compile_deck_ops(sp, design, reg, uir, tmp_path)
    raw2 = (tmp_path / ARTIFACT_OPS).read_text(encoding="utf-8")

    # Byte-for-byte deterministic compilation
    assert raw1 == raw2
    assert ops1 == ops2
    loaded = load_contract(DeckOps, tmp_path / ARTIFACT_OPS)
    assert loaded.page_ids == ["S001", "S002"]

    # Golden sequence of op kinds
    s1_kinds = [o.kind.value for o in loaded.ops if o.page_id == "S001"]
    assert s1_kinds == [
        "add_slide",
        "add_textbox",          # bullets (alphabetical tie-break at z=2)
        "set_placeholder_text", # title
        "add_chart",            # vis_chart
        "set_alt_text",
        "add_picture",          # vis_img
        "set_alt_text",
        "set_notes",
    ]
    s2_kinds = [o.kind.value for o in loaded.ops if o.page_id == "S002"]
    assert s2_kinds == [
        "add_slide",
        "add_placeholder_box",  # PLACEHOLDER_U003
        "set_placeholder_text", # title
        "add_table",            # vis_tbl
        "set_alt_text",
        "set_notes",
    ]
    pic_op = next(o for o in loaded.ops if o.kind is DeckOpKind.add_picture)
    assert pic_op.params["crop_mode"] == "crop_fill"
    assert pic_op.params["shape_name"] == "ASSET_A001__vis_img"
    notes_s1 = next(o for o in loaded.ops if o.page_id == "S001" and o.kind is DeckOpKind.set_notes)
    assert "Speaker/planning summary:" in notes_s1.params["text"]
    assert "Jane Doe" in notes_s1.params["text"]


# ------------------------------------------------------------------ 3. Unbound visual & missing attribution rejection
def test_compile_ops_rejects_unbound_visual_and_missing_attribution(tmp_path):
    (tmp_path / "assets").mkdir()
    img_bytes = _png_bytes()
    (tmp_path / "assets" / "A001.png").write_bytes(img_bytes)

    sp = _make_slide_plan(("S001",))
    # Case A: image points to A001 which is missing in registry
    reg_missing = AssetRegistry(
        run_id="r1-test",
        assets=[AssetRecord(asset_id="A001", status=AssetStatus.missing, type=AssetType.image, notes="not found")],
        sources=[],
        summary="0/1",
    )
    design = PageDesign(
        run_id="r1-test",
        pages=[
            PageDesignEntry(
                page_id="S001",
                elements=[
                    DesignElement(element_id="title", kind=ElementKind.title, x_in=0.6, y_in=0.2, w_in=12.0, h_in=0.8, text="T", font_size_pt=24.0),
                    DesignElement(element_id="v1", kind=ElementKind.image, x_in=1.0, y_in=1.5, w_in=4.0, h_in=3.0, asset_id="A001", asset_path="assets/A001.png"),
                ],
            )
        ],
    )
    with pytest.raises(UnboundVisualError, match="not acquired"):
        compile_deck_ops(sp, design, reg_missing, None, tmp_path)

    # Case B: CC BY-SA asset without author/attribution (constructed bypassing contract check via model_construct)
    bad_asset = AssetRecord.model_construct(
        asset_id="A001",
        status=AssetStatus.acquired,
        type=AssetType.image,
        file_path="assets/A001.png",
        source_url="https://example.org/a.png",
        source_name="Commons",
        license_or_terms="CC BY 4.0",
        author=None,
        attribution=None,
        retrieved_at=NOW,
    )
    reg_no_attr = AssetRegistry.model_construct(run_id="r1-test", assets=[bad_asset], sources=[], summary="1/1")
    with pytest.raises(AttributionMissingError, match="requiring attribution"):
        compile_deck_ops(sp, design, reg_no_attr, None, tmp_path)


# ------------------------------------------------------------------ 4. Diagram wrapping, connectors, source checks, overflow
def test_compile_ops_diagram_wraps_multi_row_emits_all_edges_and_rejects_overflow(tmp_path):
    (tmp_path / "inputs").mkdir()
    sp = _make_slide_plan(("S001",))
    reg = AssetRegistry(run_id="r1-test", assets=[], sources=[], summary="0/0")

    def _run_diag(nodes, edges, w_in=5.5, h_in=4.2):
        payload = json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False).encode("utf-8")
        (tmp_path / "inputs" / "U001.json").write_bytes(payload)
        uir = UserInputRequests(
            run_id="r1-test",
            requests=[
                UserInputRequest(
                    request_id="U001",
                    kind=UserRequestKind.diagram,
                    page_ids=["S001"],
                    need="5 节点流程图",
                    why="D 未找到",
                    format_wanted="JSON",
                    acceptance=["来源明确"],
                    blocking=True,
                    fallback=UserRequestFallback.placeholder,
                    status=UserRequestStatus.provided,
                    provided={
                        "file": "inputs/U001.json",
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "source": "Oke 1982",
                        "license": "CC BY 4.0",
                    },
                )
            ],
            summary="0 open (0 blocking), 1 provided",
        )
        design = PageDesign(
            run_id="r1-test",
            pages=[
                PageDesignEntry(
                    page_id="S001",
                    elements=[
                        DesignElement(element_id="title", kind=ElementKind.title, x_in=0.6, y_in=0.2, w_in=12.0, h_in=0.8, text="流程", font_size_pt=24.0),
                        DesignElement(
                            element_id="diag1",
                            kind=ElementKind.shape,
                            x_in=6.5,
                            y_in=1.5,
                            w_in=w_in,
                            h_in=h_in,
                            role="diagram_json",
                            request_id="U001",
                            asset_path="inputs/U001.json",
                        ),
                    ],
                )
            ],
        )
        return compile_deck_ops(sp, design, reg, uir, tmp_path)

    # 5 nodes + 4 edges in 5.5x4.2 slot -> wraps to 2 rows, ALL 5 nodes & 4 connectors emitted!
    nodes5 = [{"id": f"n{i}", "label": f"步骤{i}", "source": "R001"} for i in range(1, 6)]
    edges4 = [{"from": f"n{i}", "to": f"n{i+1}", "label": f"e{i}", "source": "R001"} for i in range(1, 5)]
    ops = _run_diag(nodes5, edges4, w_in=5.5, h_in=4.2)
    node_ops = [o for o in ops.ops if o.kind is DeckOpKind.add_shape and "diagram_node_id" in o.params]
    edge_ops = [o for o in ops.ops if o.kind is DeckOpKind.add_connector]
    assert len(node_ops) == 5
    assert len(edge_ops) == 4
    # Multi-row wrapping occurred (distinct y coordinates > 1)
    assert len({o.params["y_in"] for o in node_ops}) >= 2
    assert all(o.params["arrow_style"] == "triangle" for o in edge_ops)
    assert all("R001" in o.source_refs for o in node_ops + edge_ops)

    # Node missing source -> UnboundVisualError
    bad_nodes = [{"id": "n1", "label": "无来源节点"}]
    with pytest.raises(UnboundVisualError, match="missing required 'source'"):
        _run_diag(bad_nodes, [])

    # 24 nodes in a tiny 4.0x2.5 slot -> DiagramOverflowError (never silent truncation)
    many_nodes = [{"id": f"n{i}", "label": f"节点{i}", "source": "R001"} for i in range(1, 25)]
    with pytest.raises(DiagramOverflowError, match="cannot fit"):
        _run_diag(many_nodes, [], w_in=4.0, h_in=2.5)


# ------------------------------------------------------------------ 5. Runner: compile_ops done + render_pptx not_run -> incomplete
class _UnavailableRenderer:
    name = "powerpoint-com"

    def preflight(self) -> RendererInfo:
        raise RendererUnavailableError("PowerPoint COM is only available on Windows with desktop PowerPoint")

    def build(self, ops: DeckOps, run_dir: Path) -> BuildResult:
        raise AssertionError("build must not be called when preflight raises")

    def export(self, pptx: Path, out_dir: Path, width_px: int = 1920) -> ExportResult:
        raise AssertionError("export must not be called")

    def measure(self, pptx: Path) -> MeasureResult:
        raise AssertionError("measure must not be called")


def test_runner_compiles_deck_ops_honestly_when_renderer_unavailable(tmp_path):
    rep, run_dir = run_pipeline(
        U,
        tmp_path,
        FakeClient([GOOD_A, GOOD_B, GOOD_C]),
        run_id="r1-linux-like",
        renderer=_UnavailableRenderer(),
    )
    assert (run_dir / ARTIFACT_OPS).is_file()
    assert not (run_dir / "deck.pptx").exists()
    assert rep.overall is RunOverall.incomplete
    st = {s.stage: s for s in rep.stages}
    assert st["F"].status is StageStatus.done
    assert st["G"].status is StageStatus.skipped
    assert st["G"].substeps == {"compile_ops": "done", "render_pptx": "not_run"}
    assert ARTIFACT_OPS in st["G"].output_files
    assert st["H"].status is StageStatus.skipped
