"""PowerPoint COM DeckOps executor: deck_ops.json -> deck.pptx + deck_dump.json.

Adapted from:
  - thomas-villani/pptlive (MIT, 4b33e69217ba): _charts.py (SetSourceData string range + _safe_close),
    _tables.py (HasTable / Cell.Shape.TextFrame2).
  - ykuwai/ppt-mcp (MIT, 2155ecc9050e): charts.py (finally wb.Close(False)), connectors.py (BeginConnect/EndConnect).
  - Internal desktop PowerPoint 16.0 empirical finding (docs/stage-g-office-research.md): AddChart2
    (Office 2013+ / Office 2021 baseline API) requires WithWindow=msoTrue (minimized) on tested
    ClickToRun Current Channel PowerPoint 16.0; non-chart decks use WithWindow=msoFalse.
"""

from __future__ import annotations

import gc
import json
import time
from pathlib import Path
from typing import Any, Callable

from pptv01.contracts import DeckOp, DeckOpKind, DeckOps
from pptv01.renderers.protocol import (
    BuildResult,
    DeckDump,
    RenderExecutionError,
    ShapeDumpEntry,
    SlideDumpEntry,
)

from .constants import (
    ALIGN_MAP,
    ARROW_STYLE_MAP,
    CHART_TYPE_MAP,
    CONNECTOR_TYPE_MAP,
    DASH_STYLE_MAP,
    MSO_ANCHOR_MIDDLE,
    MSO_ANCHOR_TOP,
    MSO_FALSE,
    MSO_SHAPE_TYPE_PLACEHOLDER,
    MSO_TEXT_ORIENTATION_HORIZONTAL,
    MSO_TRUE,
    PP_LAYOUT_BLANK,
    PP_PLACEHOLDER_BODY,
    PP_PLACEHOLDER_CENTER_TITLE,
    PP_PLACEHOLDER_OBJECT,
    PP_PLACEHOLDER_TITLE,
    PP_WINDOW_MINIMIZED,
    SHAPE_TYPE_MAP,
    SLIDE_LAYOUT_MAP,
    excel_col_letter,
    extract_bound_id,
    hex_to_bgr,
    in_to_pt,
)
from .session import open_powerpoint_session, safe_close_workbook

DECK_FILENAME = "deck.pptx"
DUMP_FILENAME = "deck_dump.json"


def _apply_text_style(
    shape: Any,
    *,
    text: str,
    font: str = "Microsoft YaHei",
    font_size_pt: float = 16.0,
    bold: bool = False,
    color_hex: str | None = "#1E293B",
    align: str = "left",
    vertical_anchor: int = MSO_ANCHOR_TOP,
) -> None:
    tf2 = shape.TextFrame2
    try:
        tf2.AutoSize = 0  # msoAutoSizeNone: preserve PageDesign box height so BoundHeight overflow is measurable
    except Exception:
        pass
    try:
        tf2.WordWrap = MSO_TRUE
    except Exception:
        pass
    try:
        tf2.VerticalAnchor = vertical_anchor
    except Exception:
        pass
    tr = tf2.TextRange
    tr.Text = text
    f = tr.Font
    f.Name = font
    try:
        f.NameFarEast = font
    except Exception:
        pass
    f.Size = float(font_size_pt)
    f.Bold = MSO_TRUE if bold else MSO_FALSE
    try:
        f.Fill.ForeColor.RGB = hex_to_bgr(color_hex)
    except Exception:
        pass
    try:
        tr.ParagraphFormat.Alignment = ALIGN_MAP.get(align, 1)
    except Exception:
        pass


def _find_placeholder(slide: Any, placeholder_type: str) -> Any | None:
    wanted = (
        {PP_PLACEHOLDER_TITLE, PP_PLACEHOLDER_CENTER_TITLE}
        if placeholder_type == "title"
        else {PP_PLACEHOLDER_BODY, PP_PLACEHOLDER_OBJECT}
    )
    if placeholder_type == "title":
        try:
            if int(getattr(slide.Shapes, "HasTitle", 0)) == MSO_TRUE:
                return slide.Shapes.Title
        except Exception:
            pass
    for sh in slide.Shapes:
        try:
            if int(getattr(sh, "Type", 0)) == MSO_SHAPE_TYPE_PLACEHOLDER:
                ptype = int(sh.PlaceholderFormat.Type)
                if ptype in wanted:
                    return sh
        except Exception:
            continue
    return None


