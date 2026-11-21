"""Pure-Python deterministic compiler: PageDesign -> DeckOps (deck_ops.json).

Runs identically on Linux and Windows with zero Office/COM dependencies.
Enforces no-fabrication rules at compile time:
  - Every visual op (picture, chart, table, diagram nodes/connectors) MUST bind
    to an acquired AssetRegistry entry (A###) or provided UserInputRequest (U###)
    with non-empty provenance source_refs and an existing relative file.
  - Every diagram node and edge MUST carry a non-empty 'source' field; nodes wrap
    into multiple rows when needed and raise DiagramOverflowError if they still
    cannot fit (never silently truncated).
  - CC BY / CC BY-SA assets without author/attribution raise AttributionMissingError.
"""

from __future__ import annotations

import csv
import io
import json
import math
from pathlib import Path
from typing import Any

from pptv01.contracts import (
    AssetRecord,
    AssetRegistry,
    AssetStatus,
    DesignElement,
    DeckOp,
    DeckOpKind,
    DeckOps,
    ElementKind,
    PageDesign,
    PageDesignEntry,
    SlidePlan,
    UserInputRequest,
    UserInputRequests,
    UserRequestStatus,
    write_contract,
)
from pptv01.d_retrieve.license import needs_attribution

ARTIFACT_OPS = "deck_ops.json"

MIN_NODE_W_IN = 1.15
MIN_NODE_H_IN = 0.48
NODE_GAP_X_IN = 0.28
NODE_GAP_Y_IN = 0.30
DIAGRAM_PAD_IN = 0.14


class OpsCompileError(ValueError):
    """Base error raised when PageDesign cannot be compiled into valid DeckOps."""


class UnboundVisualError(OpsCompileError):
    """Raised when a visual element lacks a verified A###/U### binding or source."""


class AttributionMissingError(OpsCompileError):
    """Raised when a CC BY / CC BY-SA asset lacks required attribution metadata."""


class DiagramOverflowError(OpsCompileError):
    """Raised when diagram nodes cannot fit inside the slot even after multi-row wrapping."""


def _bound_shape_name(prefix: str, el: DesignElement, suffix: str = "") -> str:
    tail = f"__{el.element_id}{suffix}"
    if el.request_id:
        if prefix == "ASSET":
            return f"INPUT_{el.request_id}{tail}"
        return f"{prefix}_INPUT_{el.request_id}{tail}"
    if el.asset_id:
        return f"{prefix}_{el.asset_id}{tail}"
    return f"{el.element_id}{suffix}"


