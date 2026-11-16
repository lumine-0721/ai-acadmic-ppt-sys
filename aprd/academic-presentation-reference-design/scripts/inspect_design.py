#!/usr/bin/env python3
"""Optional design evidence: library geometry + source-labelled declarations, not rendering."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

import inspect_pptx as direct

SUPPORTED_VERSION = "1.0.2"
NS = direct.NS
MAX_PACKAGE_BYTES = 256 * 1024 * 1024
MAX_PARTS = 10000
DIMENSIONS = {"left": ("a:off", "x"), "top": ("a:off", "y"),
              "width": ("a:ext", "cx"), "height": ("a:ext", "cy")}


def unknown(reason):
    return {"status": "unknown", "value": None, "reason": reason}


def location(level, part, shape=None, path=None):
    result = {"level": level, "part": part}
    if shape is not None:
        result["shape_id"] = shape.shape_id
    if path:
        result["path"] = path
    return result


def element(shape):
    # Read-only adapter; never use get_or_add, setters, or save on an inspected input.
    return ET.fromstring(shape.element.xml)


def direct_dimension(shape, field):
    root = element(shape)
    child_path, attr = DIMENSIONS[field]
    for path in ("p:spPr/a:xfrm", "p:grpSpPr/a:xfrm", "p:xfrm"):
        node = root.find(path + "/" + child_path, NS)
        if node is not None and attr in node.attrib:
            return int(node.attrib[attr])
    return None


def placeholder_chain(shape, level):
    """Reuse pinned library matching, rather than reproduce its placeholder type map."""
    chain = [(level, shape)]
    error = None
    if not shape.is_placeholder:
        return chain, error
    for next_level in ({"slide": ("layout", "master"), "layout": ("master",)}.get(level, ())):
        try:
            base = getattr(chain[-1][1], "_base_placeholder", None)
        except (KeyError, ValueError, AttributeError) as exc:
            error = f"library_placeholder_lookup:{type(exc).__name__}"
            break
        if base is None:
            error = "no_matching_base_placeholder"
            break
        chain.append((next_level, base))
    return chain, error


def geometry(shape, level, nested=False):
    chain, error = placeholder_chain(shape, level)
    fields = {}
    for name in DIMENSIONS:
        try:
            value = getattr(shape, name)  # public API computes inherited dimensions
        except (KeyError, ValueError, AttributeError) as exc:
            fields[name] = unknown(f"library_geometry:{type(exc).__name__}")
            continue
        source = next(((lvl, item) for lvl, item in chain
                       if direct_dimension(item, name) is not None), None)
        if value is None or source is None:
            fields[name] = unknown(error or "dimension_not_declared")
            continue
        lvl, item = source
        fields[name] = {"status": "known", "value": int(value), "unit": "emu",
                        "source": location(lvl, str(item.part.partname).lstrip("/"), item),
                        "method": "python-pptx public dimension API"}
    return {"coordinate_space": "group_local" if nested else "slide",
            "fields": fields, "rendered_bounds": unknown("rotation_group_transform_and_text_extent_not_rendered")}


def color_declaration(node):
    if node is None:
        return unknown("not_declared_at_this_level")
    value = {"kind": direct.local(node.tag), "attributes": dict(node.attrib),
             "modifiers": [{"kind": direct.local(x.tag), "attributes": dict(x.attrib)} for x in node]}
    # No silent system-color, scheme-color, tint, alpha, or color-map flattening.
    rgb = node.get("val") if direct.local(node.tag) == "srgbClr" and not len(node) else None
    return {"status": "declared", "value": value,
            "unmodified_srgb": rgb, "rendered_color": unknown("theme_color_map_modifiers_and_rendering_not_resolved")}


def declarations(root, level, part, shape=None):
    """Record only formatting, never text. Paths are local to the recorded shape/part."""
    rows = []

    def walk(node, path):
        kind = direct.local(node.tag)
        if kind in ("rPr", "defRPr", "endParaRPr"):
            props = direct.font_style(node)
            fill = node.find("a:solidFill", NS)
            props["color"] = color_declaration(fill[0] if fill is not None and len(fill) else None)
            rows.append({"source": location(level, part, shape, path), "status": "declared",
                         "properties": props})
        if kind in ("fontRef", "fillRef", "lnRef", "effectRef"):
            rows.append({"source": location(level, part, shape, path), "status": "declared",
                         "properties": {"element": kind, "attributes": dict(node.attrib),
                                        "colors": [color_declaration(x) for x in node]}})
        counters = {}
        for child in node:
            tag = direct.local(child.tag)
            counters[tag] = counters.get(tag, 0) + 1
            walk(child, f"{path}/{tag}[{counters[tag]}]")

    walk(root, direct.local(root.tag))
    return rows


def shape_record(shape, level, include_text, nested=False, depth=0):
    if depth > 64:
        raise ValueError("shape groups exceed nesting limit")
    root = element(shape)
    part = str(shape.part.partname).lstrip("/")
    chain, error = placeholder_chain(shape, level)
    is_group = direct.local(root.tag) == "grpSp"
    rows = [] if is_group else declarations(root, level, part, shape)
    inherited = []
    for lvl, base in chain[1:]:
        src = location(lvl, str(base.part.partname).lstrip("/"), base)
        inherited.append(src)
    properties = root.find("p:spPr", NS)
    fill = properties.find("a:solidFill", NS) if properties is not None else None
    row = {"shape_id": shape.shape_id, "name": shape.name, "object_type": direct.local(root.tag),
           "source": location(level, part, shape),
           "placeholder": {"idx": shape.placeholder_format.idx,
                           "type": str(shape.placeholder_format.type)} if shape.is_placeholder else None,
           "inheritance_chain": inherited,
           "geometry": geometry(shape, level, nested), "style_declarations": rows,
           "direct_fill": {"source": location(level, part, shape, "spPr/solidFill"),
                           "color": color_declaration(fill[0] if fill is not None and len(fill) else None)},
           "effective_typography": unknown("declaration_inventory_only_not_a_style_cascade"),
           "unknowns": []}
    if error:
        row["unknowns"].append(error)
    if shape.has_text_frame:
        row["text_character_count"] = len(shape.text_frame.text)
        if include_text:
            row["text"] = shape.text_frame.text
    if is_group:
        row["unknowns"].append("group_transform_not_composed")
        row["children"] = [shape_record(s, level, include_text, True, depth + 1) for s in shape.shapes]
    if direct.local(root.tag) not in ("sp", "pic", "cxnSp", "grpSp"):
        row["unknowns"].append("content_and_styles_not_interpreted:" + direct.local(root.tag))
    # Keep unimplemented fill/effect types visible instead of treating them as no-fill.
    if properties is not None:
        row["uninterpreted_shape_properties"] = [direct.local(x.tag) for x in properties
                                                 if direct.local(x.tag) not in ("xfrm", "solidFill")]
    return row


def theme_record(z, part):
    if not part:
        return unknown("no_theme_relationship")
    root = direct.xml(z, part)
    colors = root.find("a:themeElements/a:clrScheme", NS)
    fonts = root.find("a:themeElements/a:fontScheme", NS)
    return {"part": part, "name": root.get("name"), "status": "declarations_only",
            "colors": {direct.local(slot.tag): color_declaration(slot[0] if len(slot) else None)
                       for slot in colors} if colors is not None else {},
            "fonts": {direct.local(group.tag): [{"kind": direct.local(x.tag), **dict(x.attrib)} for x in group]
                      for group in fonts} if fonts is not None else {},
            "effective_theme": unknown("theme_overrides_language_font_fallback_and_color_maps_not_resolved")}


def part_record(obj, level, z, include_text):
    part = str(obj.part.partname).lstrip("/")
    root = direct.xml(z, part)
    maps = root.findall("p:clrMap", NS) + root.findall("p:clrMapOvr", NS)
    # txStyles excludes placeholder text; each declaration carries its own level/path.
    styles = root.find("p:txStyles", NS)
    return {"part": part, "name": obj.name,
            "shapes": [shape_record(s, level, include_text) for s in obj.shapes],
            "text_style_declarations": declarations(styles, level + ".text_styles", part) if styles is not None else [],
            "color_map_xml": [ET.tostring(x, encoding="unicode") for x in maps],
            "theme_override_part": direct.related(z, part, "themeOverride")}


def inspect(source, include_text=False):
    import pptx
    if pptx.__version__ != SUPPORTED_VERSION:
        raise ValueError(f"expected python-pptx=={SUPPORTED_VERSION}; found {pptx.__version__}")
    # Bound the decompressed input before python-pptx opens the entire OPC package.
    with ZipFile(source) as z:
        infos = z.infolist()
        if len(infos) > MAX_PARTS or sum(i.file_size for i in infos) > MAX_PACKAGE_BYTES:
            raise ValueError("package exceeds optional inspector limits")
        if len({i.filename for i in infos}) != len(infos):
            raise ValueError("duplicate ZIP part names")
        if any(i.file_size > direct.MAX_XML_BYTES for i in infos if i.filename.endswith((".xml", ".rels"))):
            raise ValueError("XML part exceeds inspection limit")
    report = direct.inspect(source, include_text)
    prs = pptx.Presentation(str(source))
    report.update({"format": "aprd-design-inspection/1", "library": {"name": "python-pptx", "version": pptx.__version__},
                   "observation_scope": "direct evidence plus library placeholder geometry; source-labelled style declarations only",
                   "layouts": {}, "masters": {}, "themes": {},
                   "limitations": ["not_rendered", "geometry_is_not_rendered_bounds", "no_complete_style_cascade",
                                   "theme_color_font_and_override_resolution_unknown", "no_layout_cloning",
                                   "only_slide_referenced_layouts_and_masters_inventoried"]})
    with ZipFile(source) as z:
        pres = direct.xml(z, "ppt/presentation.xml")
        default = pres.find("p:defaultTextStyle", NS)
        report["presentation_text_style_declarations"] = declarations(default, "presentation.default_text_style", "ppt/presentation.xml") if default is not None else []
        for slide, page in zip(prs.slides, report["slides"], strict=True):
            layout = slide.slide_layout
            master = layout.slide_master
            lp, mp = str(layout.part.partname).lstrip("/"), str(master.part.partname).lstrip("/")
            if (page["slide_part"], page["layout_part"], page["master_part"]) != (str(slide.part.partname).lstrip("/"), lp, mp):
                raise ValueError("library and direct relationship evidence disagree")
            page["design_objects"] = [shape_record(s, "slide", include_text) for s in slide.shapes]
            page["layout_name"] = layout.name
            page["theme_override_part"] = direct.related(z, page["slide_part"], "themeOverride")
            root = direct.xml(z, page["slide_part"])
            page["color_map_override_xml"] = [ET.tostring(x, encoding="unicode") for x in root.findall("p:clrMapOvr", NS)]
            if lp not in report["layouts"]:
                report["layouts"][lp] = part_record(layout, "layout", z, include_text)
                report["layouts"][lp]["master_part"] = mp
            if mp not in report["masters"]:
                report["masters"][mp] = part_record(master, "master", z, include_text)
                report["masters"][mp]["theme_part"] = page["theme_part"]
            if page["theme_part"] and page["theme_part"] not in report["themes"]:
                report["themes"][page["theme_part"]] = theme_record(z, page["theme_part"])
    if hashlib.sha256(source.read_bytes()).hexdigest() != report["sha256"]:
        raise ValueError("source changed during inspection")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--include-text", action="store_true", help="opt in to text (default: counts only)")
    args = parser.parse_args()
    try:
        if args.source.suffix.lower() != ".pptx" or args.output.suffix.lower() != ".json":
            raise ValueError("expected .pptx input and .json output")
        if args.output.exists():
            raise FileExistsError("output exists; choose a new path")
        report = inspect(args.source, args.include_text)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        print(f"PASS design_pages={report['page_count']} library=python-pptx/{SUPPORTED_VERSION} rendered=false")
        return 0
    except ImportError:
        print("FAIL optional dependency missing; install requirements-inspect-design.txt in a venv", file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, AttributeError, ET.ParseError, BadZipFile) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