def _set_slide_notes(slide: Any, text: str) -> None:
    notes_page = slide.NotesPage
    target = None
    for sh in notes_page.Shapes:
        try:
            if int(getattr(sh, "Type", 0)) == MSO_SHAPE_TYPE_PLACEHOLDER:
                if int(sh.PlaceholderFormat.Type) == PP_PLACEHOLDER_BODY:
                    target = sh
                    break
        except Exception:
            continue
    if target is None:
        try:
            target = notes_page.Shapes.Placeholders(2)
        except Exception:
            return
    try:
        target.TextFrame.TextRange.Text = text
    except Exception:
        target.TextFrame2.TextRange.Text = text


def _read_slide_notes(slide: Any) -> str:
    try:
        for sh in slide.NotesPage.Shapes:
            try:
                if int(getattr(sh, "Type", 0)) == MSO_SHAPE_TYPE_PLACEHOLDER:
                    if int(sh.PlaceholderFormat.Type) == PP_PLACEHOLDER_BODY:
                        return str(sh.TextFrame.TextRange.Text or "")
            except Exception:
                continue
        return str(slide.NotesPage.Shapes.Placeholders(2).TextFrame.TextRange.Text or "")
    except Exception:
        return ""


def _add_picture_op(slide: Any, op: DeckOp, run_dir: Path) -> Any:
    p = op.params
    img_abs = (run_dir / str(p["file_path"])).resolve()
    if not img_abs.is_file():
        raise RenderExecutionError(f"{op.op_id}: bound image file not found: {img_abs}")

    x_pt, y_pt = in_to_pt(p["x_in"]), in_to_pt(p["y_in"])
    w_pt, h_pt = in_to_pt(p["w_in"]), in_to_pt(p["h_in"])
    crop_mode = str(p.get("crop_mode", "crop_fill"))

    # Insert at natural size (-1, -1), then crop or fit cleanly without stretching
    pic = slide.Shapes.AddPicture(str(img_abs), MSO_FALSE, MSO_TRUE, x_pt, y_pt, -1, -1)
    orig_w = float(getattr(pic, "Width", w_pt) or w_pt)
    orig_h = float(getattr(pic, "Height", h_pt) or h_pt)

    if orig_w > 0 and orig_h > 0 and w_pt > 0 and h_pt > 0:
        img_ar = orig_w / orig_h
        slot_ar = w_pt / h_pt
        if crop_mode == "crop_fill":
            if img_ar > slot_ar * 1.005:
                excess = max(0.0, orig_w - orig_h * slot_ar)
                pic.PictureFormat.CropLeft = excess / 2.0
                pic.PictureFormat.CropRight = excess / 2.0
            elif img_ar < slot_ar * 0.995:
                excess = max(0.0, orig_h - orig_w / slot_ar)
                pic.PictureFormat.CropTop = excess / 2.0
                pic.PictureFormat.CropBottom = excess / 2.0
            try:
                pic.LockAspectRatio = MSO_FALSE
            except Exception:
                pass
            pic.Left = x_pt
            pic.Top = y_pt
            pic.Width = w_pt
            pic.Height = h_pt
        else:
            scale = min(w_pt / orig_w, h_pt / orig_h)
            fw, fh = orig_w * scale, orig_h * scale
            try:
                pic.LockAspectRatio = MSO_TRUE
            except Exception:
                pass
            pic.Width = fw
            pic.Height = fh
            pic.Left = x_pt + (w_pt - fw) / 2.0
            pic.Top = y_pt + (h_pt - fh) / 2.0

    pic.Name = str(p.get("shape_name") or op.element_id or "picture")
    return pic


