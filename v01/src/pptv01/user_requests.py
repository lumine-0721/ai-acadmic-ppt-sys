"""Deterministic user-input request generation, validation, and CLI support (plan section 3).

Missing assets from stage D are never fabricated. Instead, stage D deterministically emits
``user_input_requests.json`` so the user can either:
  - provide verified files/structured data via ``pptv01 provide`` (--on-missing wait, exit 4), or
  - render explicitly marked ``PLACEHOLDER_U###`` boxes in the deck (--on-missing placeholder).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from pathlib import Path
from typing import Literal

from PIL import Image

from pptv01.contracts import (
    AssetRegistry,
    AssetRequirements,
    AssetStatus,
    AssetType,
    Priority,
    ProvidedInput,
    UserInputRequest,
    UserInputRequests,
    UserInputRequestsSummary,
    UserRequestFallback,
    UserRequestKind,
    UserRequestStatus,
    load_contract,
    write_contract,
)

USER_INPUT_REQUESTS_FILE = "user_input_requests.json"

_KIND_MAP: dict[AssetType, UserRequestKind] = {
    AssetType.diagram: UserRequestKind.diagram,
    AssetType.chart_data: UserRequestKind.chart,
    AssetType.statistic: UserRequestKind.statistic,
    AssetType.table: UserRequestKind.table,
    AssetType.icon: UserRequestKind.icon,
    AssetType.image: UserRequestKind.image,
    AssetType.reference: UserRequestKind.reference,
}

_FORMAT_WANTED: dict[UserRequestKind, str] = {
    UserRequestKind.diagram: (
        "二选一：①结构化 JSON {nodes:[{id,label,source}],edges:[{from,to,label,source}]}；"
        "②PNG/SVG ≥1280px，注明来源与许可"
    ),
    UserRequestKind.chart: "结构化 JSON {title,x_label,y_label,series:[{name,labels,values,source}]} 或带出处 CSV，注明来源与许可",
    UserRequestKind.statistic: "结构化 JSON {value,unit,label,source,year} 或 CSV，注明数据集来源 URL/DOI 与许可",
    UserRequestKind.table: "结构化 JSON {columns:[...],rows:[[...]],source} 或 CSV，注明来源 URL/DOI 与许可",
    UserRequestKind.icon: "PNG/SVG 图标（≥256px 或矢量 SVG），注明来源与许可",
    UserRequestKind.image: "PNG/JPG/WEBP/SVG 图片（≥1280px），注明来源 URL 与许可（如 CC BY 4.0 / CC0）",
    UserRequestKind.reference: "JSON {title,authors,year,venue,doi,url,source}，必须包含可核验 DOI 或出版物来源",
    UserRequestKind.other: "JSON/CSV/PNG/SVG 文件，注明来源与许可",
}

_RASTER_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
_IMAGE_EXTS = _RASTER_EXTS | {".svg"}
_DATA_EXTS = {".json", ".csv"}


def _format_summary(requests: list[UserInputRequest]) -> str:
    open_reqs = [r for r in requests if r.status is UserRequestStatus.open]
    blocking = sum(1 for r in open_reqs if r.blocking)
    provided = sum(1 for r in requests if r.status is UserRequestStatus.provided)
    ph = sum(1 for r in requests if r.status is UserRequestStatus.placeholder_rendered)
    parts = [f"{len(open_reqs)} open ({blocking} blocking)"]
    if provided:
        parts.append(f"{provided} provided")
    if ph:
        parts.append(f"{ph} placeholder_rendered")
    return ", ".join(parts)


def summarize_requests(uir: UserInputRequests) -> UserInputRequestsSummary:
    open_reqs = [r for r in uir.requests if r.status is UserRequestStatus.open]
    return UserInputRequestsSummary(
        path=USER_INPUT_REQUESTS_FILE,
        open_count=len(open_reqs),
        blocking_open_count=sum(1 for r in open_reqs if r.blocking),
        provided_count=sum(1 for r in uir.requests if r.status is UserRequestStatus.provided),
        placeholder_count=sum(1 for r in uir.requests if r.status is UserRequestStatus.placeholder_rendered),
    )


def build_user_input_requests(
    reqs: AssetRequirements,
    reg: AssetRegistry,
    *,
    on_missing: Literal["placeholder", "wait"] = "placeholder",
) -> UserInputRequests:
    """Deterministically build user_input_requests.json from D's missing records (no LLM)."""
    req_by_id = {r.asset_id: r for r in reqs.requirements}
    fallback = UserRequestFallback.wait if on_missing == "wait" else UserRequestFallback.placeholder
    items: list[UserInputRequest] = []
    idx = 0
    for asset in reg.assets:
        if asset.status is not AssetStatus.missing:
            continue
        idx += 1
        rq = req_by_id[asset.asset_id]
        kind = _KIND_MAP.get(asset.type or rq.type, UserRequestKind.other)
        items.append(
            UserInputRequest(
                request_id=f"U{idx:03d}",
                kind=kind,
                asset_id=asset.asset_id,
                page_ids=list(rq.page_ids),
                need=rq.purpose,
                why=asset.notes or f"D 未找到可标来源的 {rq.type.value} 素材；不允许凭空生成",
                format_wanted=_FORMAT_WANTED[kind],
                acceptance=list(rq.acceptance),
                blocking=rq.priority is Priority.required,
                fallback=fallback,
                status=UserRequestStatus.open,
            )
        )
    return UserInputRequests(
        run_id=reg.run_id,
        requests=items,
        summary=_format_summary(items),
    )


