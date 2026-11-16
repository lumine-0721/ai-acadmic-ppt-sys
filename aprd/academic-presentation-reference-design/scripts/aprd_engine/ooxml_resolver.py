#!/usr/bin/env python3
"""Native PPTX Structure & OOXML Style Cascade Resolver.

Adapted in part from MIT-licensed open-source implementations:
- hugohe3/ppt-master (commit f3633771fe699d327f02ae86393831bbcf5169f7):
  beautify_identity.py & pptx_to_svg/color_resolver.py
- anotb/pptx-masters (v0.1.1):
  src/parser/colors.js & src/parser/master.js
- jameshemson-slides (commit 245c0e6858df9afe6a3381e36244812f3d2e4452):
  source/skills/build-deck/scripts/tokens.py & lint_pptx.py
"""

from __future__ import annotations

import colorsys
import hashlib
import io
import math
import posixpath
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from PIL import Image
import pptx

EMU_PER_INCH = 914400
EMU_PER_PT = 12700
MAX_PACKAGE_BYTES = 256 * 1024 * 1024
MAX_PARTS = 10000
MAX_XML_BYTES = 32 * 1024 * 1024

NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "dgm": "http://schemas.openxmlformats.org/drawingml/2006/diagram",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "pkg": "http://schemas.openxmlformats.org/package/2006/relationships",
}

DEFAULT_CLR_MAP = {
    "bg1": "lt1",
    "tx1": "dk1",
    "bg2": "lt2",
    "tx2": "dk2",
    "accent1": "accent1",
    "accent2": "accent2",
    "accent3": "accent3",
    "accent4": "accent4",
    "accent5": "accent5",
    "accent6": "accent6",
    "hlink": "hlink",
    "folHlink": "folHlink",
}

PRESET_COLORS = {
    "black": "000000",
    "white": "FFFFFF",
    "red": "FF0000",
    "green": "008000",
    "blue": "0000FF",
    "yellow": "FFFF00",
    "cyan": "00FFFF",
    "magenta": "FF00FF",
    "gray": "808080",
    "grey": "808080",
    "ltGray": "D3D3D3",
    "dkGray": "A9A9A9",
    "dimGray": "696969",
    "navy": "000080",
    "dkBlue": "00008B",
    "medBlue": "0000CD",
    "royalBlue": "4169E1",
    "steelBlue": "4682B4",
    "dodgerBlue": "1E90FF",
    "deepSkyBlue": "00BFFF",
    "skyBlue": "87CEEB",
    "ltBlue": "ADD8E6",
    "slateBlue": "6A5ACD",
    "dkSlateBlue": "483D8B",
    "slateGray": "708090",
    "dkSlateGray": "2F4F4F",
    "teal": "008080",
    "dkCyan": "008B8B",
    "turquoise": "40E0D0",
    "dkGreen": "006400",
    "forestGreen": "228B22",
    "seaGreen": "2E8B57",
    "olive": "808000",
    "maroon": "800000",
    "dkRed": "8B0000",
    "firebrick": "B22222",
    "crimson": "DC143C",
    "coral": "FF7F50",
    "tomato": "FF6347",
    "orange": "FFA500",
    "dkOrange": "FF8C00",
    "gold": "FFD700",
    "goldenrod": "DAA520",
    "dkGoldenrod": "B8860B",
    "purple": "800080",
    "indigo": "4B0082",
    "dkViolet": "9400D3",
    "brown": "A52A2A",
    "saddleBrown": "8B4513",
    "sienna": "A0522D",
    "tan": "D2B48C",
    "wheat": "F5DEB3",
    "beige": "F5F5DC",
    "ivory": "FFFFF0",
    "whiteSmoke": "F5F5F5",
    "gainsboro": "DCDCDC",
    "silver": "C0C0C0",
}


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_xml_part(zf: ZipFile, part_name: str) -> ET.Element | None:
    part_name = part_name.lstrip("/")
    try:
        info = zf.getinfo(part_name)
    except KeyError:
        return None
    if info.file_size > MAX_XML_BYTES:
        raise ValueError(f"XML part exceeds size limit: {part_name}")
    return ET.fromstring(zf.read(info))


def read_part_rels(zf: ZipFile, part_name: str) -> dict[str, tuple[str, str]]:
    part_name = part_name.lstrip("/")
    folder, name = posixpath.split(part_name)
    rel_part = posixpath.join(folder, "_rels", f"{name}.rels")
    root = read_xml_part(zf, rel_part)
    if root is None:
        return {}
    rels: dict[str, tuple[str, str]] = {}
    for rel in root.findall("pkg:Relationship", NS):
        if rel.get("TargetMode") == "External":
            continue
        rid = rel.get("Id", "")
        rtype = rel.get("Type", "").rsplit("/", 1)[-1]
        target = rel.get("Target", "")
        norm = posixpath.normpath(
            target.lstrip("/") if target.startswith("/") else posixpath.join(folder, target)
        )
        if norm == ".." or norm.startswith("../"):
            raise ValueError(f"Relationship escapes package root: {rid}")
        rels[rid] = (rtype, norm)
    return rels


def related_part(zf: ZipFile, part_name: str, rel_kind: str) -> str | None:
    for rtype, target in read_part_rels(zf, part_name).values():
        if rtype == rel_kind:
            return target
    return None


def _srgb_to_linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(channel: float) -> float:
    if channel <= 0.0:
        return 0.0
    if channel >= 1.0:
        return 1.0
    return channel * 12.92 if channel <= 0.0031308 else 1.055 * (channel ** (1.0 / 2.4)) - 0.055


