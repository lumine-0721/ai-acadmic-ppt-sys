#!/usr/bin/env python3
"""Layout Geometry, Alignment Constraints, Visual Semantic Roles & Slide Role Classification.

Adapted in part from MIT-licensed open-source implementations:
- jameshemson-slides (commit 245c0e6858df9afe6a3381e36244812f3d2e4452):
  source/skills/build-deck/scripts/tokens.py (grid_from_rects, resolve_colour_roles)
"""

from __future__ import annotations

from typing import Any

from .ooxml_resolver import EMU_PER_PT, luminance_of_hex

ACADEMIC_CONTENT_ROLES = (
    "Cover",
    "Agenda",
    "Section Divider",
    "Background / Context",
    "Problem Statement",
    "Method",
    "Experimental Setup",
    "Results",
    "Comparison",
    "Discussion",
    "Conclusion",
    "References",
    "Ending",
)

DESIGN_ROLES = (
    "HERO_COVER",
    "AGENDA_INDEX",
    "SECTION_DIVIDER",
    "SPLIT_MEDIA_EXPLANATION",
    "DUAL_COLUMN_COMPARISON",
    "DATA_BENCHMARK_VIEW",
    "METHOD_PROCESS_FLOW",
    "MULTI_CARD_GRID",
    "SINGLE_FOCUS_STATEMENT",
    "CLOSING_SLIDE",
)


def flatten_drawable_objects(objects: list[dict[str, Any]], parent_group_id: str | None = None) -> list[dict[str, Any]]:
    """Flatten slide objects (including group children with composed world coordinates) into leaf items."""
    flat: list[dict[str, Any]] = []
    for obj in objects:
        if obj.get("object_type") == "grpSp":
            gid = f"grp_{obj.get('shape_id')}"
            flat.extend(flatten_drawable_objects(obj.get("children", []), gid))
        else:
            item = dict(obj)
            if parent_group_id:
                item["parent_group_id"] = parent_group_id
            flat.append(item)
    return flat


def resolve_semantic_color_roles(
    theme_colors: dict[str, str],
    observed_colors: list[dict[str, Any]],
) -> dict[str, Any]:
    """Map theme & observed colors to semantic roles (ink, paper, accent, muted) and Office Theme slots.

    Adapted from jameshemson-slides tokens.resolve_colour_roles (MIT).
    """
    candidates: dict[str, str] = {}
    for k, v in theme_colors.items():
        if v and v.startswith("#") and len(v) == 7:
            candidates[k] = v.upper()
    for idx, entry in enumerate(observed_colors[:6]):
        chex = entry.get("color_hex")
        if chex and chex.startswith("#") and len(chex) == 7 and chex.upper() not in candidates.values():
            candidates[f"observed_{idx+1}"] = chex.upper()

    if not candidates:
        candidates = {"dk1": "#1A202C", "lt1": "#FFFFFF", "accent1": "#1F4E79", "accent2": "#2B6CB0"}

    by_lum = sorted(candidates.items(), key=lambda kv: luminance_of_hex(kv[1]))
    ink_hex = theme_colors.get("dk1") if luminance_of_hex(theme_colors.get("dk1", "#FFFFFF")) < 90 else by_lum[0][1]
    paper_hex = theme_colors.get("lt1") if luminance_of_hex(theme_colors.get("lt1", "#000000")) > 200 else by_lum[-1][1]

    accent1 = theme_colors.get("accent1")
    if not accent1 or accent1 in (ink_hex, paper_hex):
        mid = [v for _, v in by_lum if v not in (ink_hex, paper_hex)]
        accent1 = mid[0] if mid else "#1F4E79"

    accent2 = theme_colors.get("accent2")
    if not accent2 or accent2 in (ink_hex, paper_hex, accent1):
        mid2 = [v for _, v in by_lum if v not in (ink_hex, paper_hex, accent1)]
        accent2 = mid2[0] if mid2 else accent1

    muted_hex = theme_colors.get("dk2")
    if not muted_hex or muted_hex in (ink_hex, paper_hex, accent1):
        muted_hex = "#5A6A7E"

    return {
        "semantic_roles": {
            "ink": ink_hex,
            "paper": paper_hex,
            "accent": accent1,
            "muted": muted_hex,
        },
        "office_theme_mapping": {
            "PrimaryAccent": accent1,
            "SecondaryAccent": accent2,
            "DarkText": ink_hex,
            "LightBackground": paper_hex,
        },
    }