def _verify_image_bytes(data: bytes, ext: str) -> None:
    if ext == ".svg":
        if b"<svg" not in data[:4096].lower():
            raise ValueError("SVG file does not contain an <svg> root element")
        return
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"image file is unreadable ({type(e).__name__})") from None


def _parse_json_dict(data: bytes) -> dict:
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"invalid UTF-8 JSON: {e}") from None
    if not isinstance(obj, dict):
        raise ValueError("JSON payload must be a JSON object")
    return obj


def _parse_csv_rows(data: bytes) -> list[list[str]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise ValueError(f"CSV file is not valid UTF-8: {e}") from None
    rows = [r for r in csv.reader(io.StringIO(text)) if any(cell.strip() for cell in r)]
    if len(rows) < 2 or len(rows[0]) < 1:
        raise ValueError("CSV must contain a header row and at least one data row")
    return rows


def _validate_provided_payload(kind: UserRequestKind, ext: str, data: bytes) -> None:
    if not data:
        raise ValueError("provided file is empty")
    if kind in (UserRequestKind.image, UserRequestKind.icon):
        if ext not in _IMAGE_EXTS:
            raise ValueError(f"kind={kind.value} requires an image file ({sorted(_IMAGE_EXTS)}), got {ext!r}")
        _verify_image_bytes(data, ext)
        return
    if kind is UserRequestKind.diagram:
        if ext in _IMAGE_EXTS:
            _verify_image_bytes(data, ext)
            return
        if ext != ".json":
            raise ValueError(f"kind=diagram requires .json or image ({sorted(_IMAGE_EXTS)}), got {ext!r}")
        obj = _parse_json_dict(data)
        nodes = obj.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            raise ValueError("diagram JSON must contain a non-empty 'nodes' list")
        for n in nodes:
            if not isinstance(n, dict) or not str(n.get("id", "")).strip() or not str(n.get("label", "")).strip():
                raise ValueError("each diagram node must have non-empty 'id' and 'label'")
            if not str(n.get("source", "")).strip():
                raise ValueError("each diagram node must carry a 'source' attribution field")
        edges = obj.get("edges", [])
        if not isinstance(edges, list):
            raise ValueError("diagram JSON 'edges' must be a list")
        for e in edges:
            if not isinstance(e, dict) or not str(e.get("from", "")).strip() or not str(e.get("to", "")).strip():
                raise ValueError("each diagram edge must have 'from' and 'to'")
            if not str(e.get("source", "")).strip():
                raise ValueError("each diagram edge must carry a 'source' attribution field")
        return
    if kind is UserRequestKind.chart:
        if ext == ".csv":
            _parse_csv_rows(data)
            return
        if ext != ".json":
            raise ValueError(f"kind=chart requires .json or .csv structured data, got {ext!r}")
        obj = _parse_json_dict(data)
        series = obj.get("series")
        if isinstance(series, list) and series:
            for s in series:
                if not isinstance(s, dict) or not isinstance(s.get("values"), list) or not s["values"]:
                    raise ValueError("each chart series must have a non-empty 'values' list")
                if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in s["values"]):
                    raise ValueError("chart series values must be numeric")
                if not (str(s.get("source", "")).strip() or str(obj.get("source", "")).strip()):
                    raise ValueError("chart series or root object must specify 'source'")
            return
        labels, values = obj.get("labels"), obj.get("values")
        if isinstance(labels, list) and isinstance(values, list) and values and len(labels) == len(values):
            if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
                raise ValueError("chart values must be numeric")
            if not str(obj.get("source", "")).strip():
                raise ValueError("chart JSON must specify 'source'")
            return
        raise ValueError("chart JSON must contain 'series' or matching 'labels'+'values' with numeric data and 'source'")
    if kind is UserRequestKind.statistic:
        if ext == ".csv":
            _parse_csv_rows(data)
            return
        if ext != ".json":
            raise ValueError(f"kind=statistic requires .json or .csv, got {ext!r}")
        obj = _parse_json_dict(data)
        if "value" not in obj and not (isinstance(obj.get("items"), list) and obj["items"]):
            raise ValueError("statistic JSON must contain 'value' or non-empty 'items'")
        if not str(obj.get("source", "")).strip():
            raise ValueError("statistic JSON must specify 'source'")
        return
    if kind is UserRequestKind.table:
        if ext == ".csv":
            _parse_csv_rows(data)
            return
        if ext != ".json":
            raise ValueError(f"kind=table requires .json or .csv, got {ext!r}")
        obj = _parse_json_dict(data)
        cols, rows = obj.get("columns"), obj.get("rows")
        if not isinstance(cols, list) or not cols or not isinstance(rows, list) or not rows:
            raise ValueError("table JSON must contain non-empty 'columns' and 'rows'")
        if not str(obj.get("source", "")).strip():
            raise ValueError("table JSON must specify 'source'")
        return
    if kind is UserRequestKind.reference:
        if ext != ".json":
            raise ValueError(f"kind=reference requires .json, got {ext!r}")
        obj = _parse_json_dict(data)
        if not str(obj.get("title", "")).strip():
            raise ValueError("reference JSON must contain 'title'")
        if not (str(obj.get("doi", "")).strip() or str(obj.get("url", "")).strip() or str(obj.get("source", "")).strip()):
            raise ValueError("reference JSON must contain 'doi', 'url', or 'source'")
        return
    # other
    if ext not in (_IMAGE_EXTS | _DATA_EXTS | {".txt"}):
        raise ValueError(f"unsupported file extension {ext!r} for kind=other")