def _resolve_binding(
    page_id: str,
    el: DesignElement,
    reg_by_id: dict[str, AssetRecord],
    uir_by_id: dict[str, UserInputRequest],
    run_dir: Path,
) -> tuple[Path, list[str], str, str]:
    """Validate visual binding and return (abs_file_path, source_refs, note_line, alt_text)."""
    if not el.asset_path:
        raise UnboundVisualError(f"{page_id}.{el.element_id}: visual element missing asset_path")
    if not (el.asset_id or el.request_id):
        raise UnboundVisualError(f"{page_id}.{el.element_id}: visual element must bind asset_id or request_id")

    file_path = run_dir / el.asset_path
    if not file_path.is_file():
        raise UnboundVisualError(f"{page_id}.{el.element_id}: bound file not found on disk: {el.asset_path}")

    req = uir_by_id.get(el.request_id) if el.request_id else None
    asset = reg_by_id.get(el.asset_id) if el.asset_id else None

    if el.request_id:
        if req is None or req.status is not UserRequestStatus.provided or req.provided is None:
            raise UnboundVisualError(
                f"{page_id}.{el.element_id}: request {el.request_id} is not provided (cannot render unbound visual)"
            )
        if req.provided.file != el.asset_path:
            raise UnboundVisualError(
                f"{page_id}.{el.element_id}: asset_path {el.asset_path!r} != provided file {req.provided.file!r}"
            )
        src = req.provided.source.strip()
        lic = req.provided.license.strip()
        if not src:
            raise UnboundVisualError(f"{page_id}.{el.element_id}: provided input {req.request_id} lacks source")
        note = f"[{req.request_id}] {req.provided.file} | 来源: {src} | 许可: {lic}"
        alt = f"User input {req.request_id}: {src} ({lic})"
        return file_path, [src], note, alt

    if asset is None or asset.status not in (AssetStatus.acquired, AssetStatus.substituted, AssetStatus.drawn):
        raise UnboundVisualError(
            f"{page_id}.{el.element_id}: asset {el.asset_id} is not acquired in asset_registry"
        )
    if asset.file_path != el.asset_path:
        raise UnboundVisualError(
            f"{page_id}.{el.element_id}: asset_path {el.asset_path!r} != registry file_path {asset.file_path!r}"
        )

    lic = (asset.license_or_terms or "").strip()
    src = (asset.source_url or asset.source_name or "").strip()
    if not src:
        raise UnboundVisualError(f"{page_id}.{el.element_id}: asset {asset.asset_id} lacks source_url/source_name")

    if needs_attribution(lic):
        if not (asset.attribution or asset.author):
            raise AttributionMissingError(
                f"{page_id}.{el.element_id}: asset {asset.asset_id} has licence {lic!r} requiring attribution, "
                "but both author and attribution are missing"
            )
        credit = asset.attribution or f"{asset.author}, {lic}, {src}"
        note = f"[{asset.asset_id}] {credit} | 许可: {lic} | 来源: {src}"
        alt = f"Asset {asset.asset_id}: {credit}"
    else:
        note = f"[{asset.asset_id}] 来源: {src} | 许可: {lic or 'N/A'}"
        alt = f"Asset {asset.asset_id}: {src} ({lic or 'N/A'})"

    return file_path, [src], note, alt


def _parse_chart_file(data_path: Path, fallback_source: str) -> tuple[list[str], list[dict[str, Any]], str, str]:
    ext = data_path.suffix.lower()
    if ext == ".csv":
        rows = [r for r in csv.reader(io.StringIO(data_path.read_text("utf-8-sig"))) if any(c.strip() for c in r)]
        if len(rows) < 2 or len(rows[0]) < 2:
            raise OpsCompileError(f"chart CSV {data_path.name} needs header + >=1 row and >=2 columns")
        cats = [r[0] for r in rows[1:]]
        series = []
        for col_idx in range(1, len(rows[0])):
            try:
                vals = [float(r[col_idx]) for r in rows[1:]]
            except ValueError as e:
                raise OpsCompileError(f"non-numeric value in chart CSV {data_path.name}: {e}") from None
            series.append({"name": rows[0][col_idx], "values": vals})
        if not fallback_source:
            raise UnboundVisualError(f"chart CSV {data_path.name} lacks provenance source")
        return cats, series, fallback_source, ""

    obj = json.loads(data_path.read_text("utf-8"))
    if not isinstance(obj, dict):
        raise OpsCompileError(f"chart JSON {data_path.name} must be an object")
    title = str(obj.get("title") or "").strip()
    root_src = str(obj.get("source") or "").strip()

    raw_series = obj.get("series")
    if isinstance(raw_series, list) and raw_series:
        first_labels = raw_series[0].get("labels") or obj.get("labels")
        n_pts = len(raw_series[0].get("values", []))
        cats = [str(x) for x in first_labels] if isinstance(first_labels, list) and len(first_labels) == n_pts else [str(i + 1) for i in range(n_pts)]
        series = []
        sources: list[str] = []
        for s in raw_series:
            vals_raw = s.get("values")
            if not isinstance(vals_raw, list) or not vals_raw:
                raise OpsCompileError(f"chart series in {data_path.name} missing values")
            if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals_raw):
                raise OpsCompileError(f"chart series in {data_path.name} has non-numeric values")
            s_src = str(s.get("source") or root_src or fallback_source).strip()
            if not s_src:
                raise UnboundVisualError(f"chart series in {data_path.name} lacks source attribution")
            sources.append(s_src)
            series.append({"name": str(s.get("name") or "Series"), "values": [float(v) for v in vals_raw]})
        return cats, series, "; ".join(dict.fromkeys(sources)), title

    labels, values = obj.get("labels"), obj.get("values")
    if isinstance(labels, list) and isinstance(values, list) and values and len(labels) == len(values):
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
            raise OpsCompileError(f"chart JSON {data_path.name} has non-numeric values")
        src = root_src or fallback_source
        if not src:
            raise UnboundVisualError(f"chart JSON {data_path.name} lacks source attribution")
        return [str(x) for x in labels], [{"name": title or "Value", "values": [float(v) for v in values]}], src, title

    raise OpsCompileError(f"unsupported chart JSON schema in {data_path.name}")


