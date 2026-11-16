#!/usr/bin/env python3
"""Validate Prompt 0's cross-tool index and trace changes to slides."""
import argparse
from collections import defaultdict, deque
import json
from pathlib import Path
import re
import sys

ID = re.compile(r"[MCERVS][0-9]{3,}")
ALLOWED = dict(M="", E="M", R="ME", C="MER", V="MCER", S="MCERV")
SCI = "VERIFIED USER_PROVIDED INFERRED HYPOTHESIS TO_RETRIEVE BLOCKED".split()
PAGE = "READY PROVISIONAL BLOCKED".split()
STATUS = {"E": SCI, "C": SCI, "R": "OPEN IN_PROGRESS DONE BLOCKED".split(), "V": PAGE, "S": PAGE}
LINKS = {"E": {"supports": "C", "opposes": "C"}, "R": {"target_claims": "C", "result_ids": "E"}}


def need(ok, message):
    if not ok:
        raise ValueError(message)


def string(value):
    return isinstance(value, str) and bool(value.strip())


def revnum(value):
    return type(value) is int and value > 0


def unique_pairs(pairs):
    obj = {}
    for key, value in pairs:
        need(key not in obj, f"duplicate JSON key: {key}")
        obj[key] = value
    return obj


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique_pairs)


def check_index(obj, final=False, strict_assets=False):
    need(isinstance(obj, dict), "index must be a JSON object")
    need(string(obj.get("project_id")), "missing project_id")
    rev = obj.get("revision")
    need(revnum(rev), "revision must be a positive integer")
    rs = obj.get("records")
    need(isinstance(rs, dict), "records must be an object keyed by ID")

    for key, record in rs.items():
        need(ID.fullmatch(key), f"invalid ID: {key}")
        need(isinstance(record, dict), f"{key}: record must be an object")
        need(string(record.get("summary")), f"{key}: missing summary")
        need(type(record.get("retired", False)) is bool, f"{key}: retired must be a boolean")

    def ref_list(value, prefix, key, field, active=True):
        need(isinstance(value, list) and all(isinstance(x, str) for x in value),
             f"{key}: {field} must be a list of IDs")
        need(len(value) == len(set(value)), f"{key}: duplicate ID in {field}")
        for ref in value:
            need(ref in rs and ref[0] in prefix, f"{key}: invalid {field} link {ref}")
            need(not active or not rs[ref].get("retired"), f"{key}: active link to retired {ref}")

    for key, record in rs.items():
        t = key[0]
        ref_list(record.get("depends_on"), ALLOWED[t], key, "depends_on", not record.get("retired"))
        for field, prefix in LINKS.get(t, {}).items():
            ref_list(record.get(field, []), prefix, key, field, not record.get("retired"))
        if record.get("retired"):
            continue
        if t in STATUS:
            need(record.get("status") in STATUS[t], f"{key}: invalid status")
        if t in "ME" and (t == "M" or record["status"] in ("VERIFIED", "USER_PROVIDED")):
            need(string(record.get("source")), f"{key}: source is required")
        if t == "E":
            need(record.get("origin") in ("user", "literature", "other"), f"{key}: invalid origin")
            if record["status"] in ("VERIFIED", "USER_PROVIDED"):
                need(string(record.get("locator")), f"{key}: evidence needs an exact locator")
            need(not (set(record.get("supports", [])) & set(record.get("opposes", []))),
                 f"{key}: same claim cannot be both supported and opposed by one record; split evidence")
        if t == "R" and record["status"] == "DONE":
            need(record.get("result_ids") or string(record.get("resolution")),
                 f"{key}: DONE needs result_ids or a documented resolution")
        if t == "C":
            need(record.get("kind") in ("finding", "inference", "hypothesis", "question", "context"),
                 f"{key}: invalid claim kind")
            need(record.get("origin") in ("user", "literature", "mixed", "other", "not_applicable"),
                 f"{key}: invalid origin")
        if t == "V":
            need(record.get("kind") in ("empirical", "schematic", "layout"), f"{key}: invalid visual kind")
        if t == "S" and record["status"] == "READY":
            reviewed = record.get("reviewed_in")
            need(revnum(reviewed) and reviewed <= rev, f"{key}: invalid reviewed_in")
    order = obj.get("slide_order")
    ref_list(order, "S", "index", "slide_order")
    slides = {key for key, r in rs.items() if key.startswith("S") and not r.get("retired")}
    need(set(order) == slides, "slide_order must contain every active slide exactly once")
    if final:
        need(order, "final index has no active slides")
    for key, r in rs.items():
        if r.get("retired"):
            continue
        t = key[0]
        deps = [(dep, rs[dep]) for dep in r["depends_on"]]
        if t == "C" and r["kind"] == "finding" and r["status"] in ("VERIFIED", "USER_PROVIDED"):
            def admissible(e):
                if r["status"] == "VERIFIED":
                    return e["status"] == "VERIFIED"
                return e["status"] in ("VERIFIED", "USER_PROVIDED") and e["origin"] == "user"
            origins = {e["origin"] for dep, e in deps
                       if dep.startswith("E") and key in e.get("supports", []) and admissible(e)}
            need(origins, f"{key}: finding lacks linked supporting evidence at the required status")
            if r["origin"] in ("user", "mixed"):
                need("user" in origins, f"{key}: user finding lacks user evidence")
            if r["origin"] in ("literature", "mixed"):
                need("literature" in origins, f"{key}: literature finding lacks literature evidence")
            if r["status"] == "USER_PROVIDED":
                need(r["origin"] == "user" and string(r.get("disclosure")),
                     f"{key}: USER_PROVIDED finding needs user origin and visible disclosure")
        if strict_assets and t in "VS" and r["status"] == "READY":
            for dep, retrieval in deps:
                if dep.startswith("R"):
                    need(retrieval["status"] == "DONE",
                         f"{key}: READY has unfinished required retrieval {dep} ({retrieval['status']})")
        if t == "V" and r["status"] == "READY" and r["kind"] == "empirical":
            need(any(dep.startswith("E") and e["status"] in ("VERIFIED", "USER_PROVIDED")
                     for dep, e in deps), f"{key}: READY empirical visual lacks traceable evidence")
        if t == "S" and r["status"] == "READY":
            # Also check claims reachable through a visual.
            reviewed_links = dict(deps)
            for dep, v in deps:
                if dep.startswith("V"):
                    need(v["status"] == "READY", f"{key}: visual {dep} is not READY")
                    reviewed_links.update((x, rs[x]) for x in v["depends_on"])
            for dep, c in reviewed_links.items():
                if not dep.startswith("C"):
                    continue
                need(c["status"] != "BLOCKED", f"{key}: blocked claim {dep}")
                if c["kind"] == "finding":
                    need(c["status"] in ("VERIFIED", "USER_PROVIDED"), f"{key}: unsupported finding {dep}")
                if c["kind"] in ("hypothesis", "inference"):
                    need(string(c.get("disclosure")), f"{key}: missing visible qualifier for {dep}")
        if t == "S" and final:
            need(r["status"] == "READY", f"{key}: {r['status']} is not final-ready")
    return obj