def provide_user_input(
    run_dir: Path,
    request_id: str,
    file_path: Path,
    *,
    source: str,
    license_str: str,
    note: str = "",
) -> UserInputRequests:
    """Validate a user-supplied asset/data file, copy it to inputs/, and update user_input_requests.json."""
    run_dir = Path(run_dir)
    req_path = run_dir / USER_INPUT_REQUESTS_FILE
    if not req_path.is_file():
        raise FileNotFoundError(f"{USER_INPUT_REQUESTS_FILE} not found in {run_dir}")
    source = (source or "").strip()
    license_str = (license_str or "").strip()
    if not source:
        raise ValueError("--source is required and cannot be empty")
    if not license_str:
        raise ValueError("--license is required and cannot be empty")
    if re.search(r"\b(fair use|all rights reserved)\b", license_str, re.I):
        raise ValueError(f"license {license_str!r} is not allowed")

    src_file = Path(file_path)
    if not src_file.is_file():
        raise FileNotFoundError(f"provided file not found: {src_file}")

    uir = load_contract(UserInputRequests, req_path)
    target = next((r for r in uir.requests if r.request_id == request_id), None)
    if target is None:
        raise ValueError(f"request {request_id!r} not found in {USER_INPUT_REQUESTS_FILE}")

    ext = src_file.suffix.lower()
    data = src_file.read_bytes()
    _validate_provided_payload(target.kind, ext, data)

    sha = hashlib.sha256(data).hexdigest()
    inputs_dir = run_dir / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    rel = f"inputs/{target.request_id}{ext}"
    (run_dir / rel).write_bytes(data)

    provided = ProvidedInput(
        file=rel,
        sha256=sha,
        source=source,
        license=license_str,
        note=note.strip(),
    )
    updated = UserInputRequest.model_validate({
        **target.model_dump(),
        "status": UserRequestStatus.provided,
        "provided": provided.model_dump(),
    })
    uir.requests = [updated if r.request_id == target.request_id else r for r in uir.requests]
    uir.summary = _format_summary(uir.requests)
    write_contract(uir, req_path)
    _mark_downstream_stages_stale(
        run_dir,
        stages=("E", "F", "G", "H"),
        reason=f"user provided {target.request_id} ({rel}); downstream stages must re-run on --resume",
    )
    return uir


def _mark_downstream_stages_stale(run_dir: Path, *, stages: tuple[str, ...], reason: str) -> None:
    """Mark downstream stages in run_report.json as stale so --resume re-executes them."""
    from pptv01.contracts import RunReport

    rep_path = Path(run_dir) / "run_report.json"
    if not rep_path.is_file():
        return
    report = load_contract(RunReport, rep_path)
    changed = False
    stage_set = set(stages)
    for rec in report.stages:
        if rec.stage in stage_set:
            rec.stale = True
            rec.stale_reason = reason
            changed = True
    if changed:
        write_contract(report, rep_path)


def mark_placeholders_rendered(run_dir: Path, rendered_ids: set[str]) -> UserInputRequests | None:
    """Update open requests rendered as placeholders to status=placeholder_rendered."""
    req_path = Path(run_dir) / USER_INPUT_REQUESTS_FILE
    if not req_path.is_file():
        return None
    uir = load_contract(UserInputRequests, req_path)
    changed = False
    for r in uir.requests:
        if r.request_id in rendered_ids and r.status is UserRequestStatus.open:
            r.status = UserRequestStatus.placeholder_rendered
            changed = True
    if changed:
        uir.summary = _format_summary(uir.requests)
        write_contract(uir, req_path)
    return uir
