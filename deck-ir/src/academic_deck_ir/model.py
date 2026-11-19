"""The wire document is the model. Validation never repairs or annotates its input."""
from __future__ import annotations

import hashlib
import json
import math
import re
from importlib.resources import files
from pathlib import Path, PureWindowsPath
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator


class Invalid(ValueError):
    """Malformed input or broken graph; no snapshot was published."""


class Conflict(Invalid):
    """Two inputs cannot identify the same fact or base revision."""


def require(ok, message):
    if not ok:
        raise Invalid(message)


def _pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, f"duplicate key: {key}")
        result[key] = value
    return result


def parse(text):
    """Read strict JSON (use for upstream JSON as well)."""
    def bad(value):
        raise Invalid(f"non-finite number: {value}")
    try:
        return json.loads(text, object_pairs_hook=_pairs, parse_constant=bad)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Invalid(str(exc)) from exc


def _json(value):
    if type(value) is dict:
        for key, child in value.items():
            require(type(key) is str, "object keys must be strings")
            _json(child)
    elif type(value) is list:
        for child in value:
            _json(child)
    elif type(value) is float:
        require(math.isfinite(value), "non-finite number")
    else:
        require(value is None or type(value) in (str, int, bool), "not a JSON value")


def canonical(value):
    _json(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


VERSION = "academic-deck-ir/3"
SCHEMA = parse(files(__package__).joinpath("schemas/deck-v3.json").read_bytes())
VALIDATOR = Draft202012Validator(SCHEMA)


def new_deck(identity, title=""):
    result = {key: {} for key in
              ("sections", "slides", "claims", "assets", "evidence", "sources", "layouts", "theme")}
    result.update(schema_version=VERSION, deck_id=identity, metadata={"title": title},
                  slide_order=[], state="draft", history=[])
    validate(result)
    return result


def new_slide(title, goal, role="content"):
    return {"semantic": {"title": title, "goal": goal, "role": role, "claim_ids": [],
                         "evidence_ids": [], "asset_ids": [], "elements": {}, "order": []},
            "design": {"layout_id": None, "placements": {}}, "resolved": {},
            "status": "PROVISIONAL", "gaps": []}


def portable(path):
    """Portable POSIX package key, never a URL, drive, ADS or relative traversal."""
    parts = path.split("/")
    return all(p not in ("", ".", "..") and not p.endswith((".", " "))
               and not re.search(r'[<>:"\\|?*\x00-\x1f]', p)
               and not re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", p, re.I)
               for p in parts)


def resource(root, path):
    require(portable(path), f"non-portable resource key: {path}")
    base = Path(root).resolve(strict=True)
    file = (base / path).resolve()
    require(file.is_relative_to(base), f"resource escapes root: {path}")
    return file


def _no_machine_refs(value):
    if isinstance(value, dict):
        for child in value.values():
            _no_machine_refs(child)
    elif isinstance(value, list):
        for child in value:
            _no_machine_refs(child)
    elif isinstance(value, str):
        # Metadata may contain free prose, but a standalone machine path isn't a resource ID.
        require(not PureWindowsPath(value).drive and not value.startswith(("/", "file:", "\\\\")),
                f"bind machine location to a portable key first: {value}")


def refs(seq, index, where):
    for key in seq:
        require(key in index, f"{where}: unresolved {key}")


def identities(d):
    ids = {f"{domain}/{key}" for domain in ("sections", "slides", "claims", "sources", "assets",
           "evidence", "layouts") for key in d[domain]}
    ids.update(f"slides/{sid}/elements/{eid}" for sid, p in d["slides"].items()
               for eid in p["semantic"]["elements"])
    return ids


def _page(d, sid, slide):
    sem, design = slide["semantic"], slide["design"]
    el = sem["elements"]
    for k, domain in (("claim_ids", "claims"), ("evidence_ids", "evidence"), ("asset_ids", "assets")):
        refs(sem[k], d[domain], sid)
    if "section_id" in sem:
        refs([sem["section_id"]], d["sections"], sid)
    parents = list(sem["order"])
    for eid, node in el.items():
        kind = node["kind"]
        refs(node.get("claim_ids", []), d["claims"], eid)
        refs(node.get("related", []), el, eid)
        if kind == "group":
            parents.extend(node["children"])
        elif kind == "image":
            refs([node["asset_id"]], d["assets"], eid)
        elif kind == "table":
            refs(node["evidence_ids"], d["evidence"], eid)
            require(all(len(r) == len(node["columns"]) for r in node["rows"]), f"{eid}: table width")
        elif kind == "chart":
            for series in node["series"]:
                refs([series["evidence_id"]], d["evidence"], eid)
                require(len(series["values"]) == len(node["categories"]), f"{eid}: chart length")
    require(len(parents) == len(set(parents)) and set(parents) == set(el), f"{sid}: ownership/order")
    pending, seen = list(sem["order"]), set()
    while pending:
        eid = pending.pop()
        seen.add(eid)
        pending.extend(el[eid].get("children", []))
    require(seen == set(el), f"{sid}: detached group cycle")
    lid = design["layout_id"]
    if lid is not None:
        refs([lid], d["layouts"], sid)
    layout = d["layouts"].get(lid, {})
    refs(design["placements"], el, sid)
    occupied, natives = set(), set()
    for eid, p in design["placements"].items():
        if "slot" in p:
            slot = p["slot"]
            refs([slot], layout.get("slots", []), eid)
            require(slot not in occupied, f"{sid}: duplicate slot {slot}")
            occupied.add(slot)
        nid = p.get("native_id")
        if nid is not None:
            refs([nid], layout.get("native", {}), eid)
            require(nid not in natives, f"{sid}: duplicate native {nid}")
            natives.add(nid)
        if "changes" in p:
            require(nid is not None, f"{eid}: changes need native")
            refs(p["changes"], layout["native"][nid]["allowed"], eid)
        style = p.get("style", {})
        for axis in ("width", "height"):
            require(style.get("min_" + axis, 0) <= style.get("max_" + axis, 1), f"{eid}: size bounds")
    refs(slide["resolved"], el, sid)
    for eid, box in slide["resolved"].items():
        require(box["x"] + box["w"] <= 1 + 1e-12 and box["y"] + box["h"] <= 1 + 1e-12,
                f"{eid}: box exceeds canvas")
        style = design["placements"].get(eid, {}).get("style", {})
        for axis, k in (("width", "w"), ("height", "h")):
            require(style.get("min_" + axis, 0) <= box[k] <= style.get("max_" + axis, 1),
                    f"{eid}: resolved {axis} outside intent")
        fixed = layout.get("constraints", {}).get("fixed_constraints", {})
        theme_rules = d["theme"].get("constraints")
        safe = theme_rules.get("safe_area_percent") if isinstance(theme_rules, dict) else None
        if isinstance(fixed, dict) and fixed.get("within_safe_area") is True and safe is not None:
            require(isinstance(safe, dict) and all(type(safe.get(k)) in (int, float)
                    and 0 <= safe[k] <= 100 for k in ("x", "y", "w", "h")), "invalid safe area")
            for start, size in (("x", "w"), ("y", "h")):
                require(safe[start] <= box[start] * 100 + 1e-9 and (box[start]+box[size])*100
                        <= safe[start]+safe[size]+1e-9, f"{eid}: outside template safe area")


def _check(d):
    _json(d)
    error = next(VALIDATOR.iter_errors(d), None)
    if error:
        raise Invalid(f"{error.json_path}: {error.message}")
    retired = {key for rev in d["history"] for key in rev.get("retired", [])}
    require(not (retired & identities(d)), "retired identity reused")
    require(set(d["slide_order"]) == set(d["slides"]), "slide_order must cover every slide once")
    for src in d["sources"].values():
        uri = src.get("uri")
        if uri:
            parsed = urlsplit(uri)
            require((parsed.scheme in ("http", "https") and bool(parsed.netloc)) or portable(uri),
                    f"invalid source uri: {uri}")
        _no_machine_refs(src.get("metadata", {}))
    for layout in d["layouts"].values():
        if layout["source_id"] is not None:
            refs([layout["source_id"]], d["sources"], "layout")
        _no_machine_refs(layout["constraints"] if "constraints" in layout else {})
    for eid, e in d["evidence"].items():
        src = e["source_id"]
        if src is not None:
            refs([src], d["sources"], eid)
        links = set()
        for link in e["links"]:
            cid, rel = link["claim_id"], link["relation"]
            refs([cid], d["claims"], eid)
            require((cid, rel) not in links, f"{eid}: repeated claim edge")
            links.add((cid, rel))
            require(not link["eligible"] or (rel == "supports" and src is not None and e["locator"]
                    and e["status"] in ("VERIFIED", "USER_PROVIDED")), f"{eid}: ineligible support")
        _no_machine_refs(e.get("metadata", {}))
    for aid, a in d["assets"].items():
        require(a["path"] is None or portable(a["path"]), f"{aid}: invalid resource key")
        if a["source_id"] is not None:
            refs([a["source_id"]], d["sources"], aid)
        refs(a["evidence_ids"], d["evidence"], aid)
        if a["evidence_ids"] and a["source_id"]:
            require(any(d["evidence"][e]["source_id"] == a["source_id"] for e in a["evidence_ids"]),
                    f"{aid}: source differs from evidence")
        _no_machine_refs(a.get("metadata", {}))
        _no_machine_refs(a.get("rights", {}))
    for sid, slide in d["slides"].items():
        _page(d, sid, slide)
        _no_machine_refs(slide.get("metadata", {}))
        _no_machine_refs(slide["design"].get("constraints", {}))
    for claim in d["claims"].values():
        _no_machine_refs(claim.get("metadata", {}))
    for k in ("origin", "theme", "metadata"):
        _no_machine_refs(d.get(k, {}))


def diagnostics(d, root=None):
    """Structural failures raise; scientific uncertainty and absent bytes remain inspectable."""
    _check(d)
    out = []
    gate = d.get("origin", {}).get("v2", {}).get("validation", {}).get("status")
    if gate is not None and gate != "accepted":
        out.append(f"upstream_gate:{gate}")
    if root is not None:
        for src, record in d["sources"].items():
            uri = record.get("uri")
            if uri and portable(uri) and not resource(root, uri).is_file():
                out.append(f"missing_source_file:{src}")
    signs, eligible = {}, set()
    for eid, ev in d["evidence"].items():
        if ev["source_id"] is None or not d["sources"][ev["source_id"]].get("uri"):
            out.append(f"missing_source:{eid}")
        if not ev["locator"]:
            out.append(f"missing_locator:{eid}")
        for edge in ev["links"]:
            cid = edge["claim_id"]
            signs.setdefault(cid, set()).add(edge["relation"])
            if edge["eligible"]:
                eligible.add(cid)
    for cid in d["claims"]:
        if cid not in eligible:
            out.append(f"no_eligible_support:{cid}")
        if {"supports", "opposes"} <= signs.get(cid, set()):
            out.append(f"conflicting_evidence:{cid}")
    if not d["slides"]:
        out.append("empty_deck")
    used_assets = set()
    for sid, page in d["slides"].items():
        sem = page["semantic"]
        used_assets.update(sem["asset_ids"])
        used_assets.update(n["asset_id"] for n in sem["elements"].values() if n["kind"] == "image")
        lid = page["design"]["layout_id"]
        if lid and any("native_id" in p for p in page["design"]["placements"].values()):
            src = d["layouts"][lid]["source_id"]
            if src is None or d["sources"][src].get("license") != "direct_use":
                out.append(f"native_use_not_cleared:{sid}")
        if page["status"] != "READY" or page["gaps"]:
            out.append(f"page_not_ready:{sid}")
    for aid, a in d["assets"].items():
        if aid in used_assets and a.get("rights", {}).get("status") != "ALLOWED":
            out.append(f"asset_use_not_cleared:{aid}")
        if not a["path"]:
            out.append(f"missing_asset:{aid}")
        elif root is not None:
            file = resource(root, a["path"])
            if not file.is_file():
                out.append(f"missing_asset:{aid}")
            elif a.get("sha256") and hashlib.sha256(file.read_bytes()).hexdigest() != a["sha256"]:
                out.append(f"changed_asset:{aid}")
    return sorted(out)


def validate(d):
    problems = diagnostics(d)
    require(d["state"] != "released" or not problems, f"release blocked: {problems}")


def dumps(d):
    validate(d)
    return canonical(d)


def loads(data):
    d = parse(data)
    validate(d)
    return d
