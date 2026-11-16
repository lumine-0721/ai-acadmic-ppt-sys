#!/usr/bin/env python3
"""Check Prompt 1 package structure, traceability, and revision impact."""
import argparse
import json
from pathlib import Path
import re
import sys

MARKDOWN = ("REFERENCE_REPORT.md", "REFERENCE_COVERAGE_REPORT.md", "RECONSTRUCTION_GUIDE.md")
SYSTEM = ("grid margins colors typography spacing shapes components graphics image_treatment "
          "chart_style consistency_rules constraints").split()
LEVELS = ("discovery", "landing", "thumbnail", "page_visual", "document_parsed", "object_parsed")
PRECISION = ("exact", "estimated", "derived", "unknown")


def need(ok, message):
    if not ok:
        raise ValueError(message)


def text(x):
    return isinstance(x, str) and bool(x.strip())


def nat(x):
    return type(x) is int and x > 0


def is_id(x, prefix):
    return isinstance(x, str) and re.fullmatch(rf"(?:{prefix})[0-9]{{3,}}", x) is not None


def ids(x, prefix, label):
    need(isinstance(x, list) and all(is_id(k, prefix) for k in x), f"{label}: invalid ID list")
    need(len(x) == len(set(x)), f"{label}: repeated IDs")


def fields(row, names, label):
    need(isinstance(row, dict), f"{label}: expected object")
    missing = [name for name in names if name not in row]
    need(not missing, f"{label}: missing {', '.join(missing)}")


def nonempty(row, names, label):
    fields(row, names, label)
    for key in names:
        need(text(row[key]), f"{label}: {key} must be nonempty text")


def pairs(items):
    result = {}
    for key, value in items:
        need(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load(path):
    obj = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=pairs)
    need(isinstance(obj, dict), f"{path.name}: expected JSON object")
    return obj


def rows(items, field, prefix):
    need(isinstance(items, list), f"{field}: expected array")
    result = {}
    for row in items:
        fields(row, (field,), field)
        key = row[field]
        need(is_id(key, prefix) and key not in result, f"{field}: bad or duplicate ID {key}")
        result[key] = row
    return result


def inspectable(row):
    return row["inspection_level"] in LEVELS[3:] and bool(row["inspected_pages"])


def check_sources(doc):
    refs = rows(doc.get("references"), "ref_id", "REF")
    for k, r in refs.items():
        nonempty(r, "title source_url_or_path file_format editability_status usage_notes".split(), k)
        fields(r, ("page_count_if_verified", "inspected_pages"), k)
        for key, options in {
            "source_type": ("template", "deck", "page"),
            "access_status": ("discovered", "opened", "acquired", "limited", "unavailable"),
            "inspection_level": LEVELS,
            "rights_status": ("direct_use", "reference_only", "needs_permission", "unavailable", "unknown"),
            "selected_role": ("primary", "supplement", "rejected", "candidate"),
        }.items():
            need(r.get(key) in options, f"{k}: invalid {key}")
        n, pages = r["page_count_if_verified"], r["inspected_pages"]
        need(n is None or nat(n), f"{k}: invalid verified page count")
        need(isinstance(pages, list) and all(nat(x) and (n is None or x <= n) for x in pages),
             f"{k}: invalid inspected page")
        need(len(pages) == len(set(pages)), f"{k}: repeated inspected page")
        need(not pages or r["inspection_level"] in LEVELS[3:], f"{k}: pages claimed without page inspection")
        if r["selected_role"] in ("primary", "supplement"):
            need(inspectable(r), f"{k}: selected source has no verified page-level inspection")
            need(r["access_status"] in ("opened", "acquired", "limited")
                 and r["rights_status"] != "unavailable", f"{k}: selected source unusable")
    return refs