def check_requirements(path, obj):
    text = path.read_text(encoding="utf-8-sig")
    sections = re.findall(r"^## (0[0-9])\.\s+\S.*$", text, re.M)
    need(sections == [f"{i:02d}" for i in range(10)], "requirements must contain ordered sections 00-09")
    project = re.findall(r"^Project-ID:[ \t]*(\S.*?)\s*$", text, re.M)
    revision = re.findall(r"^Revision:[ \t]*(\d+)\s*$", text, re.M)
    need(project == [obj["project_id"]], "requirements project ID does not match index")
    need(revision == [str(obj["revision"])], "requirements revision does not match index")


def impact(old, new):
    need(old["project_id"] == new["project_id"], "project IDs differ")
    need(new["revision"] == old["revision"] + 1, "new revision must follow the previous revision")
    before, after = old["records"], new["records"]
    removed = sorted(before.keys() - after.keys())
    need(not removed, f"missing prior IDs: {', '.join(removed)}; mark them retired")
    for key in before:
        need(not before[key].get("retired") or after[key].get("retired"), f"retired ID reused: {key}")
    added = sorted(after.keys() - before.keys())
    changed = sorted(key for key in before if
                     {k: v for k, v in before[key].items() if k != "reviewed_in"} !=
                     {k: v for k, v in after[key].items() if k != "reviewed_in"})
    edges = defaultdict(set)
    for index in (before, after):
        for key, r in index.items():
            if r.get("retired"):
                continue
            for dep in r["depends_on"]:
                edges[dep].add(key)
            for field in ("supports", "opposes", "target_claims"):
                edges[key].update(r.get(field, []))
            for evidence in r.get("result_ids", []):
                edges[evidence].add(key)
    old_pos = {sid: i for i, sid in enumerate(old["slide_order"])}
    new_pos = {sid: i for i, sid in enumerate(new["slide_order"])}
    moved = sorted(sid for sid in old_pos.keys() & new_pos.keys() if old_pos[sid] != new_pos[sid])
    seen = set(added + changed + moved)
    todo = deque(sorted(seen))
    while todo:
        for nxt in sorted(edges[todo.popleft()] - seen):
            seen.add(nxt)
            todo.append(nxt)
    return {"added": added, "changed": changed, "reordered": moved, "affected": sorted(seen),
            "affected_slides": [sid for sid in new["slide_order"] if sid in seen]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("requirements", type=Path)
    validate.add_argument("index", type=Path)
    validate.add_argument("--previous", type=Path)
    validate.add_argument("--final", action="store_true")
    validate.add_argument("--strict-assets", action="store_true",
                          help="treat direct S/V retrieval links as hard dependencies for READY records")
    delta = sub.add_parser("impact")
    delta.add_argument("previous", type=Path)
    delta.add_argument("index", type=Path)
    args = parser.parse_args()
    try:
        current = check_index(read_json(args.index), getattr(args, "final", False),
                              getattr(args, "strict_assets", False))
        if args.command == "validate":
            check_requirements(args.requirements, current)
        if args.previous:
            report = impact(check_index(read_json(args.previous)), current)
            if args.command == "impact":
                print(json.dumps(report, ensure_ascii=True, indent=2))
                return 0
            for sid in report["affected_slides"]:
                record = current["records"][sid]
                need(record["status"] != "READY" or record["reviewed_in"] == current["revision"],
                     f"{sid}: changed dependencies require review in revision {current['revision']}")
        print(f"PASS revision={current['revision']} records={len(current['records'])} "
              f"active_slides={len(current['slide_order'])} final={str(args.final).lower()}")
        return 0
    except (OSError, ValueError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