def apply_ooxml_color_modifiers(base_hex: str, color_node: ET.Element) -> tuple[str, float, list[dict[str, Any]]]:
    """Resolve OOXML color modifiers (lumMod, lumOff, tint, shade, satMod, satOff, alpha)."""
    clean = base_hex.lstrip("#").upper()
    if len(clean) != 6:
        return f"#{clean}", 1.0, []
    r = int(clean[0:2], 16) / 255.0
    g = int(clean[2:4], 16) / 255.0
    b = int(clean[4:6], 16) / 255.0
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    alpha = 1.0
    mods: list[dict[str, Any]] = []

    for child in color_node:
        kind = local_name(child.tag)
        raw_val = child.get("val")
        mods.append({"kind": kind, "val": raw_val})
        if raw_val is None:
            continue
        try:
            ratio = int(raw_val) / 100000.0
        except ValueError:
            continue
        if kind == "lumMod":
            l = max(0.0, min(1.0, l * ratio))
            r, g, b = colorsys.hls_to_rgb(h, l, s)
        elif kind == "lumOff":
            l = max(0.0, min(1.0, l + ratio))
            r, g, b = colorsys.hls_to_rgb(h, l, s)
        elif kind == "satMod":
            s = max(0.0, min(1.0, s * ratio))
            r, g, b = colorsys.hls_to_rgb(h, l, s)
        elif kind == "satOff":
            s = max(0.0, min(1.0, s + ratio))
            r, g, b = colorsys.hls_to_rgb(h, l, s)
        elif kind == "tint":
            lr, lg, lb = _srgb_to_linear(r), _srgb_to_linear(g), _srgb_to_linear(b)
            lr = lr * ratio + (1.0 - ratio)
            lg = lg * ratio + (1.0 - ratio)
            lb = lb * ratio + (1.0 - ratio)
            r, g, b = _linear_to_srgb(lr), _linear_to_srgb(lg), _linear_to_srgb(lb)
            h, l, s = colorsys.rgb_to_hls(r, g, b)
        elif kind == "shade":
            lr, lg, lb = _srgb_to_linear(r), _srgb_to_linear(g), _srgb_to_linear(b)
            lr, lg, lb = lr * ratio, lg * ratio, lb * ratio
            r, g, b = _linear_to_srgb(lr), _linear_to_srgb(lg), _linear_to_srgb(lb)
            h, l, s = colorsys.rgb_to_hls(r, g, b)
        elif kind == "alpha":
            alpha = max(0.0, min(1.0, ratio))

    ri = max(0, min(255, round(r * 255)))
    gi = max(0, min(255, round(g * 255)))
    bi = max(0, min(255, round(b * 255)))
    return f"#{ri:02X}{gi:02X}{bi:02X}", round(alpha, 4), mods


def luminance_of_hex(hex_str: str) -> float:
    clean = (hex_str or "").lstrip("#")
    if len(clean) != 6:
        return 255.0
    r = int(clean[0:2], 16)
    g = int(clean[2:4], 16)
    b = int(clean[4:6], 16)
    return round(0.299 * r + 0.587 * g + 0.114 * b, 2)


def parse_theme_part(zf: ZipFile, theme_part: str | None) -> dict[str, Any]:
    if not theme_part:
        return {"part": None, "name": None, "colors": {}, "fonts": {"major": {}, "minor": {}}}
    root = read_xml_part(zf, theme_part)
    if root is None:
        return {"part": theme_part, "name": None, "colors": {}, "fonts": {"major": {}, "minor": {}}}

    colors: dict[str, str] = {}
    clr_scheme = root.find("a:themeElements/a:clrScheme", NS)
    if clr_scheme is not None:
        for slot in clr_scheme:
            slot_name = local_name(slot.tag)
            if not len(slot):
                continue
            cnode = slot[0]
            ckind = local_name(cnode.tag)
            raw_hex = None
            if ckind == "srgbClr":
                raw_hex = cnode.get("val")
            elif ckind == "sysClr":
                raw_hex = cnode.get("lastClr") or ("000000" if "Text" in (cnode.get("val") or "") else "FFFFFF")
            elif ckind == "prstClr":
                raw_hex = PRESET_COLORS.get(cnode.get("val", ""))
            if raw_hex:
                resolved, _, _ = apply_ooxml_color_modifiers(raw_hex, cnode)
                colors[slot_name] = resolved

    fonts: dict[str, dict[str, Any]] = {"major": {}, "minor": {}}
    font_scheme = root.find("a:themeElements/a:fontScheme", NS)
    if font_scheme is not None:
        for xml_tag, out_key in (("majorFont", "major"), ("minorFont", "minor")):
            fnode = font_scheme.find(f"a:{xml_tag}", NS)
            if fnode is None:
                continue
            entry: dict[str, Any] = {}
            for script_tag in ("latin", "ea", "cs"):
                sub = fnode.find(f"a:{script_tag}", NS)
                if sub is not None and sub.get("typeface"):
                    entry[script_tag] = sub.get("typeface")
            scripts: dict[str, str] = {}
            for sub in fnode.findall("a:font", NS):
                sc = sub.get("script")
                tf = sub.get("typeface")
                if sc in ("Hans", "Hant", "Jpan", "Hang") and tf:
                    scripts[sc] = tf
            if scripts:
                entry["scripts"] = scripts
            fonts[out_key] = entry

    return {
        "part": theme_part,
        "name": root.get("name"),
        "colors": colors,
        "fonts": fonts,
    }


def parse_clr_map(master_root: ET.Element | None, layout_root: ET.Element | None = None, slide_root: ET.Element | None = None) -> dict[str, str]:
    cmap = dict(DEFAULT_CLR_MAP)
    if master_root is not None:
        cm_el = master_root.find("p:clrMap", NS)
        if cm_el is not None:
            for k, v in cm_el.attrib.items():
                cmap[local_name(k)] = v
    for override_owner in (layout_root, slide_root):
        if override_owner is None:
            continue
        ovr = override_owner.find("p:clrMapOvr/a:overrideClrMapping", NS)
        if ovr is not None:
            for k, v in ovr.attrib.items():
                cmap[local_name(k)] = v
    return cmap


def resolve_color_node(
    color_node: ET.Element | None,
    theme_colors: dict[str, str],
    clr_map: dict[str, str],
    ph_color_hex: str | None = None,
) -> dict[str, Any]:
    if color_node is None:
        return {"status": "none", "resolved_hex": None, "alpha": 1.0, "scheme_slot": None, "modifiers": []}
    kind = local_name(color_node.tag)
    raw_hex: str | None = None
    scheme_slot: str | None = None

    if kind == "srgbClr":
        raw_hex = color_node.get("val")
    elif kind == "sysClr":
        raw_hex = color_node.get("lastClr") or ("000000" if "Text" in (color_node.get("val") or "") else "FFFFFF")
    elif kind == "prstClr":
        raw_hex = PRESET_COLORS.get(color_node.get("val", ""))
    elif kind == "schemeClr":
        val = color_node.get("val", "")
        scheme_slot = val
        if val == "phClr" and ph_color_hex:
            raw_hex = ph_color_hex.lstrip("#")
        else:
            mapped = clr_map.get(val, val)
            resolved_slot = theme_colors.get(mapped) or theme_colors.get(val)
            if resolved_slot:
                raw_hex = resolved_slot.lstrip("#")

    if not raw_hex:
        return {
            "status": "unresolved",
            "kind": kind,
            "attributes": dict(color_node.attrib),
            "resolved_hex": None,
            "alpha": 1.0,
            "scheme_slot": scheme_slot,
            "modifiers": [{"kind": local_name(c.tag), "val": c.get("val")} for c in color_node],
        }

    final_hex, alpha, mods = apply_ooxml_color_modifiers(raw_hex, color_node)
    return {
        "status": "resolved",
        "kind": kind,
        "unmodified_hex": f"#{raw_hex.lstrip('#').upper()}",
        "resolved_hex": final_hex,
        "alpha": alpha,
        "scheme_slot": scheme_slot,
        "modifiers": mods,
    }