def _parse_table_file(data_path: Path, fallback_source: str) -> tuple[list[str], list[list[str]], str]:
    ext = data_path.suffix.lower()
    if ext == ".csv":
        rows = [r for r in csv.reader(io.StringIO(data_path.read_text("utf-8-sig"))) if any(c.strip() for c in r)]
        if len(rows) < 2 or not rows[0]:
            raise OpsCompileError(f"table CSV {data_path.name} needs header and >=1 row")
        if not fallback_source:
            raise UnboundVisualError(f"table CSV {data_path.name} lacks provenance source")
        return [str(c) for c in rows[0]], [[str(c) for c in r] for r in rows[1:]], fallback_source

    obj = json.loads(data_path.read_text("utf-8"))
    if not isinstance(obj, dict):
        raise OpsCompileError(f"table JSON {data_path.name} must be an object")
    cols, rows = obj.get("columns"), obj.get("rows")
    if not isinstance(cols, list) or not cols or not isinstance(rows, list) or not rows:
        raise OpsCompileError(f"table JSON {data_path.name} requires non-empty columns and rows")
    src = str(obj.get("source") or fallback_source).strip()
    if not src:
        raise UnboundVisualError(f"table JSON {data_path.name} lacks source attribution")
    return [str(c) for c in cols], [[str(c) for c in r] for r in rows], src


def _layout_diagram_nodes(
    n_nodes: int, x_in: float, y_in: float, w_in: float, h_in: float
) -> tuple[list[tuple[float, float, float, float, int, int]], int, int]:
    """Compute a multi-row grid for diagram nodes inside the slot.

    Returns (list of (nx, ny, nw, nh, row_idx, col_idx), rows, cols).
    Raises DiagramOverflowError if nodes cannot fit at minimum readable size.
    """
    inner_w = w_in - 2 * DIAGRAM_PAD_IN
    inner_h = h_in - 2 * DIAGRAM_PAD_IN
    if inner_w < MIN_NODE_W_IN or inner_h < MIN_NODE_H_IN:
        raise DiagramOverflowError(
            f"diagram slot ({w_in:.2f}x{h_in:.2f} in) is too small for even 1 readable node"
        )

    best: tuple[int, int, float, float] | None = None
    for rows in range(1, n_nodes + 1):
        cols = math.ceil(n_nodes / rows)
        avail_w = inner_w - NODE_GAP_X_IN * max(cols - 1, 0)
        avail_h = inner_h - NODE_GAP_Y_IN * max(rows - 1, 0)
        if avail_w <= 0 or avail_h <= 0:
            continue
        node_w = avail_w / cols
        node_h = avail_h / rows
        if node_w >= MIN_NODE_W_IN and node_h >= MIN_NODE_H_IN:
            # Cap box height for visual neatness while centering vertically in row band
            box_h = min(node_h, 1.05)
            best = (rows, cols, node_w, box_h)
            break

    if best is None:
        raise DiagramOverflowError(
            f"diagram has {n_nodes} nodes which cannot fit in slot ({w_in:.2f}x{h_in:.2f} in) "
            f"even with multi-row wrapping (min node {MIN_NODE_W_IN}x{MIN_NODE_H_IN} in); "
            "split across slides or enlarge the diagram slot"
        )

    rows, cols, node_w, box_h = best
    total_grid_h = rows * box_h + max(rows - 1, 0) * NODE_GAP_Y_IN
    start_y = y_in + DIAGRAM_PAD_IN + max(0.0, (inner_h - total_grid_h) / 2.0)

    boxes: list[tuple[float, float, float, float, int, int]] = []
    for idx in range(n_nodes):
        r_idx, c_idx = divmod(idx, cols)
        # Number of items in this row (last row may be shorter -> center it horizontally)
        row_count = cols if r_idx < rows - 1 else (n_nodes - r_idx * cols)
        row_w = row_count * node_w + max(row_count - 1, 0) * NODE_GAP_X_IN
        start_x = x_in + DIAGRAM_PAD_IN + max(0.0, (inner_w - row_w) / 2.0)
        nx = round(start_x + c_idx * (node_w + NODE_GAP_X_IN), 3)
        ny = round(start_y + r_idx * (box_h + NODE_GAP_Y_IN), 3)
        boxes.append((nx, ny, round(node_w, 3), round(box_h, 3), r_idx, c_idx))
    return boxes, rows, cols