def _add_table_op(slide: Any, op: DeckOp) -> Any:
    p = op.params
    cols: list[str] = list(p["columns"])
    rows: list[list[str]] = list(p["rows"])
    n_rows = len(rows) + 1
    n_cols = len(cols)
    tbl_shape = slide.Shapes.AddTable(
        n_rows, n_cols, in_to_pt(p["x_in"]), in_to_pt(p["y_in"]), in_to_pt(p["w_in"]), in_to_pt(p["h_in"])
    )
    tbl_shape.Name = str(p.get("shape_name") or op.element_id or "table")
    table = tbl_shape.Table
    for c_idx, header in enumerate(cols, start=1):
        cell_sh = table.Cell(1, c_idx).Shape
        _apply_text_style(
            cell_sh,
            text=str(header),
            font="Microsoft YaHei",
            font_size_pt=12.0,
            bold=True,
            color_hex="#FFFFFF",
            align="center",
            vertical_anchor=MSO_ANCHOR_MIDDLE,
        )
        try:
            cell_sh.Fill.Solid()
            cell_sh.Fill.ForeColor.RGB = hex_to_bgr("#1E3A8A")
        except Exception:
            pass
    for r_idx, row in enumerate(rows, start=2):
        for c_idx in range(1, n_cols + 1):
            val = str(row[c_idx - 1]) if c_idx - 1 < len(row) else ""
            cell_sh = table.Cell(r_idx, c_idx).Shape
            _apply_text_style(
                cell_sh,
                text=val,
                font="Microsoft YaHei",
                font_size_pt=11.0,
                bold=False,
                color_hex="#1E293B",
                align="left",
                vertical_anchor=MSO_ANCHOR_MIDDLE,
            )
    return tbl_shape


def _add_chart_op(slide: Any, op: DeckOp) -> tuple[Any, str]:
    p = op.params
    xl_type = CHART_TYPE_MAP.get(str(p.get("chart_type", "column")).lower(), 51)
    categories: list[str] = list(p["categories"])
    series: list[dict[str, Any]] = list(p["series"])
    sh_name = str(p.get("shape_name") or op.element_id or "chart")

    chart_shape = slide.Shapes.AddChart2(
        -1, xl_type, in_to_pt(p["x_in"]), in_to_pt(p["y_in"]), in_to_pt(p["w_in"]), in_to_pt(p["h_in"])
    )
    chart_shape.Name = sh_name
    chart = chart_shape.Chart
    title = str(p.get("title") or "").strip()
    if title:
        try:
            chart.HasTitle = True
            chart.ChartTitle.Text = title
        except Exception:
            pass

    cd = chart.ChartData
    wb = None
    last_wb_err: Exception | None = None
    for _wb_attempt in range(5):
        try:
            cd.Activate()
            wb = cd.Workbook
            last_wb_err = None
            break
        except Exception as e:
            last_wb_err = e
            time.sleep(0.4)
    if wb is None:
        raise last_wb_err if last_wb_err is not None else RenderExecutionError(f"{op.op_id}: failed to activate ChartData.Workbook")
    ws = None
    try:
        ws = wb.Worksheets(1)
        try:
            ws.Cells.ClearContents()
        except Exception:
            ws.Cells.Clear()
        ws.Cells(1, 1).Value = "Category"
        for s_idx, s in enumerate(series, start=2):
            ws.Cells(1, s_idx).Value = str(s.get("name") or f"Series {s_idx - 1}")
        for r_idx, cat in enumerate(categories, start=2):
            ws.Cells(r_idx, 1).Value = str(cat)
            for s_idx, s in enumerate(series, start=2):
                vals = s.get("values", [])
                val = vals[r_idx - 2] if (r_idx - 2) < len(vals) else None
                ws.Cells(r_idx, s_idx).Value = float(val) if val is not None else None
        last_col = excel_col_letter(len(series) + 1)
        last_row = len(categories) + 1
        # Must be a string range (pptlive spike finding: passing Range raises E_FAIL)
        chart.SetSourceData(f"Sheet1!$A$1:${last_col}${last_row}")
    finally:
        safe_close_workbook(wb)
        del ws, wb, cd, chart
        gc.collect()
    time.sleep(0.25)
    return chart_shape, sh_name