def resolve_font_ref(typeface: str | None, theme_fonts: dict[str, dict[str, Any]]) -> str | None:
    if not typeface:
        return None
    tf = typeface.strip()
    if not tf.startswith("+"):
        return tf
    major = theme_fonts.get("major", {})
    minor = theme_fonts.get("minor", {})
    mapping = {
        "+mj-lt": major.get("latin"),
        "+mj-ea": major.get("ea") or (major.get("scripts", {}) or {}).get("Hans") or major.get("latin"),
        "+mj-cs": major.get("cs") or major.get("latin"),
        "+mn-lt": minor.get("latin"),
        "+mn-ea": minor.get("ea") or (minor.get("scripts", {}) or {}).get("Hans") or minor.get("latin"),
        "+mn-cs": minor.get("cs") or minor.get("latin"),
    }
    return mapping.get(tf, tf)


def parse_tx_styles(master_root: ET.Element | None, theme_colors: dict[str, str], clr_map: dict[str, str], theme_fonts: dict[str, dict[str, Any]]) -> dict[str, dict[int, dict[str, Any]]]:
    out: dict[str, dict[int, dict[str, Any]]] = {"title": {}, "body": {}, "other": {}}
    if master_root is None:
        return out
    tx_styles = master_root.find("p:txStyles", NS)
    if tx_styles is None:
        return out
    for xml_tag, key in (("titleStyle", "title"), ("bodyStyle", "body"), ("otherStyle", "other")):
        style_node = tx_styles.find(f"p:{xml_tag}", NS)
        if style_node is None:
            continue
        out[key] = _parse_level_paragraphs(style_node, theme_colors, clr_map, theme_fonts, f"master.txStyles.{xml_tag}")
    return out


def _parse_level_paragraphs(
    container: ET.Element | None,
    theme_colors: dict[str, str],
    clr_map: dict[str, str],
    theme_fonts: dict[str, dict[str, Any]],
    source_label: str,
) -> dict[int, dict[str, Any]]:
    levels: dict[int, dict[str, Any]] = {}
    if container is None:
        return levels
    for lvl in range(1, 10):
        ppr = container.find(f"a:lvl{lvl}pPr", NS)
        if ppr is None and lvl == 1:
            ppr = container.find("a:defPPr", NS)
        if ppr is None:
            continue
        defrpr = ppr.find("a:defRPr", NS)
        info: dict[str, Any] = {"source": f"{source_label}.lvl{lvl}", "alignment": ppr.get("algn")}
        if defrpr is not None:
            sz = defrpr.get("sz")
            if sz and sz.isdigit():
                info["font_size_pt"] = round(int(sz) / 100.0, 2)
            if "b" in defrpr.attrib:
                info["bold"] = defrpr.get("b") in ("1", "true")
            if "i" in defrpr.attrib:
                info["italic"] = defrpr.get("i") in ("1", "true")
            for tag, fkey in (("latin", "font_latin"), ("ea", "font_ea"), ("cs", "font_cs")):
                f_el = defrpr.find(f"a:{tag}", NS)
                if f_el is not None and f_el.get("typeface"):
                    info[fkey] = resolve_font_ref(f_el.get("typeface"), theme_fonts)
            sfill = defrpr.find("a:solidFill", NS)
            if sfill is not None and len(sfill):
                c_res = resolve_color_node(sfill[0], theme_colors, clr_map)
                if c_res.get("resolved_hex"):
                    info["color_hex"] = c_res["resolved_hex"]
                    info["color_detail"] = c_res
        levels[lvl] = info
    return levels


def _extract_fill_from_sppr(
    sp_pr: ET.Element | None,
    theme_colors: dict[str, str],
    clr_map: dict[str, str],
) -> dict[str, Any]:
    if sp_pr is None:
        return {"type": "inherited_or_none", "color_hex": None, "alpha": 1.0}
    if sp_pr.find("a:noFill", NS) is not None:
        return {"type": "none", "color_hex": None, "alpha": 0.0}
    sfill = sp_pr.find("a:solidFill", NS)
    if sfill is not None and len(sfill):
        c_res = resolve_color_node(sfill[0], theme_colors, clr_map)
        return {
            "type": "solid",
            "color_hex": c_res.get("resolved_hex"),
            "alpha": c_res.get("alpha", 1.0),
            "detail": c_res,
        }
    gfill = sp_pr.find("a:gradFill", NS)
    if gfill is not None:
        stops = []
        for gs in gfill.findall("a:gsLst/a:gs", NS):
            if len(gs):
                cres = resolve_color_node(gs[0], theme_colors, clr_map)
                stops.append({"pos": gs.get("pos"), "color_hex": cres.get("resolved_hex")})
        dom_hex = stops[0]["color_hex"] if stops else None
        return {"type": "gradient", "color_hex": dom_hex, "alpha": 1.0, "stops": stops}
    bfill = sp_pr.find("a:blipFill", NS)
    if bfill is not None:
        blip = bfill.find("a:blip", NS)
        return {"type": "picture", "color_hex": None, "alpha": 1.0, "embed": blip.get(f"{{{NS['r']}}}embed") if blip is not None else None}
    return {"type": "inherited_or_none", "color_hex": None, "alpha": 1.0}


def _extract_line_and_effects(
    sp_pr: ET.Element | None,
    theme_colors: dict[str, str],
    clr_map: dict[str, str],
) -> tuple[dict[str, Any] | None, list[str]]:
    if sp_pr is None:
        return None, []
    ln = sp_pr.find("a:ln", NS)
    line_info = None
    if ln is not None and ln.find("a:noFill", NS) is None:
        w_emu = int(ln.get("w", "12700")) if ln.get("w", "").isdigit() else 12700
        sfill = ln.find("a:solidFill", NS)
        c_hex = None
        if sfill is not None and len(sfill):
            c_hex = resolve_color_node(sfill[0], theme_colors, clr_map).get("resolved_hex")
        prst_dash = ln.find("a:prstDash", NS)
        line_info = {
            "width_pt": round(w_emu / EMU_PER_PT, 2),
            "color_hex": c_hex,
            "dash": prst_dash.get("val", "solid") if prst_dash is not None else "solid",
        }
    effects: list[str] = []
    eff_lst = sp_pr.find("a:effectLst", NS)
    if eff_lst is not None:
        effects.extend(local_name(ch.tag) for ch in eff_lst)
    return line_info, effects


