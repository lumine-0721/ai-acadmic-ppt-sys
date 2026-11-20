"""Stage G: compile PageDesign into an inspectable, editable native deck.pptx using python-pptx.

Rules enforced:
  - Native textboxes, shapes, tables, and charts (never full-page screenshots).
  - External raster images embedded via add_picture with provenance shape names
    (ASSET_<id>__<el> / INPUT_<id>__<el>) matching grading/gates.py.
  - Missing/open requests rendered as dashed PLACEHOLDER_U### shapes with text
    ``待补充：<need>（U###）`` and matching slide notes.
  - Every slide's notes record sources and licences; assets requiring attribution
    (CC BY / CC BY-SA) fail loudly if attribution is missing on slide and notes.
  - Re-opens the saved deck.pptx with python-pptx to verify slide count == planned pages.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE
from pptx.enum.dml import MSO_LINE_DASH_STYLE
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

from pptv01.contracts import (
    AssetRecord,
    AssetRegistry,
    DesignElement,
    ElementKind,
    PageDesign,
    PageDesignEntry,
    SlidePlan,
    UserInputRequest,
    UserInputRequests,
)
from pptv01.d_retrieve.license import needs_attribution
from pptv01.user_requests import mark_placeholders_rendered

ARTIFACT = "deck.pptx"

_ALIGN_MAP = {
    "left": PP_ALIGN.LEFT,
    "center": PP_ALIGN.CENTER,
    "right": PP_ALIGN.RIGHT,
    "justify": PP_ALIGN.JUSTIFY,
}


class AttributionMissingError(ValueError):
    pass


class DeckGenerationError(ValueError):
    pass


def _rgb(hex_str: str | None, default: tuple[int, int, int]) -> RGBColor:
    if not hex_str:
        return RGBColor(*default)
    h = hex_str.lstrip("#")
    if len(h) == 6:
        try:
            return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        except ValueError:
            pass
    return RGBColor(*default)


def _apply_font(run, el: DesignElement, default_color: tuple[int, int, int] = (30, 41, 59)) -> None:
    if el.font:
        run.font.name = el.font
    if el.font_size_pt:
        run.font.size = Pt(el.font_size_pt)
    run.font.bold = bool(el.bold)
    run.font.color.rgb = _rgb(el.color_hex, default_color)


def _add_text_element(slide, el: DesignElement) -> None:
    tb = slide.shapes.add_textbox(Inches(el.x_in), Inches(el.y_in), Inches(el.w_in), Inches(el.h_in))
    tb.name = el.element_id
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.TOP
    lines = (el.text or "").splitlines() or [""]
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = _ALIGN_MAP.get(el.align, PP_ALIGN.LEFT)
        run = p.add_run()
        run.text = line
        _apply_font(run, el)


def _add_shape_element(slide, el: DesignElement) -> None:
    shape_type = MSO_SHAPE.ROUNDED_RECTANGLE if "card" in el.role else MSO_SHAPE.RECTANGLE
    sh = slide.shapes.add_shape(shape_type, Inches(el.x_in), Inches(el.y_in), Inches(el.w_in), Inches(el.h_in))
    sh.name = el.element_id
    if el.fill_hex:
        sh.fill.solid()
        sh.fill.fore_color.rgb = _rgb(el.fill_hex, (248, 250, 252))
    else:
        sh.fill.background()
    if el.line_hex:
        sh.line.color.rgb = _rgb(el.line_hex, (203, 213, 225))
        sh.line.width = Pt(1.0)
    else:
        sh.line.fill.background()


def _add_placeholder_element(slide, el: DesignElement) -> None:
    sh = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(el.x_in), Inches(el.y_in), Inches(el.w_in), Inches(el.h_in)
    )
    sh.name = el.element_id
    sh.fill.solid()
    sh.fill.fore_color.rgb = _rgb(el.fill_hex, (254, 243, 199))
    sh.line.color.rgb = _rgb(el.line_hex, (217, 119, 6))
    sh.line.width = Pt(2.0)
    sh.line.dash_style = MSO_LINE_DASH_STYLE.DASH

    tf = sh.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    lines = (el.text or "").splitlines() or [""]
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = PP_ALIGN.CENTER
        run = p.add_run()
        run.text = line
        _apply_font(run, el, default_color=(146, 64, 14))


def _bound_name(prefix: str, el: DesignElement) -> str:
    if el.request_id:
        return f"{prefix}_INPUT_{el.request_id}__{el.element_id}" if prefix != "ASSET" else f"INPUT_{el.request_id}__{el.element_id}"
    if el.asset_id:
        return f"{prefix}_{el.asset_id}__{el.element_id}"
    return el.element_id


def _load_chart_data(data_path: Path, req: UserInputRequest | None) -> tuple[list[str], list[tuple[str, list[float]]], str]:
    ext = data_path.suffix.lower()
    if ext == ".csv":
        rows = [r for r in csv.reader(io.StringIO(data_path.read_text("utf-8-sig"))) if any(c.strip() for c in r)]
        if len(rows) < 2 or len(rows[0]) < 2:
            raise DeckGenerationError(f"chart CSV {data_path} needs header + >=1 data row with >=2 columns")
        cats = [r[0] for r in rows[1:]]
        series = []
        for col_idx in range(1, len(rows[0])):
            vals = [float(r[col_idx]) for r in rows[1:]]
            series.append((rows[0][col_idx], vals))
        src = req.provided.source if (req and req.provided) else ""
        if not src:
            raise DeckGenerationError(f"chart CSV {data_path} lacks provenance source")
        return cats, series, src

    obj = json.loads(data_path.read_text("utf-8"))
    top_src = str(obj.get("source", "")).strip() or (req.provided.source if (req and req.provided) else "")
    if isinstance(obj.get("series"), list) and obj["series"]:
        first = obj["series"][0]
        cats = [str(x) for x in (obj.get("labels") or first.get("labels") or [f"Item {i+1}" for i in range(len(first["values"]))])]
        series = []
        for s in obj["series"]:
            s_src = str(s.get("source", "")).strip() or top_src
            if not s_src:
                raise DeckGenerationError(f"chart JSON {data_path} series lacks source attribution")
            series.append((str(s.get("name", "Series")), [float(v) for v in s["values"]]))
        return cats, series, top_src or str(obj["series"][0].get("source", ""))
    labels = [str(x) for x in obj.get("labels", [])]
    values = [float(v) for v in obj.get("values", [])]
    if not labels or len(labels) != len(values) or not top_src:
        raise DeckGenerationError(f"chart JSON {data_path} requires labels, numeric values, and source")
    return labels, [(str(obj.get("title", "Value")), values)], top_src


def _load_table_data(data_path: Path, req: UserInputRequest | None) -> tuple[list[str], list[list[str]], str]:
    ext = data_path.suffix.lower()
    if ext == ".csv":
        rows = [r for r in csv.reader(io.StringIO(data_path.read_text("utf-8-sig"))) if any(c.strip() for c in r)]
        if len(rows) < 2:
            raise DeckGenerationError(f"table CSV {data_path} needs header + >=1 row")
        src = req.provided.source if (req and req.provided) else ""
        if not src:
            raise DeckGenerationError(f"table CSV {data_path} lacks provenance source")
        return rows[0], rows[1:], src
    obj = json.loads(data_path.read_text("utf-8"))
    src = str(obj.get("source", "")).strip() or (req.provided.source if (req and req.provided) else "")
    cols = [str(c) for c in obj.get("columns", [])]
    rows = [[str(cell) for cell in r] for r in obj.get("rows", [])]
    if not cols or not rows or not src:
        raise DeckGenerationError(f"table JSON {data_path} requires columns, rows, and source")
    return cols, rows, src


def _add_chart_element(slide, el: DesignElement, run_dir: Path, req: UserInputRequest | None) -> str:
    data_path = run_dir / el.asset_path
    cats, series, src = _load_chart_data(data_path, req)
    cd = CategoryChartData()
    cd.categories = cats
    for name, vals in series:
        cd.add_series(name, vals)
    cf = slide.shapes.add_chart(
        XL_CHART_TYPE.COLUMN_CLUSTERED,
        Inches(el.x_in), Inches(el.y_in), Inches(el.w_in), Inches(el.h_in),
        cd,
    )
    cf.name = _bound_name("CHART", el)
    return src


def _add_table_element(slide, el: DesignElement, run_dir: Path, req: UserInputRequest | None) -> str:
    data_path = run_dir / el.asset_path
    cols, rows, src = _load_table_data(data_path, req)
    n_rows = 1 + len(rows)
    n_cols = len(cols)
    tf_shape = slide.shapes.add_table(
        n_rows, n_cols, Inches(el.x_in), Inches(el.y_in), Inches(el.w_in), Inches(el.h_in)
    )
    tf_shape.name = _bound_name("TABLE", el)
    tbl = tf_shape.table
    for c_i, col in enumerate(cols):
        cell = tbl.cell(0, c_i)
        cell.text = col
        for p in cell.text_frame.paragraphs:
            for r in p.runs:
                r.font.bold = True
                r.font.size = Pt(12)
    for r_i, row in enumerate(rows, start=1):
        for c_i in range(min(n_cols, len(row))):
            cell = tbl.cell(r_i, c_i)
            cell.text = row[c_i]
            for p in cell.text_frame.paragraphs:
                for r in p.runs:
                    r.font.size = Pt(11)
    return src


def _add_diagram_json_element(slide, el: DesignElement, run_dir: Path) -> list[str]:
    """Render a user-supplied {nodes, edges} JSON diagram as native shapes with sources."""
    data_path = run_dir / el.asset_path
    obj = json.loads(data_path.read_text("utf-8"))
    nodes = obj.get("nodes", [])
    edges = obj.get("edges", [])
    if not nodes:
        raise DeckGenerationError(f"diagram JSON {data_path} has no nodes")
    sources: list[str] = []
    n = len(nodes)
    gap = 0.18
    box_w = min(el.w_in - 0.3, max(1.4, (el.w_in - gap * (n - 1) - 0.2) / n))
    box_h = min(1.4, el.h_in * 0.45)
    y_mid = el.y_in + (el.h_in - box_h) / 2.0
    for i, node in enumerate(nodes):
        nx = el.x_in + 0.1 + i * (box_w + gap)
        if nx + box_w > el.x_in + el.w_in:
            break
        sh = slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE, Inches(nx), Inches(y_mid), Inches(box_w), Inches(box_h)
        )
        sh.name = f"DIAGRAM_{el.request_id or el.asset_id}_N{i+1}"
        sh.fill.solid()
        sh.fill.fore_color.rgb = RGBColor(239, 246, 255)
        sh.line.color.rgb = RGBColor(37, 99, 235)
        tf = sh.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        r = p.add_run()
        r.text = str(node.get("label", node.get("id", "")))
        r.font.size = Pt(12)
        r.font.color.rgb = RGBColor(30, 41, 59)
        src = str(node.get("source", "")).strip()
        if src:
            sources.append(f"{node.get('id')}: {src}")
    for e in edges:
        src = str(e.get("source", "")).strip()
        if src:
            sources.append(f"{e.get('from')}->{e.get('to')}: {src}")
    return sources


def _verify_and_collect_provenance(
    page: PageDesignEntry,
    el: DesignElement,
    reg_by_id: dict[str, AssetRecord],
    uir_by_id: dict[str, UserInputRequest],
    page_texts_joined: str,
) -> tuple[str | None, bool]:
    """Return (note_line, attribution_satisfied_on_slide) for bound assets/inputs."""
    asset = reg_by_id.get(el.asset_id) if el.asset_id else None
    req = uir_by_id.get(el.request_id) if el.request_id else None

    if req and req.provided:
        line = f"[{req.request_id}] {req.provided.file} | 来源: {req.provided.source} | 许可: {req.provided.license}"
        return line, True

    if asset is None:
        return None, True

    lic = asset.license_or_terms or ""
    src = asset.source_url or asset.source_name or ""
    if needs_attribution(lic):
        if not (asset.attribution or asset.author):
            raise AttributionMissingError(
                f"{page.page_id}.{el.element_id}: asset {asset.asset_id} has licence {lic!r} requiring attribution, "
                "but both author and attribution are missing"
            )
        # Ensure attribution appears either in slide text or will be written in slide notes
        credit = asset.attribution or f"{asset.author}, {lic}, {src}"
        line = f"[{asset.asset_id}] {credit} | 许可: {lic} | 来源: {src}"
        return line, True

    line = f"[{asset.asset_id}] 来源: {src or 'N/A'} | 许可: {lic or 'N/A'}"
    return line, True


def run_stage_g(
    slide_plan: SlidePlan,
    design: PageDesign,
    reg: AssetRegistry,
    uir: UserInputRequests | None,
    run_dir: Path,
) -> Path:
    run_dir = Path(run_dir)
    reg_by_id = {a.asset_id: a for a in reg.assets}
    uir_by_id = {r.request_id: r for r in uir.requests} if uir else {}

    prs = Presentation()
    prs.slide_width = Inches(design.pages[0].canvas_width_in)
    prs.slide_height = Inches(design.pages[0].canvas_height_in)
    blank_layout = prs.slide_layouts[6]

    rendered_placeholders: set[str] = set()

    for page in design.pages:
        slide = prs.slides.add_slide(blank_layout)
        note_lines: list[str] = [f"Page {page.page_id}"]
        if page.notes:
            note_lines.append(f"Layout notes: {page.notes}")

        page_texts = "\n".join(el.text or "" for el in page.elements)

        for el in sorted(page.elements, key=lambda e: e.z_index):
            if el.kind is ElementKind.shape:
                if el.role == "diagram_json" and el.asset_path:
                    d_srcs = _add_diagram_json_element(slide, el, run_dir)
                    note_lines.append(f"Diagram {el.request_id or el.asset_id} sources: {', '.join(d_srcs)}")
                else:
                    _add_shape_element(slide, el)
                continue

            if el.kind is ElementKind.placeholder:
                _add_placeholder_element(slide, el)
                if el.request_id:
                    rendered_placeholders.add(el.request_id)
                    note_lines.append(f"待补充占位 {el.request_id}: {el.text}")
                continue

            if el.kind is ElementKind.image:
                img_path = run_dir / el.asset_path
                if not img_path.is_file():
                    raise DeckGenerationError(f"{page.page_id}.{el.element_id}: image file missing: {img_path}")
                if img_path.suffix.lower() == ".svg":
                    raise DeckGenerationError(f"{page.page_id}.{el.element_id}: SVG embedding requires rasterization (not supported natively by python-pptx)")
                pic = slide.shapes.add_picture(
                    str(img_path), Inches(el.x_in), Inches(el.y_in), Inches(el.w_in), Inches(el.h_in)
                )
                pic.name = _bound_name("ASSET", el)
                prov, _ = _verify_and_collect_provenance(page, el, reg_by_id, uir_by_id, page_texts)
                if prov:
                    note_lines.append(prov)
                continue

            if el.kind is ElementKind.chart:
                req = uir_by_id.get(el.request_id) if el.request_id else None
                src = _add_chart_element(slide, el, run_dir, req)
                prov, _ = _verify_and_collect_provenance(page, el, reg_by_id, uir_by_id, page_texts)
                note_lines.append(prov or f"Chart data source: {src}")
                continue

            if el.kind is ElementKind.table:
                req = uir_by_id.get(el.request_id) if el.request_id else None
                src = _add_table_element(slide, el, run_dir, req)
                prov, _ = _verify_and_collect_provenance(page, el, reg_by_id, uir_by_id, page_texts)
                note_lines.append(prov or f"Table data source: {src}")
                continue

            # Text element (title, bullet, body, caption, footer)
            _add_text_element(slide, el)

        # Also record provenance for any reference assets cited on this page
        for aid, a in reg_by_id.items():
            if a.type and a.type.value == "reference" and aid in page_texts:
                note_lines.append(f"[{aid}] {a.source_name}: {a.source_url} ({a.license_or_terms})")

        slide.notes_slide.notes_text_frame.text = "\n".join(note_lines)

    out_path = run_dir / ARTIFACT
    prs.save(str(out_path))

    # Mandatory re-open verification
    reopened = Presentation(str(out_path))
    if len(reopened.slides) != len(slide_plan.pages):
        raise DeckGenerationError(
            f"reopened {ARTIFACT} has {len(reopened.slides)} slides, expected {len(slide_plan.pages)}"
        )

    if rendered_placeholders:
        mark_placeholders_rendered(run_dir, rendered_placeholders)

    return out_path