def _dump_presentation(
    pres: Any,
    *,
    run_id: str,
    page_ids: list[str],
    renderer_version: str,
    renderer_build: str,
) -> DeckDump:
    """Read back the actual PowerPoint shape inventory after SaveAs + read-only reopen."""
    slides_out: list[SlideDumpEntry] = []
    n_slides = int(pres.Slides.Count)
    for idx in range(1, n_slides + 1):
        slide = pres.Slides(idx)
        pid = page_ids[idx - 1] if idx - 1 < len(page_ids) else f"S{idx:03d}"
        shapes_out: list[ShapeDumpEntry] = []
        for sh in slide.Shapes:
            sh_name = str(getattr(sh, "Name", ""))
            sh_type = int(getattr(sh, "Type", 0))
            txt = ""
            try:
                if int(getattr(sh, "HasTextFrame", 0)) == MSO_TRUE and int(getattr(sh.TextFrame2, "HasText", 0)) == MSO_TRUE:
                    txt = str(sh.TextFrame2.TextRange.Text or "")
            except Exception:
                pass
            has_chart = int(getattr(sh, "HasChart", 0)) == MSO_TRUE
            has_table = int(getattr(sh, "HasTable", 0)) == MSO_TRUE
            is_ph = sh_type == MSO_SHAPE_TYPE_PLACEHOLDER
            ph_type: int | None = None
            if is_ph:
                try:
                    ph_type = int(sh.PlaceholderFormat.Type)
                except Exception:
                    pass
            is_conn = int(getattr(sh, "Connector", 0)) == MSO_TRUE
            begin_conn = False
            end_conn = False
            if is_conn:
                try:
                    begin_conn = int(sh.ConnectorFormat.BeginConnected) == MSO_TRUE
                    end_conn = int(sh.ConnectorFormat.EndConnected) == MSO_TRUE
                except Exception:
                    pass
            alt_text = ""
            try:
                alt_text = str(getattr(sh, "AlternativeText", "") or "")
            except Exception:
                pass
            shapes_out.append(
                ShapeDumpEntry(
                    shape_id=int(getattr(sh, "Id", 0)),
                    name=sh_name,
                    shape_type=sh_type,
                    x_pt=round(float(getattr(sh, "Left", 0.0)), 2),
                    y_pt=round(float(getattr(sh, "Top", 0.0)), 2),
                    w_pt=round(float(getattr(sh, "Width", 0.0)), 2),
                    h_pt=round(float(getattr(sh, "Height", 0.0)), 2),
                    text=txt,
                    has_chart=has_chart,
                    has_table=has_table,
                    is_placeholder=is_ph,
                    placeholder_type=ph_type,
                    is_connector=is_conn,
                    begin_connected=begin_conn,
                    end_connected=end_conn,
                    alt_text=alt_text,
                    bound_id=extract_bound_id(sh_name),
                )
            )
        slides_out.append(
            SlideDumpEntry(
                slide_index=idx,
                page_id=pid,
                notes=_read_slide_notes(slide),
                shapes=shapes_out,
            )
        )

    return DeckDump(
        run_id=run_id,
        renderer_name="powerpoint-com",
        renderer_version=renderer_version,
        renderer_build=renderer_build,
        slide_width_pt=round(float(pres.PageSetup.SlideWidth), 2),
        slide_height_pt=round(float(pres.PageSetup.SlideHeight), 2),
        slides=slides_out,
    )


def _is_transient_rpc_error(exc: BaseException) -> bool:
    """Return True for transient Windows COM RPC disconnect errors (-2147023174 / -2147023179)."""
    msg = str(exc)
    return any(code in msg for code in ("-2147023174", "-2147023179", "-2147417848", "RPC"))


def build_presentation(
    ops: DeckOps,
    run_dir: Path,
    *,
    app_factory: Callable[[], Any] | None = None,
) -> BuildResult:
    """Execute DeckOps via PowerPoint COM to produce deck.pptx and deck_dump.json."""
    last_exc: Exception | None = None
    max_attempts = 1 if app_factory is not None else 2
    for attempt in range(1, max_attempts + 1):
        try:
            return _build_presentation_once(ops, run_dir, app_factory=app_factory)
        except (RenderExecutionError, FileNotFoundError, ValueError):
            raise
        except Exception as e:
            if attempt < max_attempts and _is_transient_rpc_error(e):
                last_exc = e
                gc.collect()
                time.sleep(0.8)
                continue
            raise
    assert last_exc is not None  # pragma: no cover
    raise last_exc