def _extract_preset_geom(sp_pr: ET.Element | None) -> tuple[str | None, dict[str, int]]:
    if sp_pr is None:
        return None, {}
    prst = sp_pr.find("a:prstGeom", NS)
    if prst is None:
        if sp_pr.find("a:custGeom", NS) is not None:
            return "custom_geometry", {}
        return None, {}
    geom_name = prst.get("prst")
    av: dict[str, int] = {}
    for gd in prst.findall("a:avLst/a:gd", NS):
        name = gd.get("name")
        fmla = gd.get("fmla", "")
        if name and fmla.startswith("val "):
            try:
                av[name] = int(fmla.split()[1])
            except (IndexError, ValueError):
                pass
    return geom_name, av


def _compose_group_box(
    local_box_emu: list[int],
    group_chain_transforms: list[dict[str, list[int]]],
) -> list[int]:
    """Compose nested group local coordinates (chOff/chExt -> off/ext) into slide EMU coordinates."""
    x, y, w, h = [float(v) for v in local_box_emu]
    for tr in reversed(group_chain_transforms):
        off_x, off_y = tr["off"]
        ext_w, ext_h = tr["ext"]
        ch_off_x, ch_off_y = tr["ch_off"]
        ch_ext_w, ch_ext_h = tr["ch_ext"]
        scale_x = (ext_w / ch_ext_w) if ch_ext_w else 1.0
        scale_y = (ext_h / ch_ext_h) if ch_ext_h else 1.0
        x = off_x + (x - ch_off_x) * scale_x
        y = off_y + (y - ch_off_y) * scale_y
        w = w * scale_x
        h = h * scale_y
    return [round(x), round(y), round(w), round(h)]


def _placeholder_style_role(ph_type: str | None) -> str:
    if not ph_type:
        return "other"
    p = ph_type.lower()
    if "title" in p and "sub" not in p:
        return "title"
    if any(k in p for k in ("body", "sub", "obj")):
        return "body"
    return "other"


def _resolve_placeholder_chain(shape) -> list[tuple[str, Any]]:
    chain = [("slide", shape)]
    if not getattr(shape, "is_placeholder", False):
        return chain
    for lvl in ("layout", "master"):
        try:
            base = getattr(chain[-1][1], "_base_placeholder", None)
        except Exception:
            break
        if base is None:
            break
        chain.append((lvl, base))
    return chain


def _extract_background_cascade(
    zf: ZipFile,
    slide_part: str,
    layout_part: str | None,
    master_part: str | None,
    theme_colors: dict[str, str],
    clr_map: dict[str, str],
    canvas_w: int,
    canvas_h: int,
) -> dict[str, Any]:
    for level, part in (("slide", slide_part), ("layout", layout_part), ("master", master_part)):
        if not part:
            continue
        root = read_xml_part(zf, part)
        if root is None:
            continue
        bg_pr = root.find("p:cSld/p:bg/p:bgPr", NS)
        if bg_pr is not None:
            fill = _extract_fill_from_sppr(bg_pr, theme_colors, clr_map)
            if fill["type"] != "inherited_or_none":
                return {"source_level": level, "part": part, **fill}
        bg_ref = root.find("p:cSld/p:bg/p:bgRef", NS)
        if bg_ref is not None and len(bg_ref):
            cres = resolve_color_node(bg_ref[0], theme_colors, clr_map)
            if cres.get("resolved_hex"):
                return {"source_level": level, "part": part, "type": "solid", "color_hex": cres["resolved_hex"], "alpha": cres.get("alpha", 1.0)}
        # Also check full-bleed background picture in layout/master spTree
        if level in ("layout", "master"):
            rels = read_part_rels(zf, part)
            for pic in root.findall("p:cSld/p:spTree/p:pic", NS):
                xfrm = pic.find("p:spPr/a:xfrm", NS)
                ext = xfrm.find("a:ext", NS) if xfrm is not None else None
                if ext is not None:
                    cx = int(ext.get("cx", "0"))
                    cy = int(ext.get("cy", "0"))
                    if cx >= canvas_w * 0.94 and cy >= canvas_h * 0.94:
                        blip = pic.find("p:blipFill/a:blip", NS)
                        rid = blip.get(f"{{{NS['r']}}}embed") if blip is not None else None
                        media_target = rels.get(rid, (None, None))[1] if rid else None
                        return {
                            "source_level": level,
                            "part": part,
                            "type": "full_bleed_picture",
                            "color_hex": theme_colors.get(clr_map.get("bg1", "lt1"), "#FFFFFF"),
                            "alpha": 1.0,
                            "media_part": media_target,
                        }
    default_bg = theme_colors.get(clr_map.get("bg1", "lt1"), "#FFFFFF")
    return {"source_level": "theme_default", "part": None, "type": "solid", "color_hex": default_bg, "alpha": 1.0}


def _parse_chart_part(zf: ZipFile, chart_part: str) -> dict[str, Any]:
    root = read_xml_part(zf, chart_part)
    if root is None:
        return {"part": chart_part, "chart_type": "unknown", "series_count": 0, "category_count": 0}
    plot_area = root.find(".//c:plotArea", NS)
    chart_types: list[str] = []
    series_count = 0
    cat_count = 0
    if plot_area is not None:
        for child in plot_area:
            cname = local_name(child.tag)
            if cname.endswith("Chart"):
                chart_types.append(cname)
                sers = child.findall("c:ser", NS)
                series_count += len(sers)
                if sers and not cat_count:
                    pts = sers[0].findall(".//c:cat//c:pt", NS) or sers[0].findall(".//c:xVal//c:pt", NS)
                    cat_count = len(pts)
    title_texts = [t.text or "" for t in root.findall(".//c:title//a:t", NS) if t.text]
    return {
        "part": chart_part,
        "chart_type": "+".join(chart_types) if chart_types else "customChart",
        "series_count": series_count,
        "category_count": cat_count,
        "has_title": bool(title_texts),
        "title_length": len("".join(title_texts)),
    }