def check_design(d, refs):
    fields(d, ["design_revision", "primary_anchor_ref_id", "canvas", "provenance"] + SYSTEM, "design")
    need(nat(d["design_revision"]), "design_revision must be a positive integer")
    primary = [k for k, r in refs.items() if r["selected_role"] == "primary"]
    anchor = d["primary_anchor_ref_id"]
    need((anchor is None and not primary) or primary == [anchor], "primary anchor and manifest disagree")
    canvas = d["canvas"]
    fields(canvas, ("width", "height", "unit"), "canvas")
    need(all(type(canvas[k]) in (int, float) and 0 < canvas[k] < 1e100 for k in ("width", "height"))
         and canvas["unit"] in ("emu", "px", "pt", "in", "cm", "mm"), "invalid canvas")
    need(all(d[k] for k in SYSTEM), "design sections require values or explicit unknown/not applicable")
    need(isinstance(d["provenance"], list) and d["provenance"], "design provenance is required")
    for p in d["provenance"]:
        nonempty(p, ("parameter", "basis"), "provenance")
        fields(p, ("ref_id", "precision"), "provenance")
        ref = p["ref_id"]
        need(p["precision"] in PRECISION, "invalid provenance precision")
        need(ref is None or (is_id(ref, "REF") and ref in refs), "provenance has unknown REF")
        if p["precision"] == "exact" and ref is not None:
            need(inspectable(refs[ref]), f"{ref}: exact provenance lacks inspection")


def check_layouts(doc, refs):
    layouts = rows(doc.get("layouts"), "layout_id", "LAYOUT")
    required = ("element_hierarchy alignment_rules fixed_constraints variable_parameters "
                "content_capacity adaptation_rules scientific_content_compatibility").split()
    for k, row in layouts.items():
        nonempty(row, ("semantic_role",), k)
        fields(row, ["source_ref_id", "source_slide", "normalized_geometry", "precision"] + required, k)
        need(all(row[x] for x in required), f"{k}: incomplete layout rules")
        src, page, precision = (row[x] for x in ("source_ref_id", "source_slide", "precision"))
        need(precision in PRECISION, f"{k}: invalid precision")
        if src is None:
            need(page is None and precision == "derived", f"{k}: original layout must be marked derived")
        else:
            need(is_id(src, "REF") and src in refs, f"{k}: missing reference {src}")
            ref = refs[src]
            need(ref["selected_role"] in ("primary", "supplement"), f"{k}: reference was not selected")
            need(nat(page) and page in ref["inspected_pages"], f"{k}: reference page was not inspected")
            if precision == "exact":
                need(ref["inspection_level"] in ("document_parsed", "object_parsed"),
                     f"{k}: exact geometry requires parsed source")
        geometry = row["normalized_geometry"]
        need(isinstance(geometry, list) and geometry, f"{k}: needs normalized slots")
        names = set()
        for box in geometry:
            nonempty(box, ("slot",), k)
            need(box["slot"] not in names, f"{k}: duplicate slot {box['slot']}")
            names.add(box["slot"])
            fields(box, ("x", "y", "w", "h"), k)
            x, y, w, h = (box[key] for key in ("x", "y", "w", "h"))
            need(all(type(v) in (int, float) and 0 <= v <= 100.000001 for v in (x,y,w,h))
                 and w > 0 and h > 0 and x+w <= 100.000001 and y+h <= 100.000001,
                 f"{k}: geometry exceeds the 0-100 canvas")
    return layouts


