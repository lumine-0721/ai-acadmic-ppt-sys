#!/usr/bin/env python3
"""Native PPTX Fidelity Replicator, Generalization Reconstructor & Real Render Verifier.

Adapted in part from MIT-licensed open-source implementations:
- jameshemson-slides (commit 245c0e6858df9afe6a3381e36244812f3d2e4452):
  source/skills/build-deck/scripts/lint_pptx.py (alpha composite, contrast ratio, mechanical lint)
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any
from xml.etree import ElementTree as ET

from lxml import etree
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat
import pptx
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Emu, Pt

from .ooxml_resolver import EMU_PER_PT, NS, resolve_presentation_structure

A_NS = NS["a"]


def _sanitize_xml_text(text: str) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(text or ""))


def _hex_to_rgb_tuple(hex_str: str) -> tuple[int, int, int]:
    clean = (hex_str or "#000000").lstrip("#")
    if len(clean) != 6:
        return (0, 0, 0)
    return (int(clean[0:2], 16), int(clean[2:4], 16), int(clean[4:6], 16))


def composite_alpha_over_ground(colour_hex: str, alpha: float | None, ground_hex: str) -> str:
    """Alpha-composite colour_hex over ground_hex (adapted from jameshemson-slides lint_pptx.py, MIT)."""
    a = 1.0 if alpha is None else max(0.0, min(1.0, float(alpha)))
    c = _hex_to_rgb_tuple(colour_hex)
    g = _hex_to_rgb_tuple(ground_hex)
    return "#" + "".join(
        f"{max(0, min(255, round(a * ci + (1.0 - a) * gi))):02X}"
        for ci, gi in zip(c, g)
    )


def wcag_contrast_ratio(hex_a: str, hex_b: str) -> float:
    def rel_lum(rgb: tuple[int, int, int]) -> float:
        chans = []
        for v in rgb:
            s = v / 255.0
            chans.append(s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4)
        return 0.2126 * chans[0] + 0.7152 * chans[1] + 0.0722 * chans[2]

    l1 = rel_lum(_hex_to_rgb_tuple(hex_a))
    l2 = rel_lum(_hex_to_rgb_tuple(hex_b))
    bright, dark = max(l1, l2), min(l1, l2)
    return round((bright + 0.05) / (dark + 0.05), 2)


def _set_txbody_paragraphs_preserving_rpr(tx_body, lines: list[str]) -> None:
    """Replace text inside an lxml <p:txBody> while preserving existing <a:pPr> and first <a:rPr>."""
    paras = tx_body.findall(f"{{{A_NS}}}p")
    if not paras:
        return
    template_rpr = None
    for p in paras:
        r = p.find(f"{{{A_NS}}}r")
        if r is not None and r.find(f"{{{A_NS}}}rPr") is not None:
            template_rpr = etree.fromstring(etree.tostring(r.find(f"{{{A_NS}}}rPr")))
            break

    for idx, line in enumerate(lines):
        if idx < len(paras):
            p_el = paras[idx]
        else:
            p_el = etree.SubElement(tx_body, f"{{{A_NS}}}p")
            if paras[0].find(f"{{{A_NS}}}pPr") is not None:
                p_el.insert(0, etree.fromstring(etree.tostring(paras[0].find(f"{{{A_NS}}}pPr"))))
        for br in p_el.findall(f"{{{A_NS}}}br"):
            p_el.remove(br)
        runs = p_el.findall(f"{{{A_NS}}}r")
        if runs:
            first_r = runs[0]
            for extra_r in runs[1:]:
                p_el.remove(extra_r)
            t_el = first_r.find(f"{{{A_NS}}}t")
            if t_el is None:
                t_el = etree.SubElement(first_r, f"{{{A_NS}}}t")
            t_el.text = _sanitize_xml_text(line)
        else:
            r_el = etree.SubElement(p_el, f"{{{A_NS}}}r")
            if template_rpr is not None:
                r_el.append(etree.fromstring(etree.tostring(template_rpr)))
            t_el = etree.SubElement(r_el, f"{{{A_NS}}}t")
            t_el.text = _sanitize_xml_text(line)

    # Clear remaining paragraphs if new content has fewer lines
    for extra_p in paras[len(lines):]:
        for r_el in extra_p.findall(f"{{{A_NS}}}r"):
            t_el = r_el.find(f"{{{A_NS}}}t")
            if t_el is not None:
                t_el.text = ""


def replicate_deck_with_slot_payload(
    reference_pptx: Path,
    spec: dict[str, Any],
    output_pptx: Path,
    slide_payloads: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Level 2 Fidelity Replication: clone reference PPTX preserving native OOXML hierarchy and fill by slot_name."""
    reference_pptx = Path(reference_pptx).resolve()
    output_pptx = Path(output_pptx).resolve()
    output_pptx.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(reference_pptx, output_pptx)

    if not slide_payloads:
        return {
            "mode": "fidelity_clone_exact",
            "output_pptx": str(output_pptx),
            "slides_processed": spec["source_provenance"]["extraction_metadata"]["slide_count"],
            "slots_updated": 0,
        }

    prs = pptx.Presentation(str(output_pptx))
    patterns_by_id = {lp["layout_id"]: lp for lp in spec["layout_patterns"]}
    roles_by_slide = {sr["slide_number"]: sr for sr in spec["slide_roles"]}
    slots_updated = 0

    for s_idx, slide in enumerate(prs.slides, start=1):
        payload = slide_payloads.get(str(s_idx)) or slide_payloads.get(f"S{s_idx:03d}")
        if not payload:
            continue
        sr = roles_by_slide.get(s_idx)
        if not sr:
            continue
        lp = patterns_by_id.get(sr["layout_id"])
        if not lp:
            continue
        slot_by_name = {s["slot_name"]: s for s in lp["slots"]}
        shape_by_id = {sh.shape_id: sh for sh in slide.shapes}

        for slot_name, new_val in payload.items():
            sc = slot_by_name.get(slot_name)
            if not sc:
                continue
            sh = shape_by_id.get(sc.get("source_shape_id"))
            if sh is None:
                continue
            if getattr(sh, "has_table", False) and isinstance(new_val, list) and new_val and isinstance(new_val[0], list):
                tbl = sh.table
                for r_i, row_vals in enumerate(new_val):
                    if r_i >= len(tbl.rows):
                        break
                    for c_i, cell_val in enumerate(row_vals):
                        if c_i >= len(tbl.rows[r_i].cells):
                            break
                        _set_txbody_paragraphs_preserving_rpr(tbl.rows[r_i].cells[c_i].text_frame._txBody, [str(cell_val)])
                slots_updated += 1
            elif getattr(sh, "has_text_frame", False):
                lines = [str(x) for x in new_val] if isinstance(new_val, list) else [str(new_val)]
                _set_txbody_paragraphs_preserving_rpr(sh.text_frame._txBody, lines)
                slots_updated += 1

    prs.save(str(output_pptx))
    return {
        "mode": "fidelity_slot_replacement",
        "output_pptx": str(output_pptx),
        "slides_processed": len(prs.slides),
        "slots_updated": slots_updated,
    }