def derive_grid_and_safe_area(
    slides: list[dict[str, Any]],
    canvas_w: int,
    canvas_h: int,
) -> dict[str, Any]:
    """Derive 12-column grid, margins, gutter, and safe content area.

    Adapted from jameshemson-slides tokens.grid_from_rects (MIT) + SlidePoise frame deduction.
    """
    content_rects: list[tuple[int, int, int, int]] = []
    header_bottoms_pct: list[float] = []
    footer_tops_pct: list[float] = []

    for slide in slides:
        for obj in flatten_drawable_objects(slide.get("objects", [])):
            x, y, w, h = obj["world_box_emu"]
            if w <= 0 or h <= 0:
                continue
            xp, yp, wp, hp = obj["box_percent"]
            # Skip full-bleed background shapes
            if wp >= 94.0 and hp >= 94.0:
                continue
            ph_type = (obj.get("placeholder") or {}).get("type", "").lower()
            if any(k in ph_type for k in ("ftr", "footer", "sldnum", "slide_number", "dt", "date")) or (yp >= 90.0 and hp <= 8.0):
                footer_tops_pct.append(yp)
                continue
            if "hdr" in ph_type or (yp <= 4.0 and hp <= 5.0 and obj.get("text_body", {}).get("total_chars", 0) < 25):
                header_bottoms_pct.append(yp + hp)
                continue
            content_rects.append((x, y, w, h))

    baseline_emu = round(canvas_h * 0.0133)
    if not content_rects:
        margin_x = round(canvas_w * 0.05)
        margin_top = round(canvas_h * 0.08)
        margin_bottom = round(canvas_h * 0.08)
        gutter = round(canvas_w * 0.0167)
    else:
        margin_x = max(0, min(r[0] for r in content_rects))
        margin_top = max(0, min(r[1] for r in content_rects))
        margin_bottom = max(0, canvas_h - max(r[1] + r[3] for r in content_rects))

        tolerance = 12700  # 1 pt in EMU
        min_gap = None
        for i, a in enumerate(content_rects):
            for j, b in enumerate(content_rects):
                if i == j:
                    continue
                if abs(a[1] - b[1]) <= tolerance * 6 and b[0] >= a[0] + a[2]:
                    gap = b[0] - (a[0] + a[2])
                    if gap > tolerance:
                        if min_gap is None or gap < min_gap:
                            min_gap = gap
        gutter = min_gap if min_gap is not None else round(canvas_w * 0.0167)

    safe_x = round(max(2.5, min(12.0, margin_x / canvas_w * 100.0)), 2)
    safe_y = round(max(header_bottoms_pct + [max(3.0, min(14.0, margin_top / canvas_h * 100.0))]), 2)
    safe_bottom = round(min(footer_tops_pct + [100.0 - max(3.0, min(14.0, margin_bottom / canvas_h * 100.0))]), 2)
    safe_w = round(max(70.0, 100.0 - 2.0 * safe_x), 2)
    safe_h = round(max(65.0, safe_bottom - safe_y), 2)

    return {
        "grid": {
            "columns": 12,
            "margin_x_emu": int(margin_x),
            "margin_top_emu": int(margin_top),
            "margin_bottom_emu": int(margin_bottom),
            "gutter_emu": int(gutter),
            "gutter_pt": round(gutter / EMU_PER_PT, 2),
            "baseline_emu": int(baseline_emu),
            "baseline_pt": round(baseline_emu / EMU_PER_PT, 2),
        },
        "margins_percent": {
            "left": round(margin_x / canvas_w * 100.0, 2),
            "right": round(margin_x / canvas_w * 100.0, 2),
            "top": round(margin_top / canvas_h * 100.0, 2),
            "bottom": round(margin_bottom / canvas_h * 100.0, 2),
        },
        "safe_area_percent": {
            "x": safe_x,
            "y": safe_y,
            "w": safe_w,
            "h": safe_h,
        },
    }


