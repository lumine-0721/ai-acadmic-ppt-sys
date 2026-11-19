"""Read-only projections of *checked* producer artifacts, not replacement checkers."""
from copy import deepcopy

from .model import Conflict, Invalid, digest, new_deck, new_slide, require, validate


def _rest(row, *used):
    return {k: v for k, v in row.items() if k not in used}


def _rows(rows, key):
    require(type(rows) is list, f"{key}: expected rows")
    seen = set()
    for index, row in enumerate(rows):
        require(type(row) is dict and isinstance(row.get(key), str), f"missing {key}")
        ident = row[key]
        require(ident not in seen, f"duplicate {key}: {ident}")
        seen.add(ident)
        if not row.get("retired"):
            yield index, ident, row


def _bound(d, doc):
    if (doc["project_id"], doc["base_revision"]) != (d["deck_id"], d["origin"]["project_revision"]):
        raise Conflict("project_id/base_revision does not match APC")


LOCATION_FIELDS = {"source", "source_url_or_path", "url_or_path", "local_path", "path",
                   "reference_file", "output_dir", "package_path", "file_path"}


def _remap(data, locations, field=None):
    if type(data) is dict:
        return {k: _remap(v, locations, k) for k, v in data.items()}
    if type(data) is list:
        return [_remap(v, locations, field) for v in data]
    return locations.get(data, data) if isinstance(data, str) and field in LOCATION_FIELDS else data


def _apc(h, requirements, bound_keys):
    d = new_deck(h["project_id"])
    records = h["records"]
    d["origin"] = {"project_revision": h["revision"], "apc_records": records}
    d["metadata"]["task"] = requirements
    live = {k: r for k, r in records.items() if not r.get("retired")}
    for key, r in live.items():
        require(all(dep in live for dep in r["depends_on"]), f"{key}: dangling APC dependency")
        if key.startswith("C"):
            d["claims"][key] = {"text": r["summary"], "status": r["status"], "kind": r["kind"]}
        elif key.startswith("E"):
            # APC E and SEAA E are separate identity domains even if their digits match.
            eid, source = "apc." + key, None
            if r.get("source"):
                source = eid + ".source"
                d["sources"][source] = {"title": r["source"]}
                if r["source"].startswith(("http://", "https://")) or r["source"] in bound_keys:
                    d["sources"][source]["uri"] = r["source"]
            d["evidence"][eid] = {"source_id": source, "locator": r.get("locator"), "scope": None,
                "status": r["status"], "links": [{"claim_id": c, "relation": rel, "eligible": False}
                    for rel in ("supports", "opposes") for c in r.get(rel, [])],
                "metadata": {"summary": r["summary"], "material_ids": r["depends_on"]}}
    for sid in h["slide_order"]:
        require(sid in live and sid not in d["slides"] and sid.startswith("S"), f"invalid APC page {sid}")
        r = live[sid]
        page = new_slide(r["summary"], r["summary"], r.get("role", "content"))
        page["metadata"] = {"apc_status": r["status"]}
        if r["status"] == "BLOCKED":
            page["status"] = "BLOCKED"
        # Traverse the actual APC graph, including claims linked via visual records.
        seen, pending = set(), list(r["depends_on"])
        while pending:
            k = pending.pop()
            if k not in seen:
                seen.add(k)
                pending.extend(live[k]["depends_on"])
        page["semantic"]["claim_ids"] = sorted(k for k in seen if k.startswith("C"))
        page["semantic"]["evidence_ids"] = sorted("apc." + k for k in seen if k.startswith("E"))
        d["slides"][sid] = page
        d["slide_order"].append(sid)
    return d


