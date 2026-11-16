#!/usr/bin/env python3
"""Read PPTX objects in presentation order; do not infer rendered appearance."""
import argparse
import hashlib
import json
from pathlib import Path
import posixpath
import sys
from xml.etree import ElementTree as ET
from zipfile import ZipFile, BadZipFile

NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pkg": "http://schemas.openxmlformats.org/package/2006/relationships",
}
OBJECTS = {"sp", "pic", "graphicFrame", "cxnSp", "grpSp", "contentPart"}
MAX_XML_BYTES = 32 * 1024 * 1024


def local(tag):
    return tag.rsplit("}", 1)[-1]


def xml(z, part):
    info = z.getinfo(part)
    if info.file_size > MAX_XML_BYTES:
        raise ValueError(f"XML part exceeds inspection limit: {part}")
    return ET.fromstring(z.read(info))


def links(z, part):
    folder, name = posixpath.split(part)
    relpart = posixpath.join(folder, "_rels", name + ".rels")
    try:
        root = xml(z, relpart)
    except KeyError:
        return {}
    result = {}
    for rel in root.findall("pkg:Relationship", NS):
        if rel.get("TargetMode") == "External":
            continue
        target = rel.attrib["Target"]
        target = posixpath.normpath(target.lstrip("/") if target.startswith("/")
                                    else posixpath.join(folder, target))
        if target == ".." or target.startswith("../"):
            raise ValueError(f"relationship escapes package root: {rel.attrib['Id']}")
        result[rel.attrib["Id"]] = (rel.attrib["Type"].rsplit("/", 1)[-1], target)
    return result


def related(z, part, kind):
    return next((target for ty, target in links(z, part).values() if ty == kind), None)


def pair(node, name, a, b):
    child = node.find(name, NS)
    return [int(child.attrib[a]), int(child.attrib[b])] if child is not None else None


def geometry(node, width, height, nested):
    transform = next((child for path in ("p:spPr/a:xfrm", "p:grpSpPr/a:xfrm", "p:xfrm")
                      if (child := node.find(path, NS)) is not None), None)
    if transform is None:
        return None
    offset = pair(transform, "a:off", "x", "y")
    size = pair(transform, "a:ext", "cx", "cy")
    return {
        "coordinate_space": "group_local" if nested else "slide",
        "offset_emu": offset, "size_emu": size,
        "box_percent": [round(v / basis * 100, 6)
                        for v, basis in zip(offset + size, (width, height, width, height))]
                       if offset is not None and size is not None and not nested else None,
        "child_offset_emu": pair(transform, "a:chOff", "x", "y"),
        "child_size_emu": pair(transform, "a:chExt", "cx", "cy"),
        "rotation_60000ths_degree": int(transform.get("rot", "0")),
        "flip_h": transform.get("flipH", "0") in ("1", "true"),
        "flip_v": transform.get("flipV", "0") in ("1", "true"),
    }


def solid(node):
    if node is None:
        return None
    fill = node.find("a:solidFill", NS)
    if fill is None or not len(fill):
        return None
    c = fill[0]
    return {"type": local(c.tag), "attributes": c.attrib,
            "modifiers": [{"type": local(child.tag), "attributes": child.attrib} for child in c]}


def font_style(node):
    return {
        "element": local(node.tag), "attributes": node.attrib,
        "typefaces": {key: x.get("typeface") for key in ("latin", "ea", "cs", "sym")
                      if (x := node.find("a:" + key, NS)) is not None},
        "solid_fill": solid(node),
    }


def objects(tree, width, height, include_text=False, depth=0):
    if depth > 64:
        raise ValueError("PPTX shape groups exceed nesting limit")
    result = []
    for node in tree:
        kind = local(node.tag)
        if kind not in OBJECTS:
            continue
        # Non-visual metadata belongs to this object, never to a child shape.
        nv = next((x for parent in node
                   if (x := parent.find("p:cNvPr", NS)) is not None), None)
        ph = next((x for parent in node
                   if (x := parent.find("p:nvPr/p:ph", NS)) is not None), None)
        row = {
            "object_id": nv.get("id") if nv is not None else None,
            "name": nv.get("name") if nv is not None else None,
            "object_type": kind,
            "order_in_parent": len(result) + 1,
            "placeholder": ph.attrib if ph is not None else None,
            "geometry": geometry(node, width, height, depth > 0),
            "solid_fill": solid(node.find("p:spPr", NS)),
        }
        graphic = node.find("a:graphic/a:graphicData", NS)
        if graphic is not None:
            row["graphic_uri"] = graphic.get("uri")
        if kind == "grpSp":
            row["children"] = objects(node, width, height, include_text, depth + 1)
        else:
            runs = [x.text or "" for x in node.findall(".//a:t", NS)]
            row["text_run_count"] = len(runs)
            row["text_character_count"] = sum(map(len, runs))
            row["direct_text_styles"] = [font_style(x) for x in node.iter()
                                         if local(x.tag) in ("rPr", "defRPr", "endParaRPr")]
            if include_text and runs:
                row["text_runs"] = runs
        result.append(row)
    return result


def inspect(source, include_text):
    with source.open("rb") as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        fingerprint = digest.hexdigest()
    pages = []
    with ZipFile(source) as z:
        part = "ppt/presentation.xml"
        pres = xml(z, part)
        size = pres.find("p:sldSz", NS)
        if size is None:
            raise ValueError("missing p:sldSz canvas")
        width, height = int(size.attrib["cx"]), int(size.attrib["cy"])
        if min(width, height) <= 0:
            raise ValueError("nonpositive canvas")
        rels = links(z, part)
        for n, entry in enumerate(pres.findall("p:sldIdLst/p:sldId", NS), 1):
            reltype, target = rels[entry.attrib["{" + NS["r"] + "}id"]]
            if reltype != "slide":
                raise ValueError(f"presentation page {n} has non-slide relationship")
            slide = xml(z, target)
            tree = slide.find("p:cSld/p:spTree", NS)
            if tree is None:
                raise ValueError(f"page {n}: no shape tree")
            layout = related(z, target, "slideLayout")
            master = related(z, layout, "slideMaster") if layout else None
            theme = related(z, master, "theme") if master else None
            pages.append({
                "source_slide": n, "slide_part": target, "layout_part": layout,
                "master_part": master, "theme_part": theme,
                "hidden": slide.get("show") in ("0", "false"),
                "direct_background": solid(slide.find("p:cSld/p:bg/p:bgPr", NS)),
                "objects": objects(tree, width, height, include_text),
            })
    return {
        "source": str(source.resolve()), "sha256": fingerprint,
        "inspection_level": "object_parsed", "rendered": False,
        "observation_scope": "direct OOXML fields; no inherited style resolution or rendering",
        "canvas": {"width": width, "height": height, "unit": "emu"},
        "page_count": len(pages), "slides": pages,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--include-text", action="store_true", help="record text runs (default: counts only)")
    args = parser.parse_args()
    try:
        if args.source.suffix.lower() != ".pptx" or args.output.suffix.lower() != ".json":
            raise ValueError("expected .pptx input and .json output")
        if args.output.resolve() == args.source.resolve():
            raise ValueError("source and output paths must differ")
        report = inspect(args.source, args.include_text)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8", newline="\n") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"PASS pages={report['page_count']} rendered=false output={args.output}")
        return 0
    except (OSError, ValueError, KeyError, ET.ParseError, BadZipFile) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