def _build_presentation_once(
    ops: DeckOps,
    run_dir: Path,
    *,
    app_factory: Callable[[], Any] | None = None,
) -> BuildResult:
    run_dir = Path(run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    out_pptx = (run_dir / DECK_FILENAME).resolve()
    out_dump = (run_dir / DUMP_FILENAME).resolve()

    needs_chart_window = any(o.kind is DeckOpKind.add_chart for o in ops.ops)
    with_window = MSO_TRUE if needs_chart_window else MSO_FALSE
    build_notes: list[str] = []
    if needs_chart_window:
        build_notes.append("opened presentation with minimized window (WithWindow=-1) for desktop PowerPoint 16.0 AddChart2 compatibility")

    with open_powerpoint_session(app_factory=app_factory) as sess:
        app = sess.app
        version = str(getattr(app, "Version", ""))
        build = str(getattr(app, "Build", ""))

        if ops.template_path:
            tpl_abs = (run_dir / ops.template_path).resolve()
            if not tpl_abs.is_file():
                raise RenderExecutionError(f"template_path not found: {tpl_abs}")
            with sess.step("build:open_template"):
                pres = sess.track_presentation(app.Presentations.Open(str(tpl_abs), MSO_FALSE, MSO_TRUE, with_window))
        else:
            with sess.step("build:new_presentation"):
                pres = sess.track_presentation(app.Presentations.Add(with_window))

        if needs_chart_window and sess.owns_instance:
            try:
                app.WindowState = PP_WINDOW_MINIMIZED
            except Exception:
                pass

        try:
            pres.PageSetup.SlideWidth = in_to_pt(ops.canvas_width_in)
            pres.PageSetup.SlideHeight = in_to_pt(ops.canvas_height_in)

            current_slide: Any = None
            slide_shapes: dict[str, Any] = {}  # element_id and shape_name -> COM shape
            slide_count = 0

            for op in ops.ops:
                p = op.params
                if op.kind is DeckOpKind.add_slide:
                    slide_count += 1
                    layout_id = SLIDE_LAYOUT_MAP.get(str(p.get("layout", "blank")), PP_LAYOUT_BLANK)
                    current_slide = pres.Slides.Add(slide_count, layout_id)
                    slide_shapes = {}
                    continue

                if current_slide is None:
                    raise RenderExecutionError(f"{op.op_id}: operation appeared before add_slide")

                if op.kind is DeckOpKind.set_placeholder_text:
                    ph_type = str(p.get("placeholder_type", "title"))
                    sh = _find_placeholder(current_slide, ph_type)
                    if sh is None:
                        sh = current_slide.Shapes.AddTextbox(
                            MSO_TEXT_ORIENTATION_HORIZONTAL,
                            in_to_pt(p["x_in"]),
                            in_to_pt(p["y_in"]),
                            in_to_pt(p["w_in"]),
                            in_to_pt(p["h_in"]),
                        )
                    else:
                        if all(k in p for k in ("x_in", "y_in", "w_in", "h_in")):
                            sh.Left = in_to_pt(p["x_in"])
                            sh.Top = in_to_pt(p["y_in"])
                            sh.Width = in_to_pt(p["w_in"])
                            sh.Height = in_to_pt(p["h_in"])
                    sh_name = str(p.get("shape_name") or op.element_id or ph_type)
                    sh.Name = sh_name
                    _apply_text_style(
                        sh,
                        text=str(p.get("text", "")),
                        font=str(p.get("font") or "Microsoft YaHei"),
                        font_size_pt=float(p.get("font_size_pt", 28.0)),
                        bold=bool(p.get("bold", True)),
                        color_hex=p.get("color_hex"),
                        align=str(p.get("align", "left")),
                    )
                    # Native layout placeholders start at Z-order 1 before bg_header is added;
                    # bring placeholder to front (msoBringToFront = 0, Office 97+/2021 baseline API)
                    # so header background bands can never occlude the slide title.
                    try:
                        sh.ZOrder(0)
                    except Exception:
                        pass
                    if op.element_id:
                        slide_shapes[op.element_id] = sh
                    slide_shapes[sh_name] = sh
                    continue

                if op.kind is DeckOpKind.add_textbox:
                    tb = current_slide.Shapes.AddTextbox(
                        MSO_TEXT_ORIENTATION_HORIZONTAL,
                        in_to_pt(p["x_in"]),
                        in_to_pt(p["y_in"]),
                        in_to_pt(p["w_in"]),
                        in_to_pt(p["h_in"]),
                    )
                    sh_name = str(p.get("shape_name") or op.element_id or "textbox")
                    tb.Name = sh_name
                    _apply_text_style(
                        tb,
                        text=str(p.get("text", "")),
                        font=str(p.get("font") or "Microsoft YaHei"),
                        font_size_pt=float(p.get("font_size_pt", 16.0)),
                        bold=bool(p.get("bold", False)),
                        color_hex=p.get("color_hex"),
                        align=str(p.get("align", "left")),
                    )
                    if op.element_id:
                        slide_shapes[op.element_id] = tb
                    slide_shapes[sh_name] = tb
                    continue

                if op.kind is DeckOpKind.add_shape:
                    st = SHAPE_TYPE_MAP.get(str(p.get("shape_type", "rectangle")), 1)
                    sh = current_slide.Shapes.AddShape(
                        st,
                        in_to_pt(p["x_in"]),
                        in_to_pt(p["y_in"]),
                        in_to_pt(p["w_in"]),
                        in_to_pt(p["h_in"]),
                    )
                    sh_name = str(p.get("shape_name") or op.element_id or "shape")
                    sh.Name = sh_name
                    if p.get("fill_hex"):
                        sh.Fill.Solid()
                        sh.Fill.ForeColor.RGB = hex_to_bgr(p["fill_hex"])
                    else:
                        try:
                            sh.Fill.Visible = MSO_FALSE
                        except Exception:
                            pass
                    if p.get("line_hex"):
                        sh.Line.Visible = MSO_TRUE
                        sh.Line.ForeColor.RGB = hex_to_bgr(p["line_hex"])
                        sh.Line.Weight = float(p.get("line_width_pt", 1.0))
                    else:
                        try:
                            sh.Line.Visible = MSO_FALSE
                        except Exception:
                            pass
                    if p.get("text"):
                        _apply_text_style(
                            sh,
                            text=str(p["text"]),
                            font=str(p.get("font") or "Microsoft YaHei"),
                            font_size_pt=float(p.get("font_size_pt", 11.5)),
                            bold=bool(p.get("bold", False)),
                            color_hex=p.get("color_hex", "#0F172A"),
                            align=str(p.get("align", "center")),
                            vertical_anchor=MSO_ANCHOR_MIDDLE,
                        )
                    if (
                        int(p.get("z_index", 0)) == 0
                        and (
                            sh_name.lower().startswith("bg_")
                            or str(p.get("role", "")) in ("header_band", "background_card", "canvas_bg", "quote_bg", "section_bg_left", "section_bg_right", "bleed_editorial_band", "fact_highlight_bg")
                        )
                    ):
                        # Send background bands behind native layout placeholders (msoSendToBack = 1)
                        try:
                            sh.ZOrder(1)
                        except Exception:
                            pass
                    if op.element_id:
                        slide_shapes[op.element_id] = sh
                    slide_shapes[sh_name] = sh
                    continue

                if op.kind is DeckOpKind.add_placeholder_box:
                    sh = current_slide.Shapes.AddShape(
                        1,  # msoShapeRectangle
                        in_to_pt(p["x_in"]),
                        in_to_pt(p["y_in"]),
                        in_to_pt(p["w_in"]),
                        in_to_pt(p["h_in"]),
                    )
                    sh_name = str(p.get("shape_name") or op.element_id)
                    sh.Name = sh_name
                    sh.Fill.Solid()
                    sh.Fill.ForeColor.RGB = hex_to_bgr(p.get("fill_hex", "#FFFBEB"), (255, 251, 235))
                    sh.Line.Visible = MSO_TRUE
                    sh.Line.ForeColor.RGB = hex_to_bgr(p.get("line_hex", "#D97706"), (217, 119, 6))
                    sh.Line.Weight = float(p.get("line_width_pt", 1.5))
                    sh.Line.DashStyle = DASH_STYLE_MAP.get(str(p.get("dash_style", "dash")), 4)
                    _apply_text_style(
                        sh,
                        text=str(p.get("text", "")),
                        font=str(p.get("font") or "Microsoft YaHei"),
                        font_size_pt=float(p.get("font_size_pt", 11.5)),
                        bold=bool(p.get("bold", False)),
                        color_hex=p.get("color_hex", "#92400E"),
                        align=str(p.get("align", "left")),
                        vertical_anchor=MSO_ANCHOR_MIDDLE,
                    )
                    if op.element_id:
                        slide_shapes[op.element_id] = sh
                    slide_shapes[sh_name] = sh
                    continue

                if op.kind is DeckOpKind.add_picture:
                    pic = _add_picture_op(current_slide, op, run_dir)
                    if op.element_id:
                        slide_shapes[op.element_id] = pic
                    slide_shapes[pic.Name] = pic
                    continue

                if op.kind is DeckOpKind.add_table:
                    tbl = _add_table_op(current_slide, op)
                    if op.element_id:
                        slide_shapes[op.element_id] = tbl
                    slide_shapes[tbl.Name] = tbl
                    continue

                if op.kind is DeckOpKind.add_chart:
                    with sess.step(f"build:add_chart:{op.op_id}"):
                        ch, ch_name = _add_chart_op(current_slide, op)
                        sess.record_spawned_excel_pids()
                    if op.element_id:
                        slide_shapes[op.element_id] = ch
                    slide_shapes[ch_name] = ch
                    continue

                if op.kind is DeckOpKind.add_connector:
                    src_key = str(p["from_element_id"])
                    dst_key = str(p["to_element_id"])
                    src_sh = slide_shapes.get(src_key)
                    dst_sh = slide_shapes.get(dst_key)
                    if src_sh is None or dst_sh is None:
                        raise RenderExecutionError(
                            f"{op.op_id}: connector endpoints not found on slide ({src_key!r} -> {dst_key!r})"
                        )
                    ctype = CONNECTOR_TYPE_MAP.get(str(p.get("connector_type", "straight")), 1)
                    conn = current_slide.Shapes.AddConnector(ctype, 0.0, 0.0, 100.0, 100.0)
                    sh_name = str(p.get("shape_name") or op.element_id or "connector")
                    conn.Name = sh_name
                    conn.ConnectorFormat.BeginConnect(src_sh, int(p.get("begin_site", 4)))
                    conn.ConnectorFormat.EndConnect(dst_sh, int(p.get("end_site", 2)))
                    conn.Line.ForeColor.RGB = hex_to_bgr(p.get("line_hex", "#1E3A8A"), (30, 58, 138))
                    conn.Line.Weight = float(p.get("line_width_pt", 1.5))
                    conn.Line.EndArrowheadStyle = ARROW_STYLE_MAP.get(str(p.get("arrow_style", "triangle")), 2)
                    if p.get("label"):
                        try:
                            conn.AlternativeText = str(p["label"])
                        except Exception:
                            pass
                    if op.element_id:
                        slide_shapes[op.element_id] = conn
                    slide_shapes[sh_name] = conn
                    continue

                if op.kind is DeckOpKind.set_alt_text:
                    key = str(p.get("shape_name") or p.get("target_element_id") or op.element_id or "")
                    target = slide_shapes.get(key) or slide_shapes.get(str(p.get("target_element_id") or ""))
                    if target is not None:
                        try:
                            target.AlternativeText = str(p.get("alt_text", ""))
                        except Exception:
                            pass
                    continue

                if op.kind is DeckOpKind.set_name:
                    key = str(p.get("target_element_id") or op.element_id or "")
                    target = slide_shapes.get(key)
                    new_name = str(p.get("shape_name") or "")
                    if target is not None and new_name:
                        target.Name = new_name
                        slide_shapes[new_name] = target
                    continue

                if op.kind is DeckOpKind.set_notes:
                    _set_slide_notes(current_slide, str(p.get("text", "")))
                    continue

            with sess.step("build:save_pptx"):
                pres.SaveAs(str(out_pptx))
        finally:
            sess.close_presentation(pres)

    # Read back via a fresh read-only windowless COM session to produce deck_dump.json.
    # (Closing the last windowed presentation on Office 2021 may auto-exit POWERPNT.EXE,
    # so opening a fresh session guarantees a live COM handle.)
    with open_powerpoint_session(app_factory=app_factory) as ro_sess:
        ro_app = ro_sess.app
        ro_pres = ro_sess.track_presentation(ro_app.Presentations.Open(str(out_pptx), MSO_TRUE, MSO_FALSE, MSO_FALSE))
        try:
            dump = _dump_presentation(
                ro_pres,
                run_id=ops.run_id,
                page_ids=ops.page_ids,
                renderer_version=version,
                renderer_build=build,
            )
            reopened_count = int(ro_pres.Slides.Count)
        finally:
            ro_sess.close_presentation(ro_pres)

    if reopened_count != len(ops.page_ids):
        raise RenderExecutionError(
            f"reopened deck.pptx has {reopened_count} slides, expected {len(ops.page_ids)}"
        )

    out_dump.write_text(
        json.dumps(dump.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    return BuildResult(
        pptx_path=out_pptx,
        dump_path=out_dump,
        slide_count=reopened_count,
        renderer_name="powerpoint-com",
        renderer_version=version,
        renderer_build=build,
        build_notes=build_notes,
    )