def _seaa(d, package):
    m = package["02_EVIDENCE_MANIFEST.json"]
    _bound(d, m)
    for _, key, r in _rows(package["01_SOURCE_MANIFEST.json"]["sources"], "source_id"):
        require(key not in d["sources"], f"source collision: {key}")
        d["sources"][key] = {"title": r["title"], "uri": r["url_or_path"],
                             "metadata": _rest(r, "source_id", "title", "url_or_path")}
    for i, key, r in _rows(m["evidence"], "evidence_id"):
        require(key not in d["evidence"], f"evidence collision: {key}")
        d["evidence"][key] = {"source_id": r["source_id"], "scope": r["verification_scope"],
            "locator": r["locator"], "status": r["status"], "manifest_pointer": f"/evidence/{i}",
            "links": [{"claim_id": l["claim_id"], "relation": l["relation"], "eligible": False}
                      for l in r["claim_links"]],
            "metadata": _rest(r, "source_id", "evidence_id", "verification_scope", "locator",
                              "status", "claim_links")}
    for _, key, r in _rows(package["03_ASSET_MANIFEST.json"]["assets"], "asset_id"):
        d["assets"][key] = {"path": r["path"], "source_id": r["source_id"],
            "sha256": r["sha256"], "rights": r["rights"], "immutable": r["scientific_immutable"],
            "evidence_ids": r["evidence_ids"], "metadata": _rest(r, "path", "source_id", "sha256",
                "asset_id", "rights", "scientific_immutable", "evidence_ids")}
    for _, sid, r in _rows(m["slides"], "slide_id"):
        page = d["slides"][sid]
        sem = page["semantic"]
        for field, incoming in (("claim_ids", [c["claim_id"] for c in r.get("claims", [])]),
                                ("evidence_ids", r.get("evidence_ids", [])),
                                ("asset_ids", r.get("asset_ids", []))):
            sem[field] = list(dict.fromkeys(sem[field] + incoming))
        page["gaps"] = r["gaps"]
        page["metadata"]["seaa"] = _rest(r, "slide_id", "claims", "evidence_ids", "asset_ids", "gaps")
        # Retain assessment details, including qualifications for individual claims.
        page["metadata"]["seaa"]["assessed_claims"] = r.get("claims", [])
        if r["status"] == "BLOCKED":
            page["status"] = "BLOCKED"
    d["origin"]["seaa"] = _rest(m, "slides", "evidence")


def _aprd(d, pkg):
    system = pkg["DESIGN_SYSTEM.json"]
    mapping = pkg["SLIDE_STYLE_MAPPING.json"]
    require(mapping["design_revision"] == system["design_revision"], "APRD design revision mismatch")
    require(mapping["scope"] in ("project", "reference_only"), "unknown APRD scope")
    expected = d["slide_order"] if mapping["scope"] == "project" else []
    if mapping["scope"] == "project":
        _bound(d, mapping)
    require(mapping["target_slide_ids"] == expected, "APRD slide order mismatch")
    for _, key, r in _rows(pkg["SOURCE_MANIFEST.json"]["references"], "ref_id"):
        src = "aprd." + key
        require(src not in d["sources"], f"source collision: {src}")
        d["sources"][src] = {"title": r["title"], "uri": r["source_url_or_path"],
            "license": r["rights_status"], "metadata": _rest(r, "title", "ref_id", "source_url_or_path", "rights_status")}
    d["theme"] = system
    for _, key, r in _rows(pkg["LAYOUT_LIBRARY.json"]["layouts"], "layout_id"):
        d["layouts"][key] = {"source_id": "aprd." + r["source_ref_id"] if r["source_ref_id"] else None,
            "slots": [b["slot"] for b in r["normalized_geometry"]], "native": {},
            "constraints": _rest(r, "layout_id", "source_ref_id")}
    require([r["target_slide_id"] for r in mapping["mappings"]] == expected, "APRD mapping incomplete")
    for _, sid, r in _rows(mapping["mappings"], "target_slide_id"):
        p = d["slides"][sid]
        p["design"]["layout_id"] = r["selected_layout_id"]
        p["design"]["constraints"] = _rest(r, "target_slide_id", "selected_layout_id")
        if r.get("coverage") == "BLOCKED":
            p["status"] = "BLOCKED"
    d["origin"]["aprd"] = _rest(mapping, "mappings")


def assemble(handoff, *, requirements=None, aprd=None, seaa=None, locations=None):
    """Accept JSON objects keyed by the real manifest file names. No ambient file reads."""
    try:
        h, task, style, science = _remap([handoff, requirements or {}, aprd, seaa], locations or {})
        d = _apc(h, task, set((locations or {}).values()))
        if science is not None:
            _seaa(d, science)
        if style is not None:
            _aprd(d, style)
        validate(d)
        return d
    except (KeyError, TypeError, IndexError) as exc:
        raise Invalid(f"incomplete or malformed upstream artifact: {exc}") from exc


def input_receipt(handoff, *, requirements=None, aprd=None, seaa=None):
    """Capture at producer-check time. Do not manufacture a receipt when restoring a run."""
    return digest({"handoff": handoff, "requirements": requirements, "aprd": aprd, "seaa": seaa})


def migrate(v2, handoff, *, check_v2, receipt, requirements=None, aprd=None, seaa=None, locations=None):
    """Validate with the producer's validator; rehydrate omitted facts from original artifacts."""
    require(v2.get("schema_version") == "academic-deck-ir/2", "expected v2; reassemble v1 or unknown versions")
    check_v2(v2)
    require(receipt == input_receipt(handoff, requirements=requirements, aprd=aprd, seaa=seaa),
            "missing or mismatched producer input receipt; reassemble from checked artifacts")
    try:
        return _migrate(v2, handoff, requirements, aprd, seaa, locations)
    except (KeyError, TypeError, IndexError) as exc:
        raise Invalid(f"incomplete v2 migration inputs: {exc}") from exc