def _extract_static_master_chrome(
    zf: ZipFile,
    part_name: str,
    level: str,
    theme_colors: dict[str, str],
    clr_map: dict[str, str],
    canvas_w: int,
    canvas_h: int,
) -> list[dict[str, Any]]:
    root = read_xml_part(zf, part_name)
    if root is None:
        return []
    sp_tree = root.find("p:cSld/p:spTree", NS)
    if sp_tree is None:
        return []
    rels = read_part_rels(zf, part_name)
    chrome: list[dict[str, Any]] = []
    for node in sp_tree:
        kind = local_name(node.tag)
        if kind not in ("sp", "pic", "cxnSp"):
            continue
        ph = node.find(".//p:nvPr/p:ph", NS)
        if ph is not None:
            continue
        c_nv = node.find(".//p:cNvPr", NS)
        if c_nv is not None and c_nv.get("hidden") in ("1", "true"):
            continue
        xfrm = node.find("p:spPr/a:xfrm", NS)
        if xfrm is None:
            continue
        off = xfrm.find("a:off", NS)
        ext = xfrm.find("a:ext", NS)
        if off is None or ext is None:
            continue
        x, y = int(off.get("x", "0")), int(off.get("y", "0"))
        w, h = int(ext.get("cx", "0")), int(ext.get("cy", "0"))
        box_pct = [
            round(x / canvas_w * 100.0, 4),
            round(y / canvas_h * 100.0, 4),
            round(w / canvas_w * 100.0, 4),
            round(h / canvas_h * 100.0, 4),
        ]
        fill = _extract_fill_from_sppr(node.find("p:spPr", NS), theme_colors, clr_map)
        media_part = None
        if kind == "pic":
            blip = node.find("p:blipFill/a:blip", NS)
            rid = blip.get(f"{{{NS['r']}}}embed") if blip is not None else None
            if rid and rid in rels:
                media_part = rels[rid][1]
        chrome.append({
            "level": level,
            "part": part_name,
            "object_id": c_nv.get("id") if c_nv is not None else None,
            "name": c_nv.get("name") if c_nv is not None else "",
            "object_type": kind,
            "box_emu": [x, y, w, h],
            "box_percent": box_pct,
            "fill": fill,
            "media_part": media_part,
            "is_full_bleed": (w >= canvas_w * 0.94 and h >= canvas_h * 0.94),
        })
    return chrome


