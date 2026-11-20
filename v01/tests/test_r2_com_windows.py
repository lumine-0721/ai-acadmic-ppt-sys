"""Real Windows Microsoft PowerPoint COM tests (@pytest.mark.windows_com).

Skips cleanly with an explicit reason on non-Windows platforms or machines
without Microsoft PowerPoint / pywin32 installed.
Verifies:
  - Native placeholders, CJK NameFarEast, embedded picture with crop_fill,
    native table, native chart (AddChart2 + Excel cleanup), placeholder box,
    and a 5-node 4-edge arrowed flowchart (B5 regression).
  - Independent read-only python-pptx verification + COM deck_dump.json match.
  - Real PowerPoint export (slide_*.png + deck.pdf) and TextFrame2 BoundHeight measure.
  - Non-interference with pre-existing user presentations.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image
from pptx import Presentation

from pptv01.renderers import DeckDump, PowerPointComRenderer, RendererUnavailableError
from tests.test_r2_com_fake import _make_full_ops


def _require_windows_powerpoint():
    if sys.platform != "win32":
        pytest.skip("windows_com test requires Windows with Microsoft PowerPoint")
    renderer = PowerPointComRenderer()
    try:
        info = renderer.preflight()
    except RendererUnavailableError as e:
        pytest.skip(f"PowerPoint COM unavailable: {e}")
    return renderer, info


@pytest.mark.windows_com
def test_real_powerpoint_com_build_export_measure_and_b5_five_node_diagram(tmp_path):
    renderer, info = _require_windows_powerpoint()
    ops = _make_full_ops(tmp_path)

    assert info.available and info.version.startswith("16.")
    assert info.excel_available, "Office 2021 Excel COM must be available for AddChart2"

    res = renderer.build(ops, tmp_path)
    assert res.pptx_path.is_file()
    assert res.dump_path.is_file()
    assert res.slide_count == 2

    # 1. Verify COM deck_dump.json
    dump = DeckDump.model_validate_json(res.dump_path.read_text(encoding="utf-8"))
    assert len(dump.slides) == 2
    s1_names = {s.name for s in dump.slides[0].shapes}
    assert "ASSET_A001__v_img" in s1_names
    assert "CHART_INPUT_U002__v_chart" in s1_names
    assert "PLACEHOLDER_U001" in s1_names
    assert any(s.is_placeholder and s.name == "title" for s in dump.slides[0].shapes)
    assert "Tester, CC BY 4.0" in dump.slides[0].notes

    s2_shapes = dump.slides[1].shapes
    dump_nodes = [s for s in s2_shapes if s.name.startswith("DIAGRAM_INPUT_U004__v_diag_node_")]
    dump_edges = [s for s in s2_shapes if s.is_connector and s.name.startswith("DIAGRAM_INPUT_U004__v_diag_edge_")]
    assert len(dump_nodes) == 5, f"expected 5 diagram nodes in dump, got {len(dump_nodes)}"
    assert len(dump_edges) == 4, f"expected 4 connected diagram edges in dump, got {len(dump_edges)}"
    assert all(e.begin_connected and e.end_connected for e in dump_edges)

    # 2. Independent read-only verification via python-pptx
    prs = Presentation(str(res.pptx_path))
    assert len(prs.slides) == 2
    ro_s1_names = {sh.name for sh in prs.slides[0].shapes}
    assert {"ASSET_A001__v_img", "CHART_INPUT_U002__v_chart", "PLACEHOLDER_U001", "title", "bullets"} <= ro_s1_names
    assert any(sh.has_chart for sh in prs.slides[0].shapes)
    assert any(sh.is_placeholder for sh in prs.slides[0].shapes)

    ro_s2_names = [sh.name for sh in prs.slides[1].shapes]
    assert any(sh.has_table for sh in prs.slides[1].shapes)
    ro_nodes = [n for n in ro_s2_names if n.startswith("DIAGRAM_INPUT_U004__v_diag_node_")]
    ro_edges = [n for n in ro_s2_names if n.startswith("DIAGRAM_INPUT_U004__v_diag_edge_")]
    assert len(ro_nodes) == 5
    assert len(ro_edges) == 4

    # 3. Real PowerPoint export -> slide_001.png, slide_002.png, deck.pdf
    exp = renderer.export(res.pptx_path, tmp_path / "render", width_px=1280)
    assert [p.name for p in exp.png_paths] == ["slide_001.png", "slide_002.png"]
    for p in exp.png_paths:
        with Image.open(p) as im:
            assert im.width >= 1000 and im.height >= 500
    assert exp.pdf_path is not None and exp.pdf_path.is_file() and exp.pdf_path.stat().st_size > 1000

    # 4. Real PowerPoint TextFrame2 measurement
    meas = renderer.measure(res.pptx_path, page_ids=ops.page_ids)
    assert len(meas.measurements) >= 4
    title_m = next(m for m in meas.measurements if m.page_id == "S001" and m.shape_name == "title")
    assert title_m.bound_h_pt > 0 and title_m.bound_w_pt > 0
    assert title_m.font_name_fareast == "Microsoft YaHei"


@pytest.mark.windows_com
def test_real_powerpoint_com_leaves_unrelated_open_presentation_untouched(tmp_path):
    renderer, _ = _require_windows_powerpoint()
    import pythoncom
    import win32com.client

    ops = _make_full_ops(tmp_path)
    # Strip chart op so this quick politeness test runs windowless
    ops_no_chart = ops.model_copy(update={"ops": [o for o in ops.ops if o.kind.value != "add_chart"]})

    from pptv01.renderers.powerpoint_com.session import _ensure_com_initialized

    _ensure_com_initialized(pythoncom)
    app = win32com.client.Dispatch("PowerPoint.Application")
    user_pres = None
    try:
        user_pres = app.Presentations.Add(WithWindow=0)
        user_pres.Slides.Add(1, 12)
        before_count = int(app.Presentations.Count)

        res = renderer.build(ops_no_chart, tmp_path)
        assert res.pptx_path.is_file()
        # User presentation must still be open and have its slide intact
        assert int(app.Presentations.Count) == before_count
        assert int(user_pres.Slides.Count) == 1
    finally:
        if user_pres is not None:
            try:
                user_pres.Close()
            except Exception:
                pass
        if int(app.Presentations.Count) == 0:
            try:
                app.Quit()
            except Exception:
                pass