def check_mapping(m, revision, layouts, handoff, production):
    fields(m, ("scope", "project_id", "base_revision", "design_revision", "target_slide_ids", "mappings"), "mapping")
    need(nat(m["design_revision"]) and m["design_revision"] == revision, "design revision differs from mapping")
    need(m["scope"] in ("reference_only", "project"), "invalid mapping scope")
    base, project = m["base_revision"], m["project_id"]
    need(project is None or text(project), "invalid project_id")
    need(base is None or (nat(base) and text(project)), "invalid base_revision")
    order = m["target_slide_ids"]
    ids(order, "S", "target_slide_ids")
    byslide = rows(m["mappings"], "target_slide_id", "S")
    need(list(byslide) == order, "mapping must cover target slide order exactly once")
    need(bool(order) == (m["scope"] == "project"), "mapping scope conflicts with target slides")
    if production:
        need(order, "production needs target slide mappings")
    for sid, row in byslide.items():
        nonempty(row, ("required_content_type", "adaptation_instructions"), sid)
        fields(row, ("selected_layout_id", "reference_source", "coverage", "visual_requirements",
                     "layout_risks", "unresolved_requirements"), sid)
        ids(row["visual_requirements"], "V", sid)
        for key in ("layout_risks", "unresolved_requirements"):
            need(isinstance(row[key], list) and all(map(text, row[key])), f"{sid}: invalid {key}")
        lid, src, status = (row[k] for k in ("selected_layout_id", "reference_source", "coverage"))
        need(status in ("COVERED", "PARTIAL", "ORIGINAL", "BLOCKED"), f"{sid}: invalid coverage")
        if lid is None:
            need(status == "BLOCKED" and src is None, f"{sid}: no layout but not BLOCKED")
        else:
            need(is_id(lid, "LAYOUT") and lid in layouts, f"{sid}: missing layout {lid}")
            need(src == layouts[lid]["source_ref_id"], f"{sid}: reference differs from its layout")
        if status in ("COVERED", "ORIGINAL"):
            need(not row["unresolved_requirements"], f"{sid}: covered mapping has unresolved requirements")
            need((status == "ORIGINAL") == (src is None), f"{sid}: coverage/source mismatch")
        else:
            need(row["unresolved_requirements"], f"{sid}: coverage gap needs an unresolved requirement")
        if production:
            need(status in ("COVERED", "ORIGINAL"), f"{sid}: {status} is not production-ready")

    if handoff:
        need(text(project) and nat(base) and project == handoff.get("project_id")
             and base == handoff.get("revision"), "mapping is out of sync with upstream revision")
        need(order == handoff.get("slide_order"), "mapping is out of sync with upstream slide order")
        rs = handoff.get("records")
        need(isinstance(rs, dict), "upstream records missing")
        for sid, row in byslide.items():
            r = rs.get(sid)
            need(isinstance(r, dict) and not r.get("retired"), f"{sid}: absent or retired upstream")
            deps = r.get("depends_on")
            need(isinstance(deps, list) and all(isinstance(x, str) for x in deps), f"{sid}: invalid upstream dependencies")
            expected = {v for v in deps if is_id(v, "V")}
            need(set(row["visual_requirements"]) == expected, f"{sid}: V IDs differ from upstream")
            need(all(isinstance(rs.get(v), dict) and not rs[v].get("retired") for v in expected),
                 f"{sid}: V missing or retired upstream")
    return byslide


def check_feedback(f, mapping, handoff):
    fields(f, ("project_id", "base_revision", "stage", "task_ids", "owner", "inputs", "acceptance", "output_dir",
               "result", "affected_ids", "artifacts", "observations", "proposed_changes", "checks"), "feedback")
    need(text(f["project_id"]) and nat(f["base_revision"]) and
         (f["project_id"], f["base_revision"], f["stage"]) ==
         (mapping["project_id"], mapping["base_revision"], "design-reference"), "feedback project/version/stage mismatch")
    nonempty(f, ("owner", "acceptance", "output_dir"), "feedback")
    for key in ("inputs", "artifacts", "observations", "checks"):
        need(isinstance(f[key], list) and f[key], f"feedback {key} is required")
    need(f["result"] in ("accepted", "partial", "blocked", "conflict"), "feedback result invalid")
    ids(f["task_ids"], "S|V", "feedback task_ids")
    ids(f["affected_ids"], "M|C|E|R|V|S", "feedback affected_ids")
    need(f["task_ids"] and set(f["task_ids"]) <= set(f["affected_ids"]), "feedback tasks must be affected IDs")
    if handoff is not None:
        r = handoff["records"]
        need(all(isinstance(r.get(k), dict) and not r[k].get("retired") for k in f["affected_ids"]),
             "feedback mentions an absent or retired upstream ID")
    need(isinstance(f["proposed_changes"], list) and f["proposed_changes"], "feedback needs a proposal")
    for change in f["proposed_changes"]:
        nonempty(change, ("problem", "proposal", "preserve"), "feedback change")
        need(change.get("kind") in ("DESIGN_GAP", "PRESENTATION_OPPORTUNITY"), "feedback proposal kind invalid")
        affected = change.get("affected_ids")
        ids(affected, "M|C|E|R|V|S", "feedback proposal affected_ids")
        need(affected and set(affected) <= set(f["affected_ids"]), "feedback proposal IDs mismatch")