def resolve_presentation_structure(pptx_path: Path, scrub_text: bool = True) -> dict[str, Any]:
    """Parse a PPTX package and resolve OOXML inheritance across Theme, Master, Layout, and Slide."""
    source_path = Path(pptx_path).resolve()
    before_hash = sha256_file(source_path)

    with ZipFile(source_path) as zf:
        infos = zf.infolist()
        if len(infos) > MAX_PARTS or sum(i.file_size for i in infos) > MAX_PACKAGE_BYTES:
            raise ValueError("PPTX archive exceeds safety limits")

        pres_root = read_xml_part(zf, "ppt/presentation.xml")
        if pres_root is None:
            raise ValueError("Missing ppt/presentation.xml")
        sld_sz = pres_root.find("p:sldSz", NS)
        if sld_sz is None:
            raise ValueError("Missing p:sldSz in presentation.xml")
        canvas_w = int(sld_sz.get("cx", "0"))
        canvas_h = int(sld_sz.get("cy", "0"))
        if min(canvas_w, canvas_h) <= 0:
            raise ValueError("Invalid non-positive canvas dimensions")

        prs = pptx.Presentation(str(source_path))
        pres_default_styles = _parse_level_paragraphs(
            pres_root.find("p:defaultTextStyle", NS),
            {},
            DEFAULT_CLR_MAP,
            {"major": {}, "minor": {}},
            "presentation.defaultTextStyle",
        )

        slides_data: list[dict[str, Any]] = []
        themes_cache: dict[str, dict[str, Any]] = {}
        masters_cache: dict[str, dict[str, Any]] = {}
        layouts_cache: dict[str, dict[str, Any]] = {}
        media_assets: dict[str, dict[str, Any]] = {}
        unsupported_features: list[dict[str, Any]] = []

        observed_font_chars: dict[str, int] = {}
        observed_size_chars: dict[float, int] = {}
        observed_colors_count: dict[str, int] = {}

        slide_carriers = [(idx, s, False) for idx, s in enumerate(prs.slides, start=1)]
        if not slide_carriers and len(prs.slide_layouts) > 0:
            unsupported_features.append({
                "slide_number": 1,
                "object_id": "presentation.sldIdLst",
                "feature_kind": "Zero-slide template-only PPTX (layouts without slide instances)",
                "support_level": "degraded_synthesized_from_layouts",
                "fallback_action": "Extracted layout patterns directly from SlideLayouts and SlideMasters",
            })
            slide_carriers = [(idx, l, True) for idx, l in enumerate(prs.slide_layouts, start=1)]

        for slide_idx, slide, is_layout_carrier in slide_carriers:
            slide_part = str(slide.part.partname).lstrip("/")
            layout = slide if is_layout_carrier else slide.slide_layout
            master = layout.slide_master
            layout_part = str(layout.part.partname).lstrip("/")
            master_part = str(master.part.partname).lstrip("/")
            theme_part = related_part(zf, master_part, "theme") or related_part(zf, "ppt/presentation.xml", "theme")

            if theme_part not in themes_cache:
                themes_cache[theme_part or ""] = parse_theme_part(zf, theme_part)
            theme_info = themes_cache[theme_part or ""]
            theme_colors = theme_info["colors"]
            theme_fonts = theme_info["fonts"]

            master_root = read_xml_part(zf, master_part)
            layout_root = read_xml_part(zf, layout_part)
            slide_root = read_xml_part(zf, slide_part)

            clr_map = parse_clr_map(master_root, layout_root, slide_root)
            tx_styles = parse_tx_styles(master_root, theme_colors, clr_map, theme_fonts)

            if master_part not in masters_cache:
                masters_cache[master_part] = {
                    "part": master_part,
                    "theme_part": theme_part,
                    "clr_map": clr_map,
                    "tx_styles": tx_styles,
                    "static_chrome": _extract_static_master_chrome(
                        zf, master_part, "master", theme_colors, clr_map, canvas_w, canvas_h
                    ),
                }
            if layout_part not in layouts_cache:
                show_master_sp = True
                if layout_root is not None:
                    show_master_sp = layout_root.get("showMasterSp", "1") not in ("0", "false")
                layouts_cache[layout_part] = {
                    "part": layout_part,
                    "name": layout.name,
                    "layout_type": layout_root.get("type") if layout_root is not None else None,
                    "master_part": master_part,
                    "show_master_shapes": show_master_sp,
                    "static_chrome": _extract_static_master_chrome(
                        zf, layout_part, "layout", theme_colors, clr_map, canvas_w, canvas_h
                    ),
                }

            bg_info = _extract_background_cascade(
                zf, slide_part, layout_part, master_part, theme_colors, clr_map, canvas_w, canvas_h
            )
            if bg_info.get("color_hex"):
                observed_colors_count[bg_info["color_hex"]] = observed_colors_count.get(bg_info["color_hex"], 0) + 1

            slide_rels = read_part_rels(zf, slide_part)

            def walk_shapes(shapes_iter, group_transforms: list[dict[str, list[int]]], depth: int = 0) -> list[dict[str, Any]]:
                items: list[dict[str, Any]] = []
                for z_idx, sh in enumerate(shapes_iter, start=1):
                    raw_el = ET.fromstring(sh.element.xml)
                    tag_kind = local_name(raw_el.tag)
                    c_nv = raw_el.find(".//p:cNvPr", NS)
                    if c_nv is not None and c_nv.get("hidden") in ("1", "true"):
                        continue

                    chain = _resolve_placeholder_chain(sh)
                    ph_info = None
                    if getattr(sh, "is_placeholder", False):
                        ph_info = {
                            "idx": sh.placeholder_format.idx,
                            "type": str(sh.placeholder_format.type).split(".")[-1].split()[0],
                        }

                    # Geometry resolution (direct or inherited via placeholder chain, plus group affine composition)
                    left_emu = getattr(sh, "left", None)
                    top_emu = getattr(sh, "top", None)
                    width_emu = getattr(sh, "width", None)
                    height_emu = getattr(sh, "height", None)
                    geom_source = "slide"
                    for lvl_name, candidate_sh in chain:
                        cand_el = ET.fromstring(candidate_sh.element.xml)
                        if cand_el.find(".//a:xfrm/a:off", NS) is not None:
                            geom_source = lvl_name
                            break

                    local_box = [
                        int(left_emu or 0),
                        int(top_emu or 0),
                        int(width_emu or 0),
                        int(height_emu or 0),
                    ]
                    world_box = (
                        _compose_group_box(local_box, group_transforms)
                        if group_transforms
                        else list(local_box)
                    )
                    box_pct = [
                        round(world_box[0] / canvas_w * 100.0, 4),
                        round(world_box[1] / canvas_h * 100.0, 4),
                        round(world_box[2] / canvas_w * 100.0, 4),
                        round(world_box[3] / canvas_h * 100.0, 4),
                    ]
                    box_ratio = [round(v / 100.0, 4) for v in box_pct]

                    xfrm_node = next(
                        (n for n in (raw_el.find("p:spPr/a:xfrm", NS), raw_el.find("p:grpSpPr/a:xfrm", NS), raw_el.find("p:xfrm", NS)) if n is not None),
                        None,
                    )
                    rot_deg = round(int(xfrm_node.get("rot", "0")) / 60000.0, 2) if xfrm_node is not None and xfrm_node.get("rot") else 0.0

                    sp_pr = raw_el.find("p:spPr", NS)
                    fill_info = _extract_fill_from_sppr(sp_pr, theme_colors, clr_map)
                    if fill_info["type"] == "inherited_or_none" and len(chain) > 1:
                        for lvl_name, base_sh in chain[1:]:
                            base_el = ET.fromstring(base_sh.element.xml)
                            bf = _extract_fill_from_sppr(base_el.find("p:spPr", NS), theme_colors, clr_map)
                            if bf["type"] != "inherited_or_none":
                                fill_info = {**bf, "inherited_from": lvl_name}
                                break
                    if fill_info.get("color_hex"):
                        observed_colors_count[fill_info["color_hex"]] = observed_colors_count.get(fill_info["color_hex"], 0) + 1

                    line_info, effects = _extract_line_and_effects(sp_pr, theme_colors, clr_map)
                    prst_geom, av_lst = _extract_preset_geom(sp_pr)

                    obj_record: dict[str, Any] = {
                        "shape_id": sh.shape_id,
                        "name": sh.name,
                        "object_type": tag_kind,
                        "z_order": z_idx,
                        "depth": depth,
                        "placeholder": ph_info,
                        "geometry_source": geom_source,
                        "local_box_emu": local_box,
                        "world_box_emu": world_box,
                        "box_percent": box_pct,
                        "box_ratio": box_ratio,
                        "rotation_deg": rot_deg,
                        "preset_geometry": prst_geom,
                        "adjustments": av_lst,
                        "fill": fill_info,
                        "line": line_info,
                        "effects": effects,
                    }

                    # Group shape recursion with affine child space
                    if tag_kind == "grpSp":
                        ch_off = xfrm_node.find("a:chOff", NS) if xfrm_node is not None else None
                        ch_ext = xfrm_node.find("a:chExt", NS) if xfrm_node is not None else None
                        tr_entry = {
                            "off": [local_box[0], local_box[1]],
                            "ext": [local_box[2], local_box[3]],
                            "ch_off": [
                                int(ch_off.get("x", str(local_box[0]))) if ch_off is not None else local_box[0],
                                int(ch_off.get("y", str(local_box[1]))) if ch_off is not None else local_box[1],
                            ],
                            "ch_ext": [
                                int(ch_ext.get("cx", str(local_box[2]))) if ch_ext is not None else local_box[2],
                                int(ch_ext.get("cy", str(local_box[3]))) if ch_ext is not None else local_box[3],
                            ],
                        }
                        obj_record["children"] = walk_shapes(sh.shapes, group_transforms + [tr_entry], depth + 1)
                        items.append(obj_record)
                        continue

                    # Picture metadata & crop
                    if tag_kind == "pic":
                        blip = raw_el.find("p:blipFill/a:blip", NS)
                        src_rect = raw_el.find("p:blipFill/a:srcRect", NS)
                        rid = blip.get(f"{{{NS['r']}}}embed") if blip is not None else None
                        media_target = slide_rels.get(rid, (None, None))[1] if rid else None
                        px_w = px_h = None
                        img_sha = None
                        if media_target:
                            try:
                                blob = zf.read(media_target)
                                img_sha = sha256_bytes(blob)
                                with Image.open(io.BytesIO(blob)) as im:
                                    px_w, px_h = im.size
                            except Exception:
                                pass
                            if media_target not in media_assets:
                                media_assets[media_target] = {
                                    "media_part": media_target,
                                    "sha256": img_sha,
                                    "pixel_size": [px_w, px_h],
                                    "used_on_slides": [slide_idx],
                                }
                            elif slide_idx not in media_assets[media_target]["used_on_slides"]:
                                media_assets[media_target]["used_on_slides"].append(slide_idx)
                        obj_record["picture"] = {
                            "media_part": media_target,
                            "sha256": img_sha,
                            "pixel_size": [px_w, px_h],
                            "crop": dict(src_rect.attrib) if src_rect is not None else None,
                        }

                    # GraphicFrame: Table, Chart, SmartArt, OLE
                    if tag_kind == "graphicFrame":
                        gdata = raw_el.find("a:graphic/a:graphicData", NS)
                        uri = gdata.get("uri", "") if gdata is not None else ""
                        obj_record["graphic_uri"] = uri
                        if getattr(sh, "has_table", False):
                            tbl = sh.table
                            col_widths_pct = [round((c.width or 0) / canvas_w * 100.0, 2) for c in tbl.columns]
                            cell_samples = []
                            for r_i, row in enumerate(tbl.rows):
                                for c_i, cell in enumerate(row.cells):
                                    tc = cell._tc
                                    cell_samples.append({
                                        "row": r_i,
                                        "col": c_i,
                                        "char_count": len(cell.text.strip()),
                                        "row_span": int(tc.get("rowSpan", "1")),
                                        "grid_span": int(tc.get("gridSpan", "1")),
                                    })
                            obj_record["table"] = {
                                "rows": len(tbl.rows),
                                "cols": len(tbl.columns),
                                "col_widths_percent": col_widths_pct,
                                "cells_meta": cell_samples,
                            }
                        elif "chart" in uri:
                            c_node = raw_el.find(".//c:chart", NS)
                            c_rid = c_node.get(f"{{{NS['r']}}}id") if c_node is not None else None
                            c_part = slide_rels.get(c_rid, (None, None))[1] if c_rid else None
                            if c_part:
                                obj_record["chart"] = _parse_chart_part(zf, c_part)
                        elif "diagram" in uri:
                            unsupported_features.append({
                                "slide_number": slide_idx,
                                "object_id": str(sh.shape_id),
                                "feature_kind": "SmartArt (drawingml/diagram)",
                                "support_level": "degraded_preserved",
                                "fallback_action": "Preserve bounding box and original XML; mark as diagram slot",
                            })
                        else:
                            unsupported_features.append({
                                "slide_number": slide_idx,
                                "object_id": str(sh.shape_id),
                                "feature_kind": f"graphicFrame ({uri or 'unknown'})",
                                "support_level": "opaque_preserved",
                                "fallback_action": "Retain original graphicFrame XML without fabricating internals",
                            })

                    # OMML math equation detection
                    if raw_el.find(".//m:oMath", NS) is not None or raw_el.find(".//m:oMathPara", NS) is not None:
                        obj_record["has_omml_formula"] = True
                        unsupported_features.append({
                            "slide_number": slide_idx,
                            "object_id": str(sh.shape_id),
                            "feature_kind": "OMML Formula (m:oMath)",
                            "support_level": "degraded_preserved",
                            "fallback_action": "Preserve native OMML equation runs and classify as FORMULA_SLOT",
                        })

                    # Text & OOXML Typography Cascade
                    if getattr(sh, "has_text_frame", False):
                        ph_role = _placeholder_style_role(ph_info["type"] if ph_info else None)
                        # Build cascade lookup per outline level
                        shape_lst = _parse_level_paragraphs(
                            raw_el.find("p:txBody/a:lstStyle", NS),
                            theme_colors, clr_map, theme_fonts, "slide.shape.lstStyle"
                        )
                        inh_lst_chain: list[dict[int, dict[str, Any]]] = []
                        for lvl_name, base_sh in chain[1:]:
                            base_el = ET.fromstring(base_sh.element.xml)
                            inh_lst_chain.append(
                                _parse_level_paragraphs(
                                    base_el.find("p:txBody/a:lstStyle", NS),
                                    theme_colors, clr_map, theme_fonts, f"{lvl_name}.placeholder.lstStyle"
                                )
                            )

                        paras_out: list[dict[str, Any]] = []
                        total_chars = 0
                        non_empty_paras = 0
                        for p_el in raw_el.findall("p:txBody/a:p", NS):
                            ppr = p_el.find("a:pPr", NS)
                            lvl_idx = int(ppr.get("lvl", "0")) + 1 if ppr is not None and ppr.get("lvl", "").isdigit() else 1
                            algn = ppr.get("algn") if ppr is not None else None
                            p_defrpr = ppr.find("a:defRPr", NS) if ppr is not None else None

                            runs_text = [t.text or "" for t in p_el.findall(".//a:t", NS)]
                            full_p_text = "".join(runs_text)
                            char_len = len(full_p_text)
                            total_chars += char_len
                            if full_p_text.strip():
                                non_empty_paras += 1

                            # Inspect first run with formatting or fallback to cascade
                            r_el = next(
                                (n for n in (p_el.find("a:r/a:rPr", NS), p_defrpr, p_el.find("a:endParaRPr", NS)) if n is not None),
                                None,
                            )
                            direct_sz = None
                            direct_font = None
                            direct_color = None
                            direct_bold = None
                            if r_el is not None:
                                if r_el.get("sz", "").isdigit():
                                    direct_sz = round(int(r_el.get("sz")) / 100.0, 2)
                                if "b" in r_el.attrib:
                                    direct_bold = r_el.get("b") in ("1", "true")
                                for ftag in ("latin", "ea", "cs"):
                                    fn = r_el.find(f"a:{ftag}", NS)
                                    if fn is not None and fn.get("typeface") and not direct_font:
                                        direct_font = resolve_font_ref(fn.get("typeface"), theme_fonts)
                                sfill = r_el.find("a:solidFill", NS)
                                if sfill is not None and len(sfill):
                                    cres = resolve_color_node(sfill[0], theme_colors, clr_map)
                                    if cres.get("resolved_hex"):
                                        direct_color = cres["resolved_hex"]

                            # Cascade resolution for size, font, color, alignment
                            cascade_candidates = (
                                [shape_lst.get(lvl_idx, {})]
                                + [d.get(lvl_idx, {}) for d in inh_lst_chain]
                                + [tx_styles.get(ph_role, {}).get(lvl_idx, {}), tx_styles.get("other", {}).get(lvl_idx, {}), pres_default_styles.get(lvl_idx, {})]
                            )

                            eff_sz = direct_sz
                            sz_source = "direct_run" if direct_sz is not None else None
                            if eff_sz is None:
                                for cand in cascade_candidates:
                                    if cand.get("font_size_pt") is not None:
                                        eff_sz = cand["font_size_pt"]
                                        sz_source = cand.get("source")
                                        break
                            if eff_sz is None:
                                eff_sz = 28.0 if ph_role == "title" else 16.0
                                sz_source = "ooxml_default"

                            eff_font = direct_font
                            font_source = "direct_run" if direct_font is not None else None
                            if eff_font is None:
                                for cand in cascade_candidates:
                                    f_cand = cand.get("font_ea") or cand.get("font_latin")
                                    if f_cand:
                                        eff_font = f_cand
                                        font_source = cand.get("source")
                                        break
                            if eff_font is None:
                                f_slot = theme_fonts.get("major" if ph_role == "title" else "minor", {})
                                eff_font = f_slot.get("ea") or f_slot.get("latin") or "Calibri"
                                font_source = "theme_font_scheme"

                            eff_color = direct_color
                            color_source = "direct_run" if direct_color is not None else None
                            if eff_color is None:
                                for cand in cascade_candidates:
                                    if cand.get("color_hex"):
                                        eff_color = cand["color_hex"]
                                        color_source = cand.get("source")
                                        break
                            if eff_color is None:
                                eff_color = theme_colors.get(clr_map.get("tx1", "dk1"), "#000000")
                                color_source = "theme_clrMap_tx1"

                            if eff_bold := direct_bold:
                                pass
                            else:
                                eff_bold = any(bool(c.get("bold")) for c in cascade_candidates if c.get("bold") is not None)

                            if char_len > 0:
                                observed_font_chars[eff_font] = observed_font_chars.get(eff_font, 0) + char_len
                                observed_size_chars[eff_sz] = observed_size_chars.get(eff_sz, 0) + char_len
                                observed_colors_count[eff_color] = observed_colors_count.get(eff_color, 0) + 1

                            para_entry: dict[str, Any] = {
                                "level": lvl_idx,
                                "char_count": char_len,
                                "alignment": algn or next((c.get("alignment") for c in cascade_candidates if c.get("alignment")), "l"),
                                "effective_style": {
                                    "font_family": eff_font,
                                    "font_source": font_source,
                                    "font_size_pt": eff_sz,
                                    "size_source": sz_source,
                                    "color_hex": eff_color,
                                    "color_source": color_source,
                                    "bold": bool(eff_bold),
                                },
                            }
                            if not scrub_text:
                                para_entry["text"] = full_p_text
                            else:
                                # Keep structural signals (e.g. numeric/formula/keyword flags) without raw scientific prose
                                para_entry["structural_signals"] = _extract_text_signals(full_p_text)
                            paras_out.append(para_entry)

                        body_pr = raw_el.find("p:txBody/a:bodyPr", NS)
                        obj_record["text_body"] = {
                            "total_chars": total_chars,
                            "paragraph_count": len(paras_out),
                            "non_empty_paragraphs": non_empty_paras,
                            "anchor": body_pr.get("anchor", "t") if body_pr is not None else "t",
                            "wrap": body_pr.get("wrap", "square") if body_pr is not None else "square",
                            "paragraphs": paras_out,
                        }

                    items.append(obj_record)
                return items

            shapes_tree = walk_shapes(slide.shapes, [])
            slides_data.append({
                "slide_number": slide_idx,
                "slide_part": slide_part,
                "layout_part": layout_part,
                "layout_name": layout.name,
                "master_part": master_part,
                "theme_part": theme_part,
                "clr_map": clr_map,
                "background": bg_info,
                "objects": shapes_tree,
                "synthesized_from_layout": is_layout_carrier,
            })

    after_hash = sha256_file(source_path)
    if before_hash != after_hash:
        raise ValueError("Source PPTX was modified during read-only structure extraction")

    # Broken Theme Color Detection & Repair (adapted from anotb/pptx-masters)
    primary_theme = next(iter(themes_cache.values()), {"colors": {}, "fonts": {"major": {}, "minor": {}}})
    raw_theme_colors = dict(primary_theme.get("colors", {}))
    accents = [raw_theme_colors.get(f"accent{i}") for i in range(1, 7) if raw_theme_colors.get(f"accent{i}")]
    broken_theme = False
    if not accents or len(set(accents)) <= 1 and accents[0] in ("#FFFFFF", "#000000"):
        broken_theme = True
        # Recover effective accent from observed non-neutral colors
        non_neutral = [
            c for c, _ in sorted(observed_colors_count.items(), key=lambda kv: -kv[1])
            if c not in ("#FFFFFF", "#000000", "#F5F5F5")
        ]
        if non_neutral:
            raw_theme_colors["accent1"] = non_neutral[0]
            if len(non_neutral) > 1:
                raw_theme_colors["accent2"] = non_neutral[1]
        else:
            raw_theme_colors["accent1"] = "#1F4E79"

    gcd = math.gcd(canvas_w, canvas_h)
    ratio_w, ratio_h = canvas_w // gcd, canvas_h // gcd
    if abs((canvas_w / canvas_h) - (16 / 9)) < 0.02:
        aspect_str = "16:9"
    elif abs((canvas_w / canvas_h) - (4 / 3)) < 0.02:
        aspect_str = "4:3"
    else:
        aspect_str = f"{ratio_w}:{ratio_h}"

    return {
        "source_path": str(source_path),
        "file_name": source_path.name,
        "sha256": before_hash,
        "slide_count": len(slides_data),
        "canvas": {
            "width_emu": canvas_w,
            "height_emu": canvas_h,
            "width_pt": round(canvas_w / EMU_PER_PT, 2),
            "height_pt": round(canvas_h / EMU_PER_PT, 2),
            "aspect_ratio": aspect_str,
        },
        "themes": themes_cache,
        "effective_theme_colors": raw_theme_colors,
        "broken_theme_repaired": broken_theme,
        "masters": masters_cache,
        "layouts": layouts_cache,
        "media_assets": list(media_assets.values()),
        "observed_frequencies": {
            "fonts_by_chars": [{"font": k, "chars": v} for k, v in sorted(observed_font_chars.items(), key=lambda x: -x[1])],
            "sizes_pt_by_chars": [{"size_pt": k, "chars": v} for k, v in sorted(observed_size_chars.items(), key=lambda x: -x[1])],
            "colors_by_usage": [{"color_hex": k, "count": v} for k, v in sorted(observed_colors_count.items(), key=lambda x: -x[1])],
        },
        "slides": slides_data,
        "unsupported_features": unsupported_features,
    }