def _migrate(v, h, requirements, aprd, seaa, locations):
    meta, narrative = v["deck_metadata"], v["narrative_structure"]
    require((meta["project_id"], meta["revision"]) == (h["project_id"], h["revision"]),
            "v2/APC identity mismatch")
    require(narrative["claims"] == {k: r for k, r in h["records"].items() if k.startswith("C")},
            "v2/APC claims mismatch")
    require(narrative["slide_order"] == h["slide_order"] == [r["slide_id"] for r in v["slide_specifications"]],
            "v2/APC slide order mismatch")
    require(not v["presentation_requirements"].get("path") or requirements is not None, "supply original requirements")
    design = v["design_bindings"]
    for k, name in (("design_system", "DESIGN_SYSTEM.json"), ("layouts", "LAYOUT_LIBRARY.json"),
                    ("mapping", "SLIDE_STYLE_MAPPING.json")):
        if design.get(k) is not None:
            require(aprd is not None and aprd[name] == design[k], f"v2/APRD mismatch: {k}")
    require(not design.get("constraints_dropped"), "cannot recover dropped design constraints")
    d = assemble(h, requirements=requirements, aprd=aprd, seaa=seaa, locations=locations)
    expected = {(key, l["claim_id"], l["relation"]): l for key, e in d["evidence"].items()
                if not key.startswith("apc.") for l in e["links"]}
    seen = set()
    for b in v["evidence_bindings"]:
        eid, = b["evidence_ids"]
        src, = b["source_ids"]
        e = d["evidence"][eid]
        key = (eid, b["claim_id"], b["relation"])
        require(key in expected and key not in seen, f"v2/SEAA edge mismatch: {key}")
        require((src, b["locator"], b["verification_scope"], b["evidence_status"], b["manifest_ref"]["pointer"]) ==
                (e["source_id"], e["locator"], e["scope"], e["status"], e["manifest_pointer"])
                and b["source_manifest_ref"]["source_id"] == src, f"v2/SEAA provenance mismatch: {eid}")
        seen.add(key)
        expected[key]["eligible"] = b["eligible_support"]
    require(seen == set(expected), "v2/SEAA edge set mismatch")
    raw_assets = (seaa or {}).get("03_ASSET_MANIFEST.json", {}).get("assets", [])
    require(sorted(a["asset_id"] for a in v["asset_bindings"]) == sorted(a["asset_id"] for a in raw_assets),
            "v2/SEAA asset set mismatch")
    for a in v["asset_bindings"]:
        raw = next((r for r in (seaa or {}).get("03_ASSET_MANIFEST.json", {}).get("assets", [])
                    if r["asset_id"] == a["asset_id"]), None)
        require(raw is not None and all(raw.get(k) == val for k, val in a.items()), "v2/SEAA assets mismatch")
    for row in v["slide_specifications"]:
        sid = row["slide_id"]
        p = d["slides"][sid]
        require(row["summary"] == h["records"][sid]["summary"] and
                row["depends_on"] == h["records"][sid]["depends_on"], f"v2/APC page mismatch: {sid}")
        require(row["status_sources"]["apc"] == p["metadata"]["apc_status"]
                and row["status_sources"]["seaa"] == p["metadata"].get("seaa", {}).get("status"),
                f"v2 assessment requires matching sources: {sid}")
        levels = {"READY": 0, "PROVISIONAL": 1, "BLOCKED": 2}
        original = row["status_sources"]
        floor = max(levels.get(original["apc"], 2), levels.get(original["seaa"], 2)
                    if original["seaa"] is not None else 1, int(bool(p["gaps"])))
        require(row["evidence_gaps"] == p["gaps"] and levels[row["status"]] >= floor and
                not (p["status"] == "BLOCKED" and row["status"] != "BLOCKED"),
                f"v2 gaps/readiness differ from checked artifacts: {sid}")
        p["status"], p["gaps"] = row["status"], deepcopy(row["evidence_gaps"])
        if row.get("speaker_notes") is not None:
            p["semantic"]["notes"] = row["speaker_notes"]
        if row.get("on_slide_text") and row["on_slide_text"] != p["semantic"]["title"]:
            p["semantic"]["elements"]["body"] = {"kind": "text", "runs": [{"text": row["on_slide_text"]}]}
            p["semantic"]["order"].append("body")
        p["metadata"]["v2"] = _rest(row, "slide_id", "status", "summary", "depends_on", "on_slide_text",
                                     "speaker_notes", "evidence_gaps")
    require(v["validation_state"].get("status") in ("accepted", "partial", "blocked"), "unknown v2 assessment")
    d["origin"]["v2"] = _remap({"validation": v["validation_state"], "execution": v["execution_metadata"],
                                "run_id": meta.get("run_id")}, locations or {})
    validate(d)
    return d