def _compile_diagram_element(
    page_id: str,
    el: DesignElement,
    data_path: Path,
    base_sources: list[str],
    next_op_id: Any,
) -> tuple[list[DeckOp], list[str]]:
    """Compile a verified diagram JSON into node add_shape + edge add_connector ops."""
    obj = json.loads(data_path.read_text("utf-8"))
    if not isinstance(obj, dict):
        raise OpsCompileError(f"{page_id}.{el.element_id}: diagram JSON must be an object")
    nodes = obj.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise OpsCompileError(f"{page_id}.{el.element_id}: diagram JSON must contain non-empty 'nodes'")
    edges = obj.get("edges", [])
    if not isinstance(edges, list):
        raise OpsCompileError(f"{page_id}.{el.element_id}: diagram JSON 'edges' must be a list")

    # Strict provenance check for every node and every edge (B5 fix)
    node_ids: set[str] = set()
    all_sources: list[str] = list(base_sources)
    for i, n in enumerate(nodes):
        if not isinstance(n, dict) or not str(n.get("id", "")).strip() or not str(n.get("label", "")).strip():
            raise OpsCompileError(f"{page_id}.{el.element_id}: node[{i}] must have non-empty id and label")
        nid = str(n["id"]).strip()
        if nid in node_ids:
            raise OpsCompileError(f"{page_id}.{el.element_id}: duplicate diagram node id {nid!r}")
        node_ids.add(nid)
        nsrc = str(n.get("source", "")).strip()
        if not nsrc:
            raise UnboundVisualError(f"{page_id}.{el.element_id}: diagram node {nid!r} missing required 'source'")
        if nsrc not in all_sources:
            all_sources.append(nsrc)

    for j, e in enumerate(edges):
        if not isinstance(e, dict) or not str(e.get("from", "")).strip() or not str(e.get("to", "")).strip():
            raise OpsCompileError(f"{page_id}.{el.element_id}: edge[{j}] must have non-empty 'from' and 'to'")
        frm, to = str(e["from"]).strip(), str(e["to"]).strip()
        if frm not in node_ids or to not in node_ids:
            raise OpsCompileError(
                f"{page_id}.{el.element_id}: edge[{j}] ({frm}->{to}) references unknown node id"
            )
        esrc = str(e.get("source", "")).strip()
        if not esrc:
            raise UnboundVisualError(f"{page_id}.{el.element_id}: diagram edge {frm}->{to} missing required 'source'")
        if esrc not in all_sources:
            all_sources.append(esrc)

    boxes, rows, _cols = _layout_diagram_nodes(len(nodes), el.x_in, el.y_in, el.w_in, el.h_in)

    ops: list[DeckOp] = []
    node_el_map: dict[str, tuple[str, int, int]] = {}  # node_id -> (shape_name, row_idx, col_idx)
    node_font = 11.5 if len(nodes) <= 6 else 10.0

    for idx, (n, (nx, ny, nw, nh, r_idx, c_idx)) in enumerate(zip(nodes, boxes), start=1):
        nid = str(n["id"]).strip()
        label = str(n["label"]).strip()
        nsrc = str(n["source"]).strip()
        node_el_id = f"{el.element_id}_node_{idx}"
        shape_name = _bound_shape_name("DIAGRAM", el, f"_node_{idx}")
        node_el_map[nid] = (shape_name, r_idx, c_idx)
        node_refs = list(dict.fromkeys([nsrc, *base_sources]))
        ops.append(
            DeckOp(
                op_id=next_op_id(),
                page_id=page_id,
                kind=DeckOpKind.add_shape,
                element_id=node_el_id,
                asset_id=el.asset_id,
                request_id=el.request_id,
                source_refs=node_refs,
                params={
                    "shape_name": shape_name,
                    "shape_type": "rounded_rectangle",
                    "x_in": nx,
                    "y_in": ny,
                    "w_in": nw,
                    "h_in": nh,
                    "fill_hex": "#EFF6FF",
                    "line_hex": "#1E3A8A",
                    "line_width_pt": 1.25,
                    "text": label,
                    "font": el.font or "Microsoft YaHei",
                    "font_size_pt": node_font,
                    "bold": False,
                    "color_hex": "#0F172A",
                    "align": "center",
                    "diagram_node_id": nid,
                },
            )
        )

    for j, e in enumerate(edges, start=1):
        frm, to = str(e["from"]).strip(), str(e["to"]).strip()
        elabel = str(e.get("label") or "").strip()
        esrc = str(e["source"]).strip()
        from_shape, r1, c1 = node_el_map[frm]
        to_shape, r2, c2 = node_el_map[to]
        conn_el_id = f"{el.element_id}_edge_{j}"
        conn_name = _bound_shape_name("DIAGRAM", el, f"_edge_{j}")
        # Pick connector style and natural attachment sites (PowerPoint rect sites: 1=top, 2=left, 3=bottom, 4=right)
        if r1 == r2 and c2 > c1:
            begin_site, end_site, conn_type = 4, 2, "straight"
        elif r1 == r2 and c2 < c1:
            begin_site, end_site, conn_type = 2, 4, "straight"
        elif r2 > r1:
            begin_site, end_site, conn_type = 3, 1, "elbow" if rows > 1 else "straight"
        else:
            begin_site, end_site, conn_type = 1, 3, "elbow"

        edge_refs = list(dict.fromkeys([esrc, *base_sources]))
        ops.append(
            DeckOp(
                op_id=next_op_id(),
                page_id=page_id,
                kind=DeckOpKind.add_connector,
                element_id=conn_el_id,
                asset_id=el.asset_id,
                request_id=el.request_id,
                source_refs=edge_refs,
                params={
                    "shape_name": conn_name,
                    "from_element_id": from_shape,
                    "to_element_id": to_shape,
                    "begin_site": begin_site,
                    "end_site": end_site,
                    "connector_type": conn_type,
                    "arrow_style": "triangle",
                    "line_hex": "#1E3A8A",
                    "line_width_pt": 1.5,
                    "label": elabel,
                },
            )
        )

    return ops, all_sources