def _extract_text_signals(text: str) -> dict[str, Any]:
    """Extract non-sensitive structural cues from text without retaining scientific content."""
    low = (text or "").strip().lower()
    has_cjk = any("一" <= ch <= "鿿" for ch in text)
    return {
        "is_toc_like": any(k in low for k in ("agenda", "outline", "contents", "目录", "提纲", "议程")),
        "is_section_like": any(k in low for k in ("section", "part ", "chapter", "章节", "第一部分", "第二部分", "第三部分")),
        "is_method_like": any(k in low for k in ("method", "workflow", "pipeline", "protocol", "experimental", "方法", "路线", "流程", "实验方案")),
        "is_result_like": any(k in low for k in ("result", "spectrum", "spectra", "curve", "response", "yield", "结果", "谱图", "响应", "趋势", "性能")),
        "is_comparison_like": any(k in low for k in ("vs", "versus", "comparison", "compare", "benchmark", "对比", "对照", "比较")),
        "is_conclusion_like": any(k in low for k in ("conclusion", "summary", "takeaway", "future", "结论", "总结", "展望", "小结")),
        "is_reference_like": any(k in low for k in ("reference", "bibliography", "doi:", "et al", "参考文献", "文献")),
        "is_ending_like": any(k in low for k in ("thank", "q&a", "acknowledg", "致谢", "感谢", "谢谢", "提问")),
        "is_problem_like": any(k in low for k in ("problem", "challenge", "bottleneck", "gap", "痛点", "挑战", "瓶颈", "科学问题")),
        "is_background_like": any(k in low for k in ("background", "introduction", "motivation", "context", "背景", "引言", "研究意义")),
        "is_caption_like": any(k in low for k in ("fig.", "figure", "table ", "scheme", "图1", "图2", "表1", "注：", "source:")),
        "has_cjk": has_cjk,
    }