def infer_element_semantic_role(
    obj: dict[str, Any],
    slide_number: int,
    total_slides: int,
    visual_override: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Infer an element's visual/design semantic role with confidence and conflict arbitration."""
    xp, yp, wp, hp = obj["box_percent"]
    otype = obj.get("object_type", "sp")
    ph = obj.get("placeholder") or {}
    ph_type = (ph.get("type") or "").upper()
    tbody = obj.get("text_body") or {}
    total_chars = tbody.get("total_chars", 0)
    paras = tbody.get("paragraphs", [])
    max_font_pt = max((p["effective_style"]["font_size_pt"] for p in paras), default=0.0)
    signals = {}
    for p in paras:
        for k, v in (p.get("structural_signals") or {}).items():
            if v:
                signals[k] = True

    structural_role = "DECORATION_SHAPE"
    slot_name = "DECORATION_SLOT"
    replaceable = False
    confidence = 0.85
    evidence_source = "observed"

    if wp >= 94.0 and hp >= 94.0:
        structural_role = "BACKGROUND_SURFACE"
        slot_name = "BACKGROUND_ASSET"
        replaceable = False
        confidence = 0.98
    elif any(k in ph_type for k in ("SLDNUM", "SLIDE_NUMBER")) or (yp >= 89.0 and xp >= 78.0 and total_chars <= 8 and total_chars > 0):
        structural_role = "PAGE_NUMBER"
        slot_name = "FOOTER_PAGE_NUM"
        replaceable = False
        confidence = 0.95
    elif any(k in ph_type for k in ("FTR", "FOOTER", "DT", "DATE")) or (yp >= 90.0 and hp <= 8.0 and total_chars > 0):
        structural_role = "FOOTER_META"
        slot_name = "FOOTER_BAR_SLOT"
        replaceable = False
        confidence = 0.92
    elif obj.get("has_omml_formula"):
        structural_role = "FORMULA_EQUATION"
        slot_name = "FORMULA_SLOT"
        replaceable = True
        confidence = 0.95
    elif "chart" in obj:
        structural_role = "SCIENTIFIC_CHART"
        slot_name = "MAIN_FIGURE_SLOT"
        replaceable = True
        confidence = 0.97
    elif "table" in obj:
        structural_role = "DATA_TABLE"
        slot_name = "COMPARISON_TABLE_SLOT"
        replaceable = True
        confidence = 0.97
    elif otype == "pic":
        if wp <= 15.0 and hp <= 14.0 and (yp <= 15.0 or yp >= 85.0):
            structural_role = "BRAND_LOGO"
            slot_name = "BRAND_LOGO_ASSET"
            replaceable = False
            confidence = 0.88
            evidence_source = "inferred"
        elif hp <= 14.5 or (wp * hp) < 220.0:
            structural_role = "FORMULA_EQUATION"
            slot_name = "FORMULA_SLOT"
            replaceable = True
            confidence = 0.89
            evidence_source = "inferred"
        else:
            structural_role = "SCIENTIFIC_FIGURE"
            slot_name = "MAIN_FIGURE_SLOT"
            replaceable = True
            confidence = 0.93
    elif otype == "cxnSp" or (total_chars == 0 and (hp <= 1.2 or wp <= 0.8)):
        if yp <= 24.0 and wp >= 20.0 and hp <= 1.5:
            structural_role = "TITLE_DIVIDER_LINE"
            slot_name = "HEADER_DIVIDER_ASSET"
        else:
            structural_role = "FLOW_CONNECTOR_OR_DIVIDER"
            slot_name = "DECORATION_DIVIDER_ASSET"
        replaceable = False
        confidence = 0.86
        evidence_source = "inferred"
    elif total_chars > 0 or ph_type:
        if any(k in ph_type for k in ("TITLE", "CTRTITLE", "CENTER_TITLE")) and "SUB" not in ph_type:
            structural_role = "MAIN_TITLE"
            slot_name = "TITLE_SLOT"
            replaceable = True
            confidence = 0.98
        elif "SUBTITLE" in ph_type:
            structural_role = "SUBTITLE_OR_META"
            slot_name = "SUBTITLE_SLOT"
            replaceable = True
            confidence = 0.96
        elif yp <= 20.0 and max_font_pt >= 21.0:
            structural_role = "MAIN_TITLE"
            slot_name = "TITLE_SLOT"
            replaceable = True
            confidence = 0.91
            evidence_source = "inferred"
        elif yp <= 18.0 and wp <= 25.0 and total_chars <= 24:
            structural_role = "SECTION_BADGE"
            slot_name = "SECTION_BADGE_SLOT"
            replaceable = True
            confidence = 0.84
            evidence_source = "inferred"
        elif signals.get("is_caption_like") or (max_font_pt <= 12.5 and hp <= 12.0 and yp >= 55.0):
            structural_role = "FIGURE_CAPTION_OR_CONDITIONS"
            slot_name = "CAPTION_SLOT"
            replaceable = True
            confidence = 0.85
            evidence_source = "inferred"
        elif yp >= 74.0 and hp <= 20.0 and wp >= 55.0 and (obj.get("fill", {}).get("color_hex") or signals.get("is_conclusion_like")):
            structural_role = "TAKEAWAY_CALLOUT"
            slot_name = "CONCLUSION_SLOT"
            replaceable = True
            confidence = 0.87
            evidence_source = "inferred"
        elif not ph_type and (hp < 7.5 or wp < 20.0 or (wp * hp < 260.0 and total_chars <= 22)):
            structural_role = "BADGE_OR_TAG_CHROME"
            slot_name = "BADGE_CHROME_ASSET"
            replaceable = False
            confidence = 0.86
            evidence_source = "inferred"
        else:
            structural_role = "BODY_EXPLANATION"
            slot_name = "EXPLANATION_SLOT"
            replaceable = True
            confidence = 0.88
            evidence_source = "observed" if "BODY" in ph_type or "OBJECT" in ph_type else "inferred"
    else:
        # Non-text shape (card background panel or decorative accent)
        if wp >= 18.0 and hp >= 15.0:
            structural_role = "CONTAINER_PANEL"
            slot_name = "CARD_PANEL_ASSET"
        else:
            structural_role = "DECORATIVE_ACCENT"
            slot_name = "DECORATION_ASSET"
        replaceable = False
        confidence = 0.82
        evidence_source = "inferred"

    conflict_record = None
    if visual_override:
        v_role = visual_override.get("role")
        v_type = visual_override.get("claimed_object_type")
        # Conflict check: never allow visual model to override structural object_type or coordinates
        if v_type and v_type != otype:
            conflict_record = {
                "slide_number": slide_number,
                "object_id": str(obj.get("shape_id")),
                "structural_fact": f"object_type={otype}, box_percent={obj['box_percent']}",
                "visual_claim": f"claimed_object_type={v_type}, role={v_role}",
                "resolution": "structural_fact_retained_with_visual_role_note",
            }
        if v_role and structural_role in ("BODY_EXPLANATION", "DECORATIVE_ACCENT", "FLOW_CONNECTOR_OR_DIVIDER"):
            structural_role = str(v_role).upper()
            evidence_source = "inferred"
            confidence = round(float(visual_override.get("confidence", 0.88)), 2)

    role_info = {
        "semantic_role": structural_role,
        "base_slot_name": slot_name,
        "replaceable_content": replaceable,
        "confidence": confidence,
        "evidence_source": evidence_source,
    }
    return role_info, conflict_record


def extract_pairwise_alignments_and_overlaps(
    annotated_objects: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute relative alignment constraints (left, right, top, bottom, center, equal spacing) and overlaps."""
    slots = [o for o in annotated_objects if o["role_info"]["replaceable_content"]]
    if not slots:
        slots = [o for o in annotated_objects if o["role_info"]["semantic_role"] != "BACKGROUND_SURFACE"]

    tol_pct = 1.5
    alignments: list[dict[str, Any]] = []
    overlaps: list[dict[str, Any]] = []

    for i, a in enumerate(slots):
        ax, ay, aw, ah = a["box_percent"]
        a_label = a.get("assigned_slot_name") or f"obj_{a['shape_id']}"
        for j in range(i + 1, len(slots)):
            b = slots[j]
            bx, by, bw, bh = b["box_percent"]
            b_label = b.get("assigned_slot_name") or f"obj_{b['shape_id']}"

            if abs(ax - bx) <= tol_pct:
                alignments.append({
                    "type": "left_aligned",
                    "source": a_label,
                    "target": b_label,
                    "delta_percent": round(abs(ax - bx), 3),
                    "rule": f"{a_label} aligned with {b_label} left edge",
                })
            if abs((ax + aw) - (bx + bw)) <= tol_pct:
                alignments.append({
                    "type": "right_aligned",
                    "source": a_label,
                    "target": b_label,
                    "delta_percent": round(abs((ax + aw) - (bx + bw)), 3),
                    "rule": f"{a_label} aligned with {b_label} right edge",
                })
            if abs(ay - by) <= tol_pct:
                alignments.append({
                    "type": "top_aligned",
                    "source": a_label,
                    "target": b_label,
                    "delta_percent": round(abs(ay - by), 3),
                    "rule": f"{a_label} shares top baseline with {b_label}",
                })
            if abs((ax + aw / 2.0) - (bx + bw / 2.0)) <= tol_pct:
                alignments.append({
                    "type": "center_x_aligned",
                    "source": a_label,
                    "target": b_label,
                    "delta_percent": round(abs((ax + aw / 2.0) - (bx + bw / 2.0)), 3),
                    "rule": f"{a_label} horizontally centered with {b_label}",
                })

            # Overlap check
            ix1, iy1 = max(ax, bx), max(ay, by)
            ix2, iy2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
            if ix2 > ix1 + 0.5 and iy2 > iy1 + 0.5:
                inter_area = (ix2 - ix1) * (iy2 - iy1)
                min_area = max(0.01, min(aw * ah, bw * bh))
                if inter_area / min_area > 0.05:
                    overlaps.append({
                        "slot_a": a_label,
                        "slot_b": b_label,
                        "overlap_ratio": round(inter_area / min_area, 3),
                    })

    # Reading order: top-to-bottom (binned by 8% y), then left-to-right
    ordered = sorted(slots, key=lambda s: (round(s["box_percent"][1] / 8.0), s["box_percent"][0]))
    reading_order = [s.get("assigned_slot_name") or f"obj_{s['shape_id']}" for s in ordered]

    return {
        "alignments": alignments,
        "overlaps": overlaps,
        "non_overlapping_pairs": len(overlaps) == 0,
        "reading_order": reading_order,
    }


def classify_slide_roles(
    slide: dict[str, Any],
    annotated_objects: list[dict[str, Any]],
    total_slides: int,
) -> dict[str, Any]:
    """Classify both Design Role (structural visual pattern) and Academic Content Role (decoupled)."""
    s_num = slide["slide_number"]
    lname = (slide.get("layout_name") or "").lower()

    replaceable = [o for o in annotated_objects if o["role_info"]["replaceable_content"]]
    roles_present = [o["role_info"]["semantic_role"] for o in replaceable]
    fig_objs = [o for o in replaceable if o["role_info"]["semantic_role"] in ("SCIENTIFIC_FIGURE", "SCIENTIFIC_CHART", "DATA_TABLE", "FORMULA_EQUATION")]
    body_objs = [o for o in replaceable if o["role_info"]["semantic_role"] == "BODY_EXPLANATION"]
    panel_objs = [o for o in annotated_objects if o["role_info"]["semantic_role"] == "CONTAINER_PANEL"]

    signals: dict[str, bool] = {}
    for o in annotated_objects:
        for p in (o.get("text_body") or {}).get("paragraphs", []):
            for k, v in (p.get("structural_signals") or {}).items():
                if v:
                    signals[k] = True

    # 1. Design Role (purely structural & layout geometry driven)
    if s_num == 1 and len(fig_objs) == 0 and len(body_objs) <= 1:
        design_role = "HERO_COVER"
    elif s_num == total_slides and total_slides > 3 and len(fig_objs) == 0 and len(body_objs) <= 1 and signals.get("is_ending_like"):
        design_role = "CLOSING_SLIDE"
    elif ("section" in lname or "divider" in lname or signals.get("is_section_like")) and len(fig_objs) == 0 and len(body_objs) <= 1:
        design_role = "SECTION_DIVIDER"
    elif signals.get("is_toc_like") and len(fig_objs) == 0:
        design_role = "AGENDA_INDEX"
    elif len(fig_objs) >= 2:
        design_role = "DATA_BENCHMARK_VIEW"
    elif len(fig_objs) == 1 and len(body_objs) >= 1:
        design_role = "SPLIT_MEDIA_EXPLANATION"
    elif len(body_objs) == 2 and abs(body_objs[0]["box_percent"][0] - body_objs[1]["box_percent"][0]) > 25.0:
        design_role = "DUAL_COLUMN_COMPARISON"
    elif len(panel_objs) >= 3 or len(body_objs) >= 3:
        design_role = "METHOD_PROCESS_FLOW" if signals.get("is_method_like") else "MULTI_CARD_GRID"
    else:
        design_role = "SINGLE_FOCUS_STATEMENT"

    # 2. Academic Content Role (13 candidate roles, decoupled from Design Role)
    evidence_source = "inferred"
    confidence = 0.86
    if design_role == "HERO_COVER":
        content_role = "Cover"
        confidence = 0.96
    elif design_role == "CLOSING_SLIDE" or signals.get("is_ending_like"):
        content_role = "Ending"
        confidence = 0.94
    elif design_role == "AGENDA_INDEX" or signals.get("is_toc_like"):
        content_role = "Agenda"
        confidence = 0.93
    elif design_role == "SECTION_DIVIDER":
        content_role = "Section Divider"
        confidence = 0.91
    elif signals.get("is_reference_like"):
        content_role = "References"
        confidence = 0.92
    elif signals.get("is_conclusion_like") or (s_num == total_slides and total_slides >= 4):
        content_role = "Conclusion"
        confidence = 0.88
    elif signals.get("is_comparison_like") or design_role == "DUAL_COLUMN_COMPARISON":
        content_role = "Comparison"
        confidence = 0.87
    elif signals.get("is_method_like"):
        content_role = "Method" if "FORMULA_EQUATION" not in roles_present else "Experimental Setup"
        confidence = 0.88
    elif signals.get("is_problem_like"):
        content_role = "Problem Statement"
        confidence = 0.86
    elif signals.get("is_background_like") or (s_num == 2 and len(fig_objs) == 0):
        content_role = "Background / Context"
        confidence = 0.84
    elif len(fig_objs) >= 1 or signals.get("is_result_like"):
        content_role = "Results"
        confidence = 0.90
    else:
        content_role = "Discussion"
        confidence = 0.80

    rationale = (
        f"design_role={design_role} derived from {len(fig_objs)} figure/chart/table slot(s) and "
        f"{len(body_objs)} text explanation slot(s); content_role={content_role} inferred without hard-binding layout."
    )
    return {
        "slide_number": s_num,
        "design_role": design_role,
        "content_role": content_role,
        "confidence": confidence,
        "evidence_source": evidence_source,
        "rationale": rationale,
    }