def package(folder, upstream=None, production=False):
    for name in MARKDOWN:
        need(text((folder / name).read_text(encoding="utf-8-sig")), f"{name} is empty")
    refs = check_sources(load(folder / "SOURCE_MANIFEST.json"))
    for rid, record in refs.items():
        if "inspection_artifact" not in record:
            continue
        rel = record["inspection_artifact"]
        need(text(rel), f"{rid}: invalid inspection artifact path")
        path = (folder / rel).resolve()
        need(folder.resolve() in path.parents, f"{rid}: inspection artifact must be inside the package")
        data = load(path)
        need(record["inspection_level"] == "object_parsed" and
             data.get("inspection_level") == "object_parsed" and
             data.get("page_count") == record["page_count_if_verified"] and
             text(record.get("sha256")) and data.get("sha256") == record["sha256"],
             f"{rid}: inspection artifact provenance does not match manifest")
        pages = data.get("slides")
        need(isinstance(pages, list) and all(isinstance(p, dict) for p in pages) and
             set(record["inspected_pages"]) <= {p.get("source_slide") for p in pages if nat(p.get("source_slide"))},
             f"{rid}: inspection artifact is missing claimed slides")
    design = load(folder / "DESIGN_SYSTEM.json")
    check_design(design, refs)
    layouts = check_layouts(load(folder / "LAYOUT_LIBRARY.json"), refs)
    mapping = load(folder / "SLIDE_STYLE_MAPPING.json")
    handoff = load(upstream) if upstream is not None else None
    if upstream is not None:
        need(handoff, "upstream index is empty")
    byslide = check_mapping(mapping, design["design_revision"], layouts, handoff, production)
    if byslide:
        coverage = (folder / "REFERENCE_COVERAGE_REPORT.md").read_text(encoding="utf-8-sig")
        entries = re.findall(r"(?m)^\|[ \t]*(S[0-9]{3,})[ \t]*\|[ \t]*(COVERED|PARTIAL|ORIGINAL|BLOCKED)[ \t]*\|", coverage)
        need(entries == [(sid, row["coverage"]) for sid, row in byslide.items()],
             "coverage report must match mapping IDs, statuses and order")
    feedback = folder / "DESIGN_FEEDBACK.json"
    if feedback.exists():
        check_feedback(load(feedback), mapping, handoff)
    return refs, design, layouts, mapping, byslide


def impact(old, new):
    oref, od, ol, om, os = old
    nref, nd, nl, nm, ns = new
    need(om["project_id"] == nm["project_id"] and om["scope"] == nm["scope"], "project/scope differs")
    need(nd["design_revision"] == od["design_revision"] + 1, "design revision must increment by 1")
    if om["base_revision"] is not None:
        need(nm["base_revision"] is not None and nm["base_revision"] >= om["base_revision"],
             "upstream revision moved backward")
    for key in oref.keys() & nref.keys():
        a, b = oref[key], nref[key]
        need(a["source_url_or_path"] == b["source_url_or_path"]
             or (text(a.get("sha256")) and a.get("sha256") == b.get("sha256")), f"{key}: reference ID reassigned")
    for key in ol.keys() & nl.keys():
        need(ol[key]["semantic_role"] == nl[key]["semantic_role"], f"{key}: layout ID reassigned to a different purpose")

    def changed(a, b):
        return sorted(k for k in a.keys() | b.keys() if a.get(k) != b.get(k))

    rc, lc = changed(oref, nref), changed(ol, nl)
    global_change = ({k: v for k, v in od.items() if k != "design_revision"} !=
                     {k: v for k, v in nd.items() if k != "design_revision"}) or nd["primary_anchor_ref_id"] in rc
    pos = {sid: i for i, sid in enumerate(om["target_slide_ids"])}
    affected = [sid for i, sid in enumerate(nm["target_slide_ids"])
                if global_change or pos.get(sid) != i or os.get(sid) != ns[sid]
                or ns[sid]["reference_source"] in rc or ns[sid]["selected_layout_id"] in lc]
    return {"global_change": global_change, "changed_references": rc, "changed_layouts": lc,
            "removed_slides": sorted(os.keys() - ns.keys()), "affected_slides": affected}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("validate")
    verify.add_argument("package", type=Path)
    verify.add_argument("--handoff", type=Path)
    verify.add_argument("--production", action="store_true")
    delta = commands.add_parser("impact")
    delta.add_argument("old", type=Path)
    delta.add_argument("new", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "impact":
            print(json.dumps(impact(package(args.old), package(args.new)), ensure_ascii=True))
        else:
            refs, d, layouts, _, slides = package(args.package, args.handoff, args.production)
            print(f"PASS design_revision={d['design_revision']} refs={len(refs)} layouts={len(layouts)} "
                  f"mapped={len(slides)} production={str(args.production).lower()}")
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