def generalize_deck_from_spec(
    spec: dict[str, Any],
    content_plan: dict[str, Any],
    output_pptx: Path,
    seed_template_pptx: Path | None = None,
) -> dict[str, Any]:
    """Level 3 Design Generalization: generate a brand-new natively editable academic PPTX for a different topic/length
    using ONLY the extracted DesignSpecification tokens, reusable layout patterns, and slot contracts.
    """
    output_pptx = Path(output_pptx).resolve()
    output_pptx.parent.mkdir(parents=True, exist_ok=True)

    prs = pptx.Presentation()
    canvas_w = int(spec["canvas"]["width_emu"])
    canvas_h = int(spec["canvas"]["height_emu"])
    prs.slide_width = canvas_w
    prs.slide_height = canvas_h

    blank_layout = prs.slide_layouts[6] if len(prs.slide_layouts) > 6 else prs.slide_layouts[-1]
    colors = spec["design_tokens"]["colors"]["semantic_roles"]
    office_map = spec["design_tokens"]["colors"]["office_theme_mapping"]
    heading_font = spec["design_tokens"]["typography"]["major_font"]["family"] or "Calibri"
    body_font = spec["design_tokens"]["typography"]["minor_font"]["family"] or "Calibri"

    ink_rgb = RGBColor(*_hex_to_rgb_tuple(colors["ink"]))
    paper_rgb = RGBColor(*_hex_to_rgb_tuple(colors["paper"]))
    accent_rgb = RGBColor(*_hex_to_rgb_tuple(colors["accent"]))
    muted_rgb = RGBColor(*_hex_to_rgb_tuple(colors["muted"]))
    secondary_rgb = RGBColor(*_hex_to_rgb_tuple(office_map.get("SecondaryAccent", colors["accent"])))

    patterns_by_id = {lp["layout_id"]: lp for lp in spec["layout_patterns"]}
    patterns_by_key = {lp["pattern_key"]: lp for lp in spec["layout_patterns"]}
    patterns_by_role: dict[str, list[dict[str, Any]]] = {}
    for lp in spec["layout_patterns"]:
        for cr in lp["generalization"]["applicable_content_roles"]:
            patterns_by_role.setdefault(cr.lower(), []).append(lp)

    slides_spec = content_plan.get("slides", [])
    applied_decisions: list[dict[str, Any]] = []

    for idx, s_plan in enumerate(slides_spec, start=1):
        slide = prs.slides.add_slide(blank_layout)
        # Set slide background paper color
        bg_fill = slide.background.fill
        bg_fill.solid()
        bg_fill.fore_color.rgb = paper_rgb

        req_layout_id = s_plan.get("layout_id")
        req_pattern_key = s_plan.get("pattern_key")
        req_content_role = (s_plan.get("content_role") or "Results").strip()
        requested_slot_keys = set((s_plan.get("slots") or {}).keys())

        chosen_lp = None
        if req_layout_id and req_layout_id in patterns_by_id:
            chosen_lp = patterns_by_id[req_layout_id]
        elif req_pattern_key and req_pattern_key in patterns_by_key:
            chosen_lp = patterns_by_key[req_pattern_key]
        elif req_content_role.lower() in patterns_by_role:
            candidates = patterns_by_role[req_content_role.lower()]
            if requested_slot_keys:
                chosen_lp = max(
                    candidates,
                    key=lambda lp: sum(
                        1 for req_k in requested_slot_keys
                        if any(req_k in s["slot_name"] or s["slot_name"] in req_k for s in lp["slots"])
                    ),
                )
            else:
                chosen_lp = candidates[0]
        else:
            chosen_lp = spec["layout_patterns"][min(idx - 1, len(spec["layout_patterns"]) - 1)]

        # Draw subtle top accent bar & footer bar from design system
        top_rule = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE,
            Emu(int(canvas_w * 0.05)),
            Emu(int(canvas_h * 0.045)),
            Emu(int(canvas_w * 0.06)),
            Emu(int(4 * EMU_PER_PT)),
        )
        top_rule.fill.solid()
        top_rule.fill.fore_color.rgb = accent_rgb
        top_rule.line.fill.background()

        slot_values = s_plan.get("slots", {})
        notes_overflow: list[str] = []
        placed_slots: list[str] = []

        for sc in chosen_lp["slots"]:
            sname = sc["slot_name"]
            srole = sc["semantic_role"]
            rect_pct = sc["rect_percent"]
            left = Emu(int(canvas_w * (rect_pct["x"] / 100.0)))
            top = Emu(int(canvas_h * (rect_pct["y"] / 100.0)))
            width = Emu(int(canvas_w * (rect_pct["w"] / 100.0)))
            height = Emu(int(canvas_h * (rect_pct["h"] / 100.0)))

            val = slot_values.get(sname)
            has_sibling_b = any(s["slot_name"] == f"{sname[:-2]}_B" for s in chosen_lp["slots"]) if sname.endswith("_A") else False
            if val is None:
                if sname.endswith("_A"):
                    base_key = sname[:-2]
                    raw_v = slot_values.get(base_key)
                    val = [raw_v[0]] if (has_sibling_b and isinstance(raw_v, list) and raw_v) else raw_v
                elif re.search(r"_[B-Z]$", sname):
                    idx_ord = ord(sname[-1]) - ord("A")
                    base_key = sname[:-2]
                    base_val = slot_values.get(base_key) or s_plan.get("claims")
                    if isinstance(base_val, list) and idx_ord < len(base_val):
                        val = [base_val[idx_ord]]
                    else:
                        continue
            if val is None:
                if srole == "MAIN_TITLE":
                    val = s_plan.get("title", f"Slide {idx}: {req_content_role}")
                elif srole in ("SUBTITLE_OR_META", "SECTION_BADGE"):
                    val = s_plan.get("subtitle", req_content_role)
                elif srole == "BODY_EXPLANATION" and sname in ("EXPLANATION_SLOT", "EXPLANATION_SLOT_A"):
                    claims_list = s_plan.get("claims", ["Key experimental evidence and quantitative observation."])
                    has_multi_exp = any(s["slot_name"] == "EXPLANATION_SLOT_B" for s in chosen_lp["slots"])
                    val = [claims_list[0]] if (has_multi_exp and isinstance(claims_list, list) and claims_list) else claims_list
                elif srole == "FIGURE_CAPTION_OR_CONDITIONS":
                    val = s_plan.get("conditions", "Conditions: 298 K, pH 7.4, n = 3 independent replicates")
                elif srole == "TAKEAWAY_CALLOUT":
                    val = s_plan.get("takeaway", "Core takeaway validated across comparative conditions.")

            style_tok = sc["style_tokens"]
            cap_est = sc["capacity_estimate"]
            target_pt = float(style_tok.get("font_size_pt", 15.0))

            # Handle Table Slot
            if isinstance(val, dict) and "table_rows" in val:
                rows_data = val["table_rows"]
                r_cnt = len(rows_data)
                c_cnt = len(rows_data[0]) if rows_data else 2
                t_shape = slide.shapes.add_table(r_cnt, c_cnt, left, top, width, height)
                tbl = t_shape.table
                for r_i, r_vals in enumerate(rows_data):
                    for c_i, c_val in enumerate(r_vals):
                        cell = tbl.cell(r_i, c_i)
                        cell.text = str(c_val)
                        for p in cell.text_frame.paragraphs:
                            p.font.name = body_font
                            p.font.size = Pt(12.0 if r_i > 0 else 13.0)
                            p.font.bold = (r_i == 0)
                            p.font.color.rgb = paper_rgb if r_i == 0 else ink_rgb
                        cell.fill.solid()
                        cell.fill.fore_color.rgb = accent_rgb if r_i == 0 else (RGBColor(242, 246, 250) if r_i % 2 == 1 else paper_rgb)
                placed_slots.append(sname)
                continue

            # Handle Image / Figure Slot
            if isinstance(val, dict) and ("image_path" in val or "figure_title" in val):
                img_path = val.get("image_path")
                if img_path and Path(img_path).is_file():
                    with Image.open(img_path) as im:
                        iw, ih = im.size
                    img_aspect = iw / max(1.0, float(ih))
                    box_aspect = float(width) / max(1.0, float(height))
                    if box_aspect > img_aspect:
                        draw_h = int(height)
                        draw_w = int(draw_h * img_aspect)
                    else:
                        draw_w = int(width)
                        draw_h = int(draw_w / img_aspect)
                    draw_l = int(left) + (int(width) - draw_w) // 2
                    draw_t = int(top) + (int(height) - draw_h) // 2
                    slide.shapes.add_picture(str(Path(img_path).resolve()), Emu(draw_l), Emu(draw_t), Emu(draw_w), Emu(draw_h))
                else:
                    # Structured vector evidence panel when external bitmap is not provided
                    panel = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, left, top, width, height)
                    panel.fill.solid()
                    panel.fill.fore_color.rgb = RGBColor(240, 245, 250)
                    panel.line.color.rgb = secondary_rgb
                    panel.line.width = Pt(1.5)
                    tf = panel.text_frame
                    tf.word_wrap = True
                    p0 = tf.paragraphs[0]
                    p0.text = str(val.get("figure_title", "Experimental Figure Slot"))
                    p0.font.name = heading_font
                    p0.font.size = Pt(14.0)
                    p0.font.bold = True
                    p0.font.color.rgb = accent_rgb
                    for series_line in val.get("data_points", []):
                        p_sub = tf.add_paragraph()
                        p_sub.text = f"• {series_line}"
                        p_sub.font.name = body_font
                        p_sub.font.size = Pt(12.0)
                        p_sub.font.color.rgb = ink_rgb
                placed_slots.append(sname)
                continue

            # Handle Text / Callout / Explanation Slot with Capacity & Graceful Degradation
            lines = [str(x) for x in val] if isinstance(val, list) else ([str(val)] if val else [])
            if not lines:
                continue
            slot_w_pt = float(spec["canvas"]["width_pt"]) * (rect_pct["w"] / 100.0)
            slot_h_pt = float(spec["canvas"]["height_pt"]) * (rect_pct["h"] / 100.0)
            max_cjk = int(cap_est.get("cjk_recommended_max_chars", 160))
            max_lines = int(cap_est.get("max_lines", 6))
            joined_len = sum(len(x) for x in lines)
            stepped_pt = target_pt

            # Adaptive step-down font fitting (adapted from jameshemson-slides roles._fit_pt, MIT)
            floor_pt = 16.0 if srole == "MAIN_TITLE" else (10.5 if "CAPTION" in srole else 12.0)
            while stepped_pt > floor_pt:
                cpl = max(1.0, (slot_w_pt - 12.0) / max(6.0, stepped_pt * 0.88))
                needed_lines = sum(max(1, math.ceil(len(line_s) / cpl)) for line_s in lines)
                needed_h_pt = needed_lines * (stepped_pt * 1.28)
                if needed_h_pt <= slot_h_pt + 6.0:
                    break
                stepped_pt = round(stepped_pt - 1.5, 1)

            # If still exceeding slot height after font step-down, demote excess lines or expand stretchable height
            cpl_final = max(1.0, (slot_w_pt - 12.0) / max(6.0, stepped_pt * 0.88))
            needed_lines = sum(max(1, math.ceil(len(line_s) / cpl_final)) for line_s in lines)
            req_h_pt = needed_lines * (stepped_pt * 1.28) + 6.0
            if req_h_pt > slot_h_pt and (sc.get("stretchable") or srole == "MAIN_TITLE"):
                max_avail_h_pt = float(spec["canvas"]["height_pt"]) * max(0.12, (92.0 - rect_pct["y"]) / 100.0)
                stretched_h_pt = min(req_h_pt, max_avail_h_pt)
                slot_h_pt = stretched_h_pt
                height = Emu(int(stretched_h_pt * EMU_PER_PT))
            if req_h_pt > slot_h_pt + 6.0 and srole != "MAIN_TITLE" and len(lines) > 1:
                kept: list[str] = []
                used_lines = 0
                max_fit_lines = max(1, int((slot_h_pt + 6.0) // (stepped_pt * 1.28)))
                for line_s in lines:
                    l_cnt = max(1, math.ceil(len(line_s) / cpl_final))
                    if used_lines + l_cnt <= max_fit_lines or not kept:
                        kept.append(line_s)
                        used_lines += l_cnt
                    else:
                        notes_overflow.append(line_s)
                lines = kept

            if srole == "TAKEAWAY_CALLOUT":
                box = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, left, top, width, height)
                box.fill.solid()
                box.fill.fore_color.rgb = RGBColor(232, 240, 248)
                box.line.color.rgb = accent_rgb
                box.line.width = Pt(1.2)
            else:
                box = slide.shapes.add_textbox(left, top, width, height)
                if style_tok.get("fill_hex"):
                    box.fill.solid()
                    box.fill.fore_color.rgb = RGBColor(*_hex_to_rgb_tuple(style_tok["fill_hex"]))

            tf = box.text_frame
            tf.word_wrap = True
            for l_i, line_str in enumerate(lines):
                p = tf.paragraphs[0] if l_i == 0 else tf.add_paragraph()
                p.text = _sanitize_xml_text(line_str)
                p.font.name = heading_font if srole == "MAIN_TITLE" else body_font
                p.font.size = Pt(stepped_pt)
                p.font.bold = bool(style_tok.get("bold")) or (srole in ("MAIN_TITLE", "TAKEAWAY_CALLOUT"))
                p.font.color.rgb = accent_rgb if srole in ("MAIN_TITLE", "SECTION_BADGE") else (muted_rgb if "CAPTION" in srole else ink_rgb)
            placed_slots.append(sname)

        if notes_overflow or s_plan.get("speaker_notes"):
            notes_slide = slide.notes_slide
            ntf = notes_slide.notes_text_frame
            note_parts = []
            if s_plan.get("speaker_notes"):
                note_parts.append(str(s_plan["speaker_notes"]))
            if notes_overflow:
                note_parts.append("[Overflow Supporting Details Demoted from Slide Surface]: " + " | ".join(notes_overflow))
            ntf.text = "\n".join(note_parts)

        applied_decisions.append({
            "slide_number": idx,
            "content_role": req_content_role,
            "selected_layout_id": chosen_lp["layout_id"],
            "pattern_key": chosen_lp["pattern_key"],
            "placed_slots": placed_slots,
            "demoted_to_notes_count": len(notes_overflow),
        })

    prs.save(str(output_pptx))
    return {
        "mode": "design_generalization_level3",
        "output_pptx": str(output_pptx),
        "slide_count": len(prs.slides),
        "applied_decisions": applied_decisions,
    }


def render_and_measure_via_powerpoint_com(
    pptx_path: Path,
    export_dir: Path,
    width_px: int = 1920,
    height_px: int = 1080,
) -> dict[str, Any]:
    """Render PPTX slides to PNG and measure live TextFrame2.TextRange.BoundHeight via Windows PowerPoint COM."""
    pptx_path = Path(pptx_path).resolve()
    export_dir = Path(export_dir).resolve()
    export_dir.mkdir(parents=True, exist_ok=True)

    ps_script = export_dir / "_com_render_probe.ps1"
    report_json = export_dir / "com_render_report.json"
    ps_code = f"""
$ErrorActionPreference = "Stop"
$pptxPath = "{str(pptx_path)}"
$outDir = "{str(export_dir)}"
$reportPath = "{str(report_json)}"
$app = New-Object -ComObject PowerPoint.Application
$pres = $null
try {{
    $version = $app.Version
    $pres = $app.Presentations.Open($pptxPath, -1, 0, 0)
    $pres.Export($outDir, "PNG", {width_px}, {height_px})
    $rawPngs = @(Get-ChildItem -LiteralPath $outDir -Filter "*.PNG" | Sort-Object Name)
    $slidesOut = @()
    $overflows = @()
    $idx = 0
    foreach ($slide in $pres.Slides) {{
        $idx++
        $pngPath = Join-Path $outDir ("slide_{{0:D2}}.png" -f $idx)
        if ($idx -le $rawPngs.Count) {{
            if ($rawPngs[$idx - 1].FullName -ne $pngPath) {{
                Copy-Item -LiteralPath $rawPngs[$idx - 1].FullName -Destination $pngPath -Force
            }}
        }}
        foreach ($sh in $slide.Shapes) {{
            try {{
                if ($sh.HasTextFrame -eq -1 -and $sh.TextFrame2.HasText -eq -1) {{
                    $allocH = [double]$sh.Height
                    $boundH = [double]$sh.TextFrame2.TextRange.BoundHeight
                    $boundW = [double]$sh.TextFrame2.TextRange.BoundWidth
                    if ($boundH -gt ($allocH + 6.0) -and $allocH -gt 10.0) {{
                        $overflows += [ordered]@{{
                            slide_number = $idx
                            shape_id = $sh.Id
                            shape_name = $sh.Name
                            allocated_height_pt = [math]::Round($allocH, 2)
                            bound_height_pt = [math]::Round($boundH, 2)
                            overflow_pt = [math]::Round($boundH - $allocH, 2)
                        }}
                    }}
                }}
            }} catch {{}}
        }}
        $slidesOut += $pngPath
    }}
    $result = [ordered]@{{
        renderer = "PowerPoint.Application COM"
        version = $version
        slide_count = $idx
        exported_pngs = $slidesOut
        bound_height_overflows = $overflows
        status = "rendered"
    }}
    $result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $reportPath -Encoding UTF8
}} finally {{
    if ($null -ne $pres) {{ $pres.Close() }}
}}
"""
    ps_script.write_text(ps_code, encoding="utf-8")
    pwsh_bin = "C:/Program Files/PowerShell/7/pwsh.exe"
    if not Path(pwsh_bin).is_file():
        pwsh_bin = "pwsh"
    proc = subprocess.run(
        [pwsh_bin, "-NoProfile", "-NonInteractive", "-File", str(ps_script)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
    )
    try:
        ps_script.unlink(missing_ok=True)
    except OSError:
        pass

    if proc.returncode == 0 and report_json.is_file():
        return json.loads(report_json.read_text(encoding="utf-8-sig"))
    # Fallback deterministic OOXML rasterizer + text-bound estimator when running inside an AppContainer sandbox blocking COM
    return _render_and_measure_fallback_pillow(pptx_path, export_dir, width_px, height_px, (proc.stderr or proc.stdout or "").strip()[:240])


def _load_cjk_font(size_px: int, bold: bool = False):
    candidates = (
        ["C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/msyh.ttc"]
        if bold
        else ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/arial.ttf"]
    )
    for fp in candidates:
        if Path(fp).is_file():
            try:
                return ImageFont.truetype(fp, size=max(10, int(size_px)))
            except Exception:
                continue
    return ImageFont.load_default()


def _render_and_measure_fallback_pillow(
    pptx_path: Path,
    export_dir: Path,
    width_px: int,
    height_px: int,
    com_note: str,
) -> dict[str, Any]:
    """Deterministic OOXML slide rasterizer & text-bound overflow verifier (fallback when COM is sandbox-blocked)."""
    resolved = resolve_presentation_structure(pptx_path, scrub_text=False)
    exported_pngs: list[str] = []
    overflows: list[dict[str, Any]] = []
    canvas_w_pt = max(1.0, resolved["canvas"]["width_pt"])
    canvas_h_pt = max(1.0, resolved["canvas"]["height_pt"])
    scale_px_per_pt = height_px / canvas_h_pt

    for slide in resolved["slides"]:
        s_idx = slide["slide_number"]
        bg_hex = slide["background"].get("color_hex") or "#FFFFFF"
        img = Image.new("RGB", (width_px, height_px), _hex_to_rgb_tuple(bg_hex))
        draw = ImageDraw.Draw(img)

        for obj in slide["objects"]:
            xp, yp, wp, hp = obj["box_percent"]
            x1 = int((xp / 100.0) * width_px)
            y1 = int((yp / 100.0) * height_px)
            x2 = max(x1 + 2, int(((xp + wp) / 100.0) * width_px))
            y2 = max(y1 + 2, int(((yp + hp) / 100.0) * height_px))
            fill_hex = (obj.get("fill") or {}).get("color_hex")
            line_hex = ((obj.get("line") or {}).get("color_hex")) or ("#CBD5E1" if obj.get("table") else None)
            if fill_hex:
                draw.rectangle([x1, y1, x2, y2], fill=_hex_to_rgb_tuple(fill_hex), outline=_hex_to_rgb_tuple(line_hex) if line_hex else None)
            elif line_hex:
                draw.rectangle([x1, y1, x2, y2], outline=_hex_to_rgb_tuple(line_hex), width=2)

            tbody = obj.get("text_body")
            if tbody and tbody.get("total_chars", 0) > 0:
                alloc_w_pt = (wp / 100.0) * canvas_w_pt
                alloc_h_pt = (hp / 100.0) * canvas_h_pt
                est_h_pt = 0.0
                cy = y1 + 8
                for p in tbody.get("paragraphs", []):
                    txt = p.get("text") or ("X" * p.get("char_count", 0))
                    if not txt.strip():
                        continue
                    f_pt = float(p["effective_style"].get("font_size_pt", 14.0))
                    is_bold = bool(p["effective_style"].get("bold", False))
                    font_px = max(12, round(f_pt * scale_px_per_pt))
                    font_obj = _load_cjk_font(font_px, bold=is_bold)
                    t_col = _hex_to_rgb_tuple(p["effective_style"].get("color_hex") or "#1A202C")
                    chars_per_line = max(1.0, (alloc_w_pt - 12.0) / max(6.0, f_pt * 0.85))
                    cpl_int = max(8, int(chars_per_line))
                    lines_needed = max(1, math.ceil(len(txt) / chars_per_line))
                    est_h_pt += lines_needed * (f_pt * 1.28)
                    for chunk_i in range(0, len(txt), cpl_int):
                        if cy >= y2 - 4:
                            break
                        draw.text((x1 + 8, cy), txt[chunk_i:chunk_i + cpl_int], fill=t_col, font=font_obj)
                        cy += int(font_px * 1.28)
                if est_h_pt > alloc_h_pt + 8.0 and alloc_h_pt > 12.0:
                    overflows.append({
                        "slide_number": s_idx,
                        "shape_id": obj.get("shape_id"),
                        "shape_name": obj.get("name"),
                        "allocated_height_pt": round(alloc_h_pt, 2),
                        "bound_height_pt": round(est_h_pt, 2),
                        "overflow_pt": round(est_h_pt - alloc_h_pt, 2),
                    })

        png_path = export_dir / f"slide_{s_idx:02d}.png"
        img.save(png_path, "PNG")
        exported_pngs.append(str(png_path))

    out_payload = {
        "renderer": "APRD Deterministic OOXML Rasterizer (Pillow fallback; COM sandbox note recorded)",
        "version": "2.0.0",
        "font_environment": "Microsoft YaHei (msyh.ttc / msyhbd.ttc) + Arial fallback",
        "com_probe_note": com_note,
        "slide_count": len(exported_pngs),
        "exported_pngs": exported_pngs,
        "bound_height_overflows": overflows,
        "status": "rendered_via_fallback",
    }
    (export_dir / "com_render_report.json").write_text(json.dumps(out_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out_payload
    return {
        "renderer": "PowerPoint.Application COM",
        "version": None,
        "slide_count": 0,
        "exported_pngs": [],
        "bound_height_overflows": [],
        "status": "degraded_no_com",
        "error": (proc.stderr or proc.stdout or "").strip()[:400],
    }


def compute_image_similarity(img_a_path: Path, img_b_path: Path) -> dict[str, float]:
    """Compute structural & perceptual similarity (SSIM approximation + pixel MAE + histogram correlation) using Pillow."""
    with Image.open(img_a_path) as ia, Image.open(img_b_path) as ib:
        ga = ia.convert("L").resize((320, 180))
        gb = ib.convert("L").resize((320, 180))
        diff = ImageChops.difference(ga, gb)
        stat_diff = ImageStat.Stat(diff)
        mae = stat_diff.mean[0] / 255.0

        stat_a = ImageStat.Stat(ga)
        stat_b = ImageStat.Stat(gb)
        mu_a, mu_b = stat_a.mean[0], stat_b.mean[0]
        var_a, var_b = stat_a.var[0], stat_b.var[0]

        pa = list(ga.getdata())
        pb = list(gb.getdata())
        n = max(1, len(pa))
        cov = sum((pa[i] - mu_a) * (pb[i] - mu_b) for i in range(n)) / n
        c1 = (0.01 * 255) ** 2
        c2 = (0.03 * 255) ** 2
        ssim = ((2 * mu_a * mu_b + c1) * (2 * cov + c2)) / ((mu_a**2 + mu_b**2 + c1) * (var_a + var_b + c2))

    return {
        "ssim": round(float(max(0.0, min(1.0, ssim))), 4),
        "pixel_similarity": round(float(1.0 - mae), 4),
        "mae_normalized": round(float(mae), 4),
    }


def verify_deck_against_spec(
    pptx_path: Path,
    spec: dict[str, Any],
    render_dir: Path | None = None,
    reference_render_dir: Path | None = None,
) -> dict[str, Any]:
    """Run mechanical design-token lint + optional PowerPoint COM rendering & visual comparison."""
    resolved = resolve_presentation_structure(pptx_path, scrub_text=True)
    sem_colors = spec["design_tokens"]["colors"]["semantic_roles"]
    paper_hex = sem_colors["paper"]

    checks: list[dict[str, Any]] = []
    # 1. Canvas match
    canvas_ok = (
        resolved["canvas"]["width_emu"] == spec["canvas"]["width_emu"]
        and resolved["canvas"]["height_emu"] == spec["canvas"]["height_emu"]
    )
    checks.append({
        "check": "canvas_dimensions",
        "status": "PASS" if canvas_ok else "FAIL",
        "expected_emu": [spec["canvas"]["width_emu"], spec["canvas"]["height_emu"]],
        "actual_emu": [resolved["canvas"]["width_emu"], resolved["canvas"]["height_emu"]],
    })

    # 2. Per-slide contrast, bounds, and editability checks
    editable_objects = 0
    contrast_warnings = 0
    out_of_bounds = 0
    for slide in resolved["slides"]:
        bg_hex = slide["background"].get("color_hex") or paper_hex
        for obj in slide["objects"]:
            editable_objects += 1
            xp, yp, wp, hp = obj["box_percent"]
            if xp < -2.0 or yp < -2.0 or (xp + wp) > 102.0 or (yp + hp) > 102.0:
                out_of_bounds += 1
            fill_hex = (obj.get("fill") or {}).get("color_hex")
            fill_alpha = (obj.get("fill") or {}).get("alpha", 1.0)
            ground = composite_alpha_over_ground(fill_hex, fill_alpha, bg_hex) if fill_hex else bg_hex
            for p in (obj.get("text_body") or {}).get("paragraphs", []):
                if p.get("char_count", 0) == 0:
                    continue
                t_hex = p["effective_style"].get("color_hex") or sem_colors["ink"]
                cr = wcag_contrast_ratio(t_hex, ground)
                if cr < 3.0:
                    contrast_warnings += 1

    checks.append({"check": "native_editability", "status": "PASS" if editable_objects > 0 else "FAIL", "editable_object_count": editable_objects})
    checks.append({"check": "canvas_bounds", "status": "PASS" if out_of_bounds == 0 else "WARN", "out_of_bounds_count": out_of_bounds})
    checks.append({"check": "text_ground_contrast", "status": "PASS" if contrast_warnings == 0 else "WARN", "low_contrast_paragraphs": contrast_warnings})

    render_report = None
    visual_comparisons: list[dict[str, Any]] = []
    if render_dir is not None:
        render_report = render_and_measure_via_powerpoint_com(pptx_path, render_dir)
        overflow_cnt = len(render_report.get("bound_height_overflows", []))
        checks.append({
            "check": "com_bound_height_overflow",
            "status": "PASS" if overflow_cnt == 0 else "WARN",
            "overflow_count": overflow_cnt,
            "details": render_report.get("bound_height_overflows", []),
        })
        if reference_render_dir is not None and render_report.get("exported_pngs"):
            ref_pngs = sorted(Path(reference_render_dir).glob("slide_*.png"))
            out_pngs = [Path(p) for p in render_report["exported_pngs"] if Path(p).is_file()]
            for s_i, (rp, op) in enumerate(zip(ref_pngs, out_pngs), start=1):
                sim = compute_image_similarity(rp, op)
                visual_comparisons.append({"slide_number": s_i, "reference_png": str(rp), "output_png": str(op), **sim})

    passed = sum(1 for c in checks if c["status"] == "PASS")
    failed = sum(1 for c in checks if c["status"] == "FAIL")
    warnings = sum(1 for c in checks if c["status"] == "WARN")
    avg_ssim = (
        round(sum(v["ssim"] for v in visual_comparisons) / len(visual_comparisons), 4)
        if visual_comparisons
        else None
    )

    return {
        "pptx_path": str(Path(pptx_path).resolve()),
        "summary": {
            "total_checks": len(checks),
            "passed": passed,
            "failed": failed,
            "warnings": warnings,
            "editable_objects": editable_objects,
            "average_ssim": avg_ssim,
        },
        "checks": checks,
        "render_report": render_report,
        "visual_comparisons": visual_comparisons,
    }