def compile_deck_ops(
    slide_plan: SlidePlan,
    design: PageDesign,
    reg: AssetRegistry,
    uir: UserInputRequests | None,
    run_dir: Path,
    *,
    template_path: str | None = None,
    write_file: bool = True,
) -> DeckOps:
    """Deterministically compile PageDesign into DeckOps (deck_ops.json)."""
    run_dir = Path(run_dir)
    reg_by_id = {a.asset_id: a for a in reg.assets}
    uir_by_id = {r.request_id: r for r in uir.requests} if uir else {}
    sp_by_id = {p.page_id: p for p in slide_plan.pages}

    want_pages = [p.page_id for p in slide_plan.pages]
    got_pages = [p.page_id for p in design.pages]
    if got_pages != want_pages:
        raise OpsCompileError(f"design pages {got_pages} do not match slide_plan pages {want_pages}")

    op_counter = 0

    def next_op_id() -> str:
        nonlocal op_counter
        op_counter += 1
        return f"OP{op_counter:04d}"

    ops: list[DeckOp] = []
    canvas_w = design.pages[0].canvas_width_in
    canvas_h = design.pages[0].canvas_height_in

    for page in design.pages:
        has_title = any(e.kind is ElementKind.title for e in page.elements)
        slide_layout = "title_only" if has_title else "blank"
        page_layout_id = page.layout_id or page.layout_name
        ops.append(
            DeckOp(
                op_id=next_op_id(),
                page_id=page.page_id,
                kind=DeckOpKind.add_slide,
                params={
                    "layout": slide_layout,
                    "layout_id": page_layout_id,
                    "layout_name": page.layout_name or page_layout_id,
                },
            )
        )

        note_lines: list[str] = [f"Page {page.page_id}"]
        sp_page = sp_by_id.get(page.page_id)
        if sp_page and sp_page.content_summary.strip():
            note_lines.append(f"Speaker/planning summary: {sp_page.content_summary.strip()}")
        if page.notes:
            note_lines.append(f"Layout notes: {page.notes}")

        page_sources: list[str] = []
        page_texts = "\n".join(el.text or "" for el in page.elements)

        # Deterministic z-order, tie-broken by element_id
        for el in sorted(page.elements, key=lambda e: (e.z_index, e.element_id)):
            if el.kind is ElementKind.title:
                ops.append(
                    DeckOp(
                        op_id=next_op_id(),
                        page_id=page.page_id,
                        kind=DeckOpKind.set_placeholder_text,
                        element_id=el.element_id,
                        params={
                            "placeholder_type": "title",
                            "shape_name": el.element_id,
                            "text": el.text or "",
                            "x_in": el.x_in,
                            "y_in": el.y_in,
                            "w_in": el.w_in,
                            "h_in": el.h_in,
                            "font": el.font or "Microsoft YaHei",
                            "font_size_pt": el.font_size_pt or 28.0,
                            "bold": bool(el.bold),
                            "color_hex": el.color_hex or "#1E3A8A",
                            "align": el.align,
                        },
                    )
                )
                continue

            if el.kind is ElementKind.placeholder:
                if not el.request_id:
                    raise OpsCompileError(f"{page.page_id}.{el.element_id}: placeholder missing request_id")
                ops.append(
                    DeckOp(
                        op_id=next_op_id(),
                        page_id=page.page_id,
                        kind=DeckOpKind.add_placeholder_box,
                        element_id=el.element_id,
                        asset_id=el.asset_id,
                        request_id=el.request_id,
                        params={
                            "shape_name": el.element_id,
                            "text": el.text or "",
                            "x_in": el.x_in,
                            "y_in": el.y_in,
                            "w_in": el.w_in,
                            "h_in": el.h_in,
                            "font": el.font or "Microsoft YaHei",
                            "font_size_pt": el.font_size_pt or 11.5,
                            "bold": bool(el.bold),
                            "color_hex": el.color_hex or "#92400E",
                            "fill_hex": el.fill_hex or "#FFFBEB",
                            "line_hex": el.line_hex or "#D97706",
                            "line_width_pt": 1.25 if el.role == "compact_placeholder" else 2.0,
                            "align": el.align or "left",
                            "dash_style": "dash",
                        },
                    )
                )
                note_lines.append(f"待补充占位 {el.request_id}: {el.text}")
                continue

            if el.kind is ElementKind.shape:
                if el.role == "diagram_json":
                    data_path, base_srcs, note, alt = _resolve_binding(page.page_id, el, reg_by_id, uir_by_id, run_dir)
                    diag_ops, diag_srcs = _compile_diagram_element(page.page_id, el, data_path, base_srcs, next_op_id)
                    ops.extend(diag_ops)
                    note_lines.append(note)
                    note_lines.append(f"Diagram {el.request_id or el.asset_id} sources: {', '.join(diag_srcs)}")
                    page_sources.extend(diag_srcs)
                    if diag_ops:
                        first_node_name = diag_ops[0].params.get("shape_name", "")
                        ops.append(
                            DeckOp(
                                op_id=next_op_id(),
                                page_id=page.page_id,
                                kind=DeckOpKind.set_alt_text,
                                element_id=diag_ops[0].element_id,
                                asset_id=el.asset_id,
                                request_id=el.request_id,
                                source_refs=diag_srcs,
                                params={"target_element_id": diag_ops[0].element_id, "shape_name": first_node_name, "alt_text": alt},
                            )
                        )
                else:
                    ops.append(
                        DeckOp(
                            op_id=next_op_id(),
                            page_id=page.page_id,
                            kind=DeckOpKind.add_shape,
                            element_id=el.element_id,
                            params={
                                "shape_name": el.element_id,
                                "shape_type": "rounded_rectangle" if "card" in el.role else "rectangle",
                                "x_in": el.x_in,
                                "y_in": el.y_in,
                                "w_in": el.w_in,
                                "h_in": el.h_in,
                                "fill_hex": el.fill_hex,
                                "line_hex": el.line_hex,
                                "role": el.role,
                                "z_index": el.z_index,
                            },
                        )
                    )
                continue

            if el.kind is ElementKind.image:
                data_path, srcs, note, alt = _resolve_binding(page.page_id, el, reg_by_id, uir_by_id, run_dir)
                if data_path.suffix.lower() == ".svg":
                    raise OpsCompileError(
                        f"{page.page_id}.{el.element_id}: SVG embedding requires rasterization before Office COM"
                    )
                shape_name = _bound_shape_name("ASSET", el)
                ops.append(
                    DeckOp(
                        op_id=next_op_id(),
                        page_id=page.page_id,
                        kind=DeckOpKind.add_picture,
                        element_id=el.element_id,
                        asset_id=el.asset_id,
                        request_id=el.request_id,
                        source_refs=srcs,
                        params={
                            "shape_name": shape_name,
                            "file_path": el.asset_path,
                            "x_in": el.x_in,
                            "y_in": el.y_in,
                            "w_in": el.w_in,
                            "h_in": el.h_in,
                            "crop_mode": "crop_fill",
                        },
                    )
                )
                ops.append(
                    DeckOp(
                        op_id=next_op_id(),
                        page_id=page.page_id,
                        kind=DeckOpKind.set_alt_text,
                        element_id=el.element_id,
                        asset_id=el.asset_id,
                        request_id=el.request_id,
                        source_refs=srcs,
                        params={"target_element_id": el.element_id, "shape_name": shape_name, "alt_text": alt},
                    )
                )
                note_lines.append(note)
                page_sources.extend(srcs)
                continue

            if el.kind is ElementKind.chart:
                data_path, srcs, note, alt = _resolve_binding(page.page_id, el, reg_by_id, uir_by_id, run_dir)
                cats, series, chart_src, chart_title = _parse_chart_file(data_path, srcs[0])
                all_refs = list(dict.fromkeys([chart_src, *srcs]))
                shape_name = _bound_shape_name("CHART", el)
                ops.append(
                    DeckOp(
                        op_id=next_op_id(),
                        page_id=page.page_id,
                        kind=DeckOpKind.add_chart,
                        element_id=el.element_id,
                        asset_id=el.asset_id,
                        request_id=el.request_id,
                        source_refs=all_refs,
                        params={
                            "shape_name": shape_name,
                            "file_path": el.asset_path,
                            "chart_type": "column",
                            "title": chart_title,
                            "categories": cats,
                            "series": series,
                            "x_in": el.x_in,
                            "y_in": el.y_in,
                            "w_in": el.w_in,
                            "h_in": el.h_in,
                        },
                    )
                )
                ops.append(
                    DeckOp(
                        op_id=next_op_id(),
                        page_id=page.page_id,
                        kind=DeckOpKind.set_alt_text,
                        element_id=el.element_id,
                        asset_id=el.asset_id,
                        request_id=el.request_id,
                        source_refs=all_refs,
                        params={"target_element_id": el.element_id, "shape_name": shape_name, "alt_text": alt},
                    )
                )
                note_lines.append(note)
                page_sources.extend(all_refs)
                continue

            if el.kind is ElementKind.table:
                data_path, srcs, note, alt = _resolve_binding(page.page_id, el, reg_by_id, uir_by_id, run_dir)
                cols, rows, tbl_src = _parse_table_file(data_path, srcs[0])
                all_refs = list(dict.fromkeys([tbl_src, *srcs]))
                shape_name = _bound_shape_name("TABLE", el)
                ops.append(
                    DeckOp(
                        op_id=next_op_id(),
                        page_id=page.page_id,
                        kind=DeckOpKind.add_table,
                        element_id=el.element_id,
                        asset_id=el.asset_id,
                        request_id=el.request_id,
                        source_refs=all_refs,
                        params={
                            "shape_name": shape_name,
                            "file_path": el.asset_path,
                            "columns": cols,
                            "rows": rows,
                            "x_in": el.x_in,
                            "y_in": el.y_in,
                            "w_in": el.w_in,
                            "h_in": el.h_in,
                        },
                    )
                )
                ops.append(
                    DeckOp(
                        op_id=next_op_id(),
                        page_id=page.page_id,
                        kind=DeckOpKind.set_alt_text,
                        element_id=el.element_id,
                        asset_id=el.asset_id,
                        request_id=el.request_id,
                        source_refs=all_refs,
                        params={"target_element_id": el.element_id, "shape_name": shape_name, "alt_text": alt},
                    )
                )
                note_lines.append(note)
                page_sources.extend(all_refs)
                continue

            # Text elements: bullet, body, caption, footer
            ops.append(
                DeckOp(
                    op_id=next_op_id(),
                    page_id=page.page_id,
                    kind=DeckOpKind.add_textbox,
                    element_id=el.element_id,
                    params={
                        "shape_name": el.element_id,
                        "text": el.text or "",
                        "x_in": el.x_in,
                        "y_in": el.y_in,
                        "w_in": el.w_in,
                        "h_in": el.h_in,
                        "font": el.font or "Microsoft YaHei",
                        "font_size_pt": el.font_size_pt or 16.0,
                        "bold": bool(el.bold),
                        "color_hex": el.color_hex or "#1E293B",
                        "align": el.align,
                        "role": el.role,
                    },
                )
            )

        # Record provenance for any reference assets cited on this page
        for aid, a in reg_by_id.items():
            if a.type and a.type.value == "reference" and aid in page_texts:
                note_lines.append(f"[{aid}] {a.source_name}: {a.source_url} ({a.license_or_terms})")
                if a.source_url:
                    page_sources.append(a.source_url)

        ops.append(
            DeckOp(
                op_id=next_op_id(),
                page_id=page.page_id,
                kind=DeckOpKind.set_notes,
                source_refs=list(dict.fromkeys(page_sources)),
                params={"text": "\n".join(note_lines)},
            )
        )

    deck_ops = DeckOps(
        run_id=design.run_id,
        canvas_width_in=canvas_w,
        canvas_height_in=canvas_h,
        template_path=template_path,
        page_ids=[p.page_id for p in design.pages],
        ops=ops,
    )
    if write_file:
        write_contract(deck_ops, run_dir / ARTIFACT_OPS)
    return deck_ops
