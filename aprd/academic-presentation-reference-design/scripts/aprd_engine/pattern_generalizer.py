#!/usr/bin/env python3
"""Visual Design System Extraction, Content Slot Abstraction, Pattern Clustering & Generalization.

Adapted in part from MIT-licensed open-source implementations:
- hugohe3/ppt-master (commit f3633771fe699d327f02ae86393831bbcf5169f7):
  scripts/svg_quality/slot_capacity.py (binary-search CJK & Latin capacity estimation)
"""

from __future__ import annotations

import math
from typing import Any

from .geometry_semantics import (
    classify_slide_roles,
    derive_grid_and_safe_area,
    extract_pairwise_alignments_and_overlaps,
    flatten_drawable_objects,
    infer_element_semantic_role,
    resolve_semantic_color_roles,
)
from .ooxml_resolver import EMU_PER_PT

# Average glyph advance ratios relative to font_size_pt (calibrated against Arial/Calibri/Microsoft YaHei)
LATIN_AVG_ADVANCE_FACTOR = 0.54
CJK_FULLWIDTH_ADVANCE_FACTOR = 1.02


def estimate_slot_text_capacity(
    width_pt: float,
    height_pt: float,
    font_size_pt: float,
    line_height_factor: float = 1.25,
    padding_pt: float = 10.0,
) -> dict[str, Any]:
    """Estimate advisory CJK and Latin text capacity using binary search over glyph advances.

    Adapted from hugohe3/ppt-master slot_capacity.py (MIT).
    Note: Exact fitting depends on runtime font metrics and Office COM BoundHeight verification.
    """
    usable_w = max(8.0, width_pt - 2.0 * padding_pt)
    usable_h = max(font_size_pt, height_pt - 2.0 * (padding_pt * 0.6))
    line_pitch = max(font_size_pt * 1.05, font_size_pt * line_height_factor)
    max_lines = max(1, 1 + math.floor((usable_h - font_size_pt) / line_pitch))

    def binary_search_chars(advance_per_char: float) -> int:
        low, high = 0, 1
        while high < 10000 and (high * advance_per_char) <= usable_w:
            low, high = high, high * 2
        while low + 1 < high:
            mid = (low + high) // 2
            if (mid * advance_per_char) <= usable_w:
                low = mid
            else:
                high = mid
        return max(1, low)

    cjk_cpl = binary_search_chars(font_size_pt * CJK_FULLWIDTH_ADVANCE_FACTOR)
    latin_cpl = binary_search_chars(font_size_pt * LATIN_AVG_ADVANCE_FACTOR)

    return {
        "advisory_only": True,
        "font_size_pt": round(font_size_pt, 1),
        "line_height_factor": line_height_factor,
        "max_lines": int(max_lines),
        "cjk_chars_per_line": int(cjk_cpl),
        "cjk_recommended_max_chars": int(cjk_cpl * max_lines),
        "latin_chars_per_line": int(latin_cpl),
        "latin_recommended_max_chars": int(latin_cpl * max_lines),
        "verification_note": "Character counts are advisory estimates; verify final fit via Office COM TextFrame2.TextRange.BoundHeight or rendered visual QA.",
    }


def _assign_unique_slot_names(annotated_objs: list[dict[str, Any]]) -> None:
    """Assign canonical APRD slot names (TITLE_SLOT, MAIN_FIGURE_SLOT, EXPLANATION_SLOT, etc.)."""
    ordered = sorted(annotated_objs, key=lambda o: (round(o["box_percent"][1] / 8.0), o["box_percent"][0]))
    counts: dict[str, int] = {}
    for obj in ordered:
        base = obj["role_info"]["base_slot_name"]
        counts[base] = counts.get(base, 0) + 1

    seen: dict[str, int] = {}
    for obj in ordered:
        base = obj["role_info"]["base_slot_name"]
        if counts[base] == 1:
            obj["assigned_slot_name"] = base
        else:
            seen[base] = seen.get(base, 0) + 1
            suffix = chr(ord("A") + seen[base] - 1) if seen[base] <= 26 else str(seen[base])
            obj["assigned_slot_name"] = f"{base}_{suffix}"


def _generalized_pattern_identity(design_role: str, slots: list[dict[str, Any]]) -> tuple[str, str, dict[str, Any]]:
    """Map a slide's design role and slot topology to a generalized reusable academic pattern."""
    slot_roles = [s["semantic_role"] for s in slots]
    fig_count = sum(1 for r in slot_roles if r in ("SCIENTIFIC_FIGURE", "SCIENTIFIC_CHART", "DATA_TABLE", "FORMULA_EQUATION"))
    exp_count = sum(1 for r in slot_roles if r == "BODY_EXPLANATION")
    has_conclusion = "TAKEAWAY_CALLOUT" in slot_roles

    if design_role == "HERO_COVER":
        pattern_key = "ACADEMIC_COVER_HERO"
        semantic_role = "cover_title_and_metadata"
        gen = {
            "applicable_content_roles": ["Cover"],
            "inapplicable_cases": ["Dense multi-figure empirical results", "Multi-column method comparison"],
            "fallback_patterns": ["SECTION_DIVIDER_CLEAN"],
            "density_tier": "breathing",
        }
    elif design_role == "SECTION_DIVIDER":
        pattern_key = "SECTION_DIVIDER_CLEAN"
        semantic_role = "section_transition_header"
        gen = {
            "applicable_content_roles": ["Section Divider", "Agenda"],
            "inapplicable_cases": ["Detailed experimental data tables or multi-panel spectra"],
            "fallback_patterns": ["SINGLE_CLAIM_STATEMENT"],
            "density_tier": "breathing",
        }
    elif design_role == "AGENDA_INDEX":
        pattern_key = "AGENDA_STRUCTURED_INDEX"
        semantic_role = "agenda_and_roadmap"
        gen = {
            "applicable_content_roles": ["Agenda", "Background / Context"],
            "inapplicable_cases": ["High-resolution spectral plots requiring >=45% canvas area"],
            "fallback_patterns": ["MULTI_CARD_GRID_OVERVIEW"],
            "density_tier": "medium",
        }
    elif design_role == "SPLIT_MEDIA_EXPLANATION":
        pattern_key = "RESULT_WITH_EVIDENCE"
        semantic_role = "primary_evidence_with_structured_claims"
        gen = {
            "applicable_content_roles": ["Results", "Method", "Experimental Setup", "Background / Context", "Discussion"],
            "inapplicable_cases": [
                "More than 2 independent large figures on one slide",
                "Long prose paragraphs exceeding 180 CJK characters without bulleting",
            ],
            "fallback_patterns": ["MULTI_FIGURE_BENCHMARK", "DUAL_COMPARISON_SPLIT"],
            "density_tier": "medium_high",
        }
    elif design_role == "DATA_BENCHMARK_VIEW" or fig_count >= 2:
        pattern_key = "MULTI_FIGURE_BENCHMARK"
        semantic_role = "parallel_visual_evidence_and_caption_synthesis"
        gen = {
            "applicable_content_roles": ["Results", "Comparison", "Experimental Setup"],
            "inapplicable_cases": [
                "Single narrative thesis without visual/tabular evidence",
                "More than 4 sub-figures where axis labels drop below 10pt equivalent",
            ],
            "fallback_patterns": ["RESULT_WITH_EVIDENCE"],
            "density_tier": "dense",
        }
    elif design_role == "DUAL_COLUMN_COMPARISON" or exp_count == 2:
        pattern_key = "DUAL_COMPARISON_SPLIT"
        semantic_role = "two_column_contrast_or_mechanism_comparison"
        gen = {
            "applicable_content_roles": ["Comparison", "Problem Statement", "Background / Context", "Discussion", "Method"],
            "inapplicable_cases": ["Wide panoramic workflow diagrams requiring >80% horizontal span"],
            "fallback_patterns": ["MULTI_CARD_GRID_OVERVIEW", "RESULT_WITH_EVIDENCE"],
            "density_tier": "medium",
        }
    elif design_role in ("METHOD_PROCESS_FLOW", "MULTI_CARD_GRID"):
        pattern_key = "METHOD_OR_KEYPOINT_GRID"
        semantic_role = "multi_step_workflow_or_parallel_takeaways"
        gen = {
            "applicable_content_roles": ["Method", "Experimental Setup", "Problem Statement", "Conclusion", "Discussion"],
            "inapplicable_cases": ["Large single spectrum or microscopy image requiring detailed zoom"],
            "fallback_patterns": ["DUAL_COMPARISON_SPLIT"],
            "density_tier": "medium_high",
        }
    elif design_role == "CLOSING_SLIDE":
        pattern_key = "ACADEMIC_CLOSING_QA"
        semantic_role = "closing_acknowledgements_and_qa"
        gen = {
            "applicable_content_roles": ["Ending", "Conclusion", "References"],
            "inapplicable_cases": ["Empirical data presentation"],
            "fallback_patterns": ["SINGLE_CLAIM_STATEMENT"],
            "density_tier": "breathing",
        }
    else:
        pattern_key = "SINGLE_CLAIM_WITH_SUPPORT" if not has_conclusion else "EVIDENCE_AND_BOTTOM_TAKEAWAY"
        semantic_role = "focused_academic_claim_and_supporting_body"
        gen = {
            "applicable_content_roles": ["Background / Context", "Problem Statement", "Discussion", "Conclusion", "References"],
            "inapplicable_cases": ["Multi-chart quantitative benchmark"],
            "fallback_patterns": ["DUAL_COMPARISON_SPLIT", "RESULT_WITH_EVIDENCE"],
            "density_tier": "medium",
        }
    return pattern_key, semantic_role, gen


def _build_slot_contract(
    obj: dict[str, Any],
    canvas_w_pt: float,
    canvas_h_pt: float,
    default_colors: dict[str, str],
    default_fonts: dict[str, str],
) -> dict[str, Any]:
    role = obj["role_info"]["semantic_role"]
    slot_name = obj["assigned_slot_name"]
    xp, yp, wp, hp = obj["box_percent"]
    # Clamp or fallback degenerate/out-of-canvas boxes from hostile templates
    if wp <= 0.5 or hp <= 0.5:
        if role == "MAIN_TITLE":
            xp, yp, wp, hp = 6.0, 8.0, 88.0, 14.0
        else:
            xp, yp, wp, hp = 6.0, 26.0, 88.0, 58.0
    xp = round(max(0.0, min(98.0, float(xp))), 4)
    yp = round(max(0.0, min(98.0, float(yp))), 4)
    wp = round(max(1.0, min(100.0 - xp, float(wp))), 4)
    hp = round(max(1.0, min(100.0 - yp, float(hp))), 4)
    xr, yr, wr, hr = [round(v / 100.0, 4) for v in (xp, yp, wp, hp)]
    w_pt = (wp / 100.0) * canvas_w_pt
    h_pt = (hp / 100.0) * canvas_h_pt

    tbody = obj.get("text_body") or {}
    paras = tbody.get("paragraphs", [])
    if paras:
        first_style = paras[0]["effective_style"]
        font_pt = first_style["font_size_pt"]
        font_family = first_style["font_family"]
        color_hex = first_style["color_hex"]
        bold = first_style["bold"]
        align = paras[0].get("alignment", "l")
    else:
        is_title = role == "MAIN_TITLE"
        font_pt = 26.0 if is_title else (12.0 if "CAPTION" in role else 16.0)
        font_family = default_fonts.get("heading" if is_title else "body", "Calibri")
        color_hex = default_colors.get("ink", "#1A202C")
        bold = is_title
        align = "l"

    if role == "MAIN_TITLE":
        allowed_types = ["TEXT", "ACTION_TITLE"]
        typo_group = "TYPO_GROUP_SLIDE_TITLE"
        overflow_strategy = "Shrink font down to 20pt (max 2 lines); never truncate scientific condition qualifiers."
        required = True
        stretchable = False
    elif role in ("SUBTITLE_OR_META", "SECTION_BADGE"):
        allowed_types = ["TEXT", "METADATA_CHIP"]
        typo_group = "TYPO_GROUP_SUBTITLE"
        overflow_strategy = "Single-line or 2-line wrap; move secondary affiliation/metadata to speaker notes."
        required = False
        stretchable = False
    elif role in ("SCIENTIFIC_FIGURE", "SCIENTIFIC_CHART"):
        allowed_types = ["IMAGE_BOX", "CHART", "SPECTRUM_PLOT"]
        typo_group = "TYPO_GROUP_VISUAL_ASSET"
        overflow_strategy = "Preserve original aspect ratio (keep_aspect=true); never crop axis labels, scale bars, or legends."
        required = True
        stretchable = True
    elif role == "DATA_TABLE":
        allowed_types = ["TABLE", "COMPARISON_MATRIX"]
        typo_group = "TYPO_GROUP_TABLE_CELL"
        overflow_strategy = "Step cell font from 13pt down to 10.5pt; if rows > capacity, split table across continuation slide."
        required = True
        stretchable = True
    elif role == "FORMULA_EQUATION":
        allowed_types = ["OMML_FORMULA", "SVG_FORMULA", "TEXT"]
        typo_group = "TYPO_GROUP_FORMULA"
        overflow_strategy = "Keep equation on single line or align at '='; place variable definitions in adjacent caption slot."
        required = True
        stretchable = False
    elif role == "FIGURE_CAPTION_OR_CONDITIONS":
        allowed_types = ["KV_BAR", "CAPTION_TEXT", "CITATION_TAG"]
        typo_group = "TYPO_GROUP_CAPTION"
        overflow_strategy = "Use concise key-value condition chips (e.g. 'λex = 488 nm | pH 7.4'); move full protocol to notes."
        required = False
        stretchable = True
    elif role == "TAKEAWAY_CALLOUT":
        allowed_types = ["TEXT", "CALLOUT_BANNER", "KV_BAR"]
        typo_group = "TYPO_GROUP_CONCLUSION_CALLOUT"
        overflow_strategy = "Keep takeaway <= 45 CJK chars / 2 lines; move secondary mechanism details to speaker notes."
        required = False
        stretchable = True
    else:
        allowed_types = ["TEXT", "BULLET_LIST", "KEY_VALUE_CHIPS"]
        typo_group = "TYPO_GROUP_BODY_PRIMARY"
        overflow_strategy = "Step font down within typography_group (min 13pt); demote supporting_detail to NotesPage if BoundHeight > allocated."
        required = True
        stretchable = True

    cap = estimate_slot_text_capacity(w_pt, h_pt, font_pt)
    if "TABLE" in allowed_types and "table" in obj:
        cap["table_grid"] = {"rows": obj["table"]["rows"], "cols": obj["table"]["cols"]}
    if "IMAGE_BOX" in allowed_types:
        aspect = round(w_pt / max(1.0, h_pt), 3)
        cap["recommended_aspect_ratio"] = aspect
        cap["keep_aspect"] = True

    return {
        "slot_id": f"sh_{obj['shape_id']}",
        "source_shape_id": obj["shape_id"],
        "slot_name": slot_name,
        "semantic_role": role,
        "allowed_content_types": allowed_types,
        "rect_percent": {"x": xp, "y": yp, "w": wp, "h": hp},
        "rect_ratio": [xr, yr, wr, hr],
        "typography_group": typo_group,
        "style_tokens": {
            "font_family": font_family,
            "font_size_pt": font_pt,
            "color_hex": color_hex,
            "bold": bold,
            "alignment": align,
            "fill_hex": (obj.get("fill") or {}).get("color_hex"),
        },
        "capacity_estimate": cap,
        "required": required,
        "stretchable": stretchable,
        "replaceable": True,
        "overflow_strategy": overflow_strategy,
    }


def _spatial_signature(design_role: str, slots: list[dict[str, Any]]) -> str:
    """Compute layout clustering signature that respects both functional design_role and spatial slot topology."""
    pattern_key, _, _ = _generalized_pattern_identity(design_role, slots)
    parts = [f"PK:{pattern_key}", f"DR:{design_role}"]
    role_buckets: dict[str, list[str]] = {}
    for s in slots:
        r = s["semantic_role"]
        if r in ("SUBTITLE_OR_META", "SECTION_BADGE", "FIGURE_CAPTION_OR_CONDITIONS"):
            continue
        xp = s["rect_percent"]["x"]
        yp = s["rect_percent"]["y"]
        wp = s["rect_percent"]["w"]
        horiz = "FULL" if wp >= 60.0 else ("LEFT" if xp < 45.0 else "RIGHT")
        role_buckets.setdefault(r, []).append(horiz)
    for r in sorted(role_buckets.keys()):
        zones = sorted(set(role_buckets[r]))
        cnt = 1 if r == "MAIN_TITLE" else (2 if len(role_buckets[r]) >= 2 else 1)
        parts.append(f"{r}:{cnt}@{','.join(zones)}")
    return "|".join(parts)


def synthesize_design_system_and_patterns(
    resolved_deck: dict[str, Any],
    visual_annotations: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Synthesize global vs. page-scoped design system tokens, slot contracts, and deduplicated layout patterns."""
    canvas = resolved_deck["canvas"]
    canvas_w = canvas["width_emu"]
    canvas_h = canvas["height_emu"]
    canvas_w_pt = canvas["width_pt"]
    canvas_h_pt = canvas["height_pt"]
    slides = resolved_deck["slides"]
    total_slides = len(slides)

    grid_info = derive_grid_and_safe_area(slides, canvas_w, canvas_h)
    color_roles = resolve_semantic_color_roles(
        resolved_deck["effective_theme_colors"],
        resolved_deck["observed_frequencies"]["colors_by_usage"],
    )

    # Primary fonts
    first_theme = next(iter(resolved_deck["themes"].values()), {"fonts": {"major": {}, "minor": {}}})
    major_f = first_theme["fonts"].get("major", {})
    minor_f = first_theme["fonts"].get("minor", {})
    obs_fonts = resolved_deck["observed_frequencies"]["fonts_by_chars"]
    top_obs_font = obs_fonts[0]["font"] if obs_fonts else "Calibri"
    heading_font = major_f.get("ea") or major_f.get("latin") or top_obs_font
    body_font = minor_f.get("ea") or minor_f.get("latin") or top_obs_font

    # Master text style summary
    first_master = next(iter(resolved_deck["masters"].values()), {"tx_styles": {"title": {}, "body": {}, "other": {}}})
    master_tx_summary = {
        k: {str(lvl): v.get("font_size_pt") for lvl, v in lvls.items() if v.get("font_size_pt")}
        for k, lvls in first_master.get("tx_styles", {}).items()
    }

    # Annotate every slide's objects and separate replaceable slots from fixed design assets
    slide_roles_out: list[dict[str, Any]] = []
    conflicts_out: list[dict[str, Any]] = []
    per_slide_bundles: list[dict[str, Any]] = []
    fixed_chrome_candidates: list[dict[str, Any]] = []

    for m_data in resolved_deck["masters"].values():
        fixed_chrome_candidates.extend(m_data.get("static_chrome", []))
    for l_data in resolved_deck["layouts"].values():
        fixed_chrome_candidates.extend(l_data.get("static_chrome", []))

    for slide in slides:
        s_num = slide["slide_number"]
        v_slide = ((visual_annotations or {}).get("slides") or {}).get(str(s_num), {})
        v_objs = v_slide.get("objects") or {}

        flat_objs = flatten_drawable_objects(slide.get("objects", []))
        annotated: list[dict[str, Any]] = []
        for obj in flat_objs:
            v_ovr = v_objs.get(str(obj.get("shape_id")))
            r_info, conflict = infer_element_semantic_role(obj, s_num, total_slides, v_ovr)
            if conflict:
                conflicts_out.append(conflict)
            obj_copy = dict(obj)
            obj_copy["role_info"] = r_info
            annotated.append(obj_copy)
            if not r_info["replaceable_content"]:
                fixed_chrome_candidates.append({
                    "level": "slide",
                    "slide_number": s_num,
                    "object_id": obj.get("shape_id"),
                    "name": obj.get("name"),
                    "semantic_role": r_info["semantic_role"],
                    "box_percent": obj["box_percent"],
                    "fill": obj.get("fill"),
                })

        replaceable_objs = [o for o in annotated if o["role_info"]["replaceable_content"]]
        # If a slide has no replaceable objects (e.g., pure title or graphic), promote largest object
        if not replaceable_objs and annotated:
            largest = max(annotated, key=lambda x: x["box_percent"][2] * x["box_percent"][3])
            largest["role_info"]["replaceable_content"] = True
            largest["role_info"]["base_slot_name"] = "TITLE_SLOT" if s_num == 1 else "MAIN_CONTENT_SLOT"
            replaceable_objs = [largest]

        _assign_unique_slot_names(replaceable_objs)
        role_summary = classify_slide_roles(slide, annotated, total_slides)
        constraints_info = extract_pairwise_alignments_and_overlaps(replaceable_objs)

        slot_contracts = [
            _build_slot_contract(
                o,
                canvas_w_pt,
                canvas_h_pt,
                color_roles["semantic_roles"],
                {"heading": heading_font, "body": body_font},
            )
            for o in replaceable_objs
        ]
        sig = _spatial_signature(role_summary["design_role"], slot_contracts)
        per_slide_bundles.append({
            "slide_number": s_num,
            "slide": slide,
            "role_summary": role_summary,
            "slot_contracts": slot_contracts,
            "constraints": constraints_info,
            "signature": sig,
        })

    # Cross-slide Pattern Discovery & Deduplication (4.7)
    pattern_groups: dict[str, list[dict[str, Any]]] = {}
    for bundle in per_slide_bundles:
        sig = bundle["signature"]
        pattern_groups.setdefault(sig, []).append(bundle)

    layout_patterns: list[dict[str, Any]] = []
    slide_to_layout_id: dict[int, str] = {}

    for idx, (sig, members) in enumerate(pattern_groups.items(), start=1):
        layout_id = f"LAYOUT{idx:03d}"
        rep = members[0]
        source_slides = [m["slide_number"] for m in members]
        for s_num in source_slides:
            slide_to_layout_id[s_num] = layout_id

        design_role = rep["role_summary"]["design_role"]
        slots = rep["slot_contracts"]
        pattern_key, semantic_role, gen_info = _generalized_pattern_identity(design_role, slots)

        norm_0_100 = [
            {
                "slot": s["slot_name"],
                "x": round(s["rect_percent"]["x"], 4),
                "y": round(s["rect_percent"]["y"], 4),
                "w": round(s["rect_percent"]["w"], 4),
                "h": round(s["rect_percent"]["h"], 4),
            }
            for s in slots
        ]
        slot_ast_0_1 = [
            {
                "name": s["slot_name"],
                "type": s["allowed_content_types"][0],
                "rect": s["rect_ratio"],
                "max_lines": s["capacity_estimate"]["max_lines"],
                "keep_aspect": bool(s["capacity_estimate"].get("keep_aspect", False)),
            }
            for s in slots
        ]

        safe = grid_info["safe_area_percent"]
        within_safe = all(
            s["rect_percent"]["x"] >= safe["x"] - 2.5
            and s["rect_percent"]["y"] >= safe["y"] - 3.5
            and (s["rect_percent"]["x"] + s["rect_percent"]["w"]) <= (safe["x"] + safe["w"] + 3.5)
            for s in slots
        )

        variants = [
            {
                "variant_id": f"{layout_id}_COMPACT",
                "description": "Reduce body font by 1.5pt and increase figure slot width by 5% when axis annotations are dense.",
                "trigger": "High-density spectrum or multi-curve plot",
            },
            {
                "variant_id": f"{layout_id}_WITH_BOTTOM_CHIP_BAR",
                "description": "Reserve bottom 8% band (y=84..92%) for experimental condition chips (solvent, pH, temperature, LOD).",
                "trigger": "Empirical claim requiring explicit condition disclosure",
            },
        ]

        layout_patterns.append({
            "layout_id": layout_id,
            "pattern_key": pattern_key,
            "semantic_role": semantic_role,
            "design_role": design_role,
            "source_slides": source_slides,
            "cluster_signature": sig,
            "geometry": {
                "normalized_0_100": norm_0_100,
                "slot_ast_0_1": slot_ast_0_1,
            },
            "slots": slots,
            "constraints": {
                "alignments": rep["constraints"]["alignments"],
                "non_overlapping_pairs": rep["constraints"]["non_overlapping_pairs"],
                "overlaps_detected": rep["constraints"]["overlaps"],
                "reading_order": rep["constraints"]["reading_order"],
                "within_safe_area": within_safe,
            },
            "variants": variants,
            "generalization": gen_info,
        })

    for bundle in per_slide_bundles:
        r_sum = dict(bundle["role_summary"])
        r_sum["layout_id"] = slide_to_layout_id[bundle["slide_number"]]
        slide_roles_out.append(r_sum)

    # Scope separation: global rules vs role-scoped vs slide-specific (4.5)
    bg_colors = [s["background"].get("color_hex") for s in slides if s["background"].get("color_hex")]
    dominant_bg = max(set(bg_colors), key=bg_colors.count) if bg_colors else color_roles["semantic_roles"]["paper"]
    slide_specific_exceptions = [
        {"slide_number": s["slide_number"], "exception": f"background={s['background'].get('color_hex')}"}
        for s in slides
        if s["background"].get("color_hex") and s["background"].get("color_hex") != dominant_bg
    ]

    # Collect typography groups across patterns
    typo_groups = [
        {"group_id": "TYPO_GROUP_SLIDE_TITLE", "role": "Main Action Title", "target_pt": 26.0, "min_pt": 20.0, "scope": "global"},
        {"group_id": "TYPO_GROUP_SUBTITLE", "role": "Subtitle / Section Tag", "target_pt": 16.0, "min_pt": 13.0, "scope": "role_scoped"},
        {"group_id": "TYPO_GROUP_BODY_PRIMARY", "role": "Primary Explanation / Claims", "target_pt": 15.0, "min_pt": 13.0, "scope": "global"},
        {"group_id": "TYPO_GROUP_CONCLUSION_CALLOUT", "role": "Bottom Takeaway Banner", "target_pt": 14.0, "min_pt": 12.5, "scope": "role_scoped"},
        {"group_id": "TYPO_GROUP_CAPTION", "role": "Figure Caption & Conditions", "target_pt": 11.5, "min_pt": 10.0, "scope": "global"},
    ]

    observed_sizes = [x["size_pt"] for x in resolved_deck["observed_frequencies"]["sizes_pt_by_chars"]]
    title_sz = max(observed_sizes) if observed_sizes else 26.0
    body_sz = min(observed_sizes) if len(observed_sizes) > 1 else 15.0

    return {
        "safe_area_percent": grid_info["safe_area_percent"],
        "design_tokens": {
            "colors": {
                "theme_slots": resolved_deck["effective_theme_colors"],
                "semantic_roles": color_roles["semantic_roles"],
                "observed_palette": resolved_deck["observed_frequencies"]["colors_by_usage"][:10],
                "office_theme_mapping": color_roles["office_theme_mapping"],
                "broken_theme_repaired": resolved_deck["broken_theme_repaired"],
            },
            "typography": {
                "major_font": {"family": heading_font, "declared": major_f},
                "minor_font": {"family": body_font, "declared": minor_f},
                "master_tx_styles": master_tx_summary,
                "observed_scale_pt": {
                    "title_max_pt": round(title_sz, 1),
                    "body_primary_pt": round(body_sz, 1),
                    "distribution": resolved_deck["observed_frequencies"]["sizes_pt_by_chars"][:8],
                },
                "typography_groups": typo_groups,
            },
            "spacing": {
                "grid": grid_info["grid"],
                "margins_percent": grid_info["margins_percent"],
                "spacing_scale_pt": [4.0, 8.0, 12.0, 16.0, 24.0, 32.0],
            },
            "visual_styles": {
                "shape_language": "Clean academic rectangular containers with subtle rounded callouts and hairline dividers",
                "chart_styling": "High-contrast data series using PrimaryAccent/SecondaryAccent; unobstructed axis labels >=11pt",
                "image_treatment": "Aspect-ratio locked scientific plots/microscopy with adjacent condition/caption bar",
                "caption_styling": f"{body_font} 10.5-12pt muted ({color_roles['semantic_roles']['muted']}) with explicit condition/source tags",
                "header_footer_rules": f"Top title band within y={grid_info['margins_percent']['top']}%..20%; bottom footer below y=90%",
            },
        },
        "slide_roles": slide_roles_out,
        "layout_patterns": layout_patterns,
        "reusable_assets": {
            "fixed_chrome_elements": fixed_chrome_candidates[:30],
            "master_backgrounds": [
                {"slide_number": s["slide_number"], **s["background"]}
                for s in slides
            ],
            "media_assets": resolved_deck["media_assets"],
            "scope_summary": {
                "global_rules": [
                    "16:9 or native canvas aspect ratio with unified 12-column grid",
                    f"Primary ink {color_roles['semantic_roles']['ink']} on paper {color_roles['semantic_roles']['paper']} with accent {color_roles['semantic_roles']['accent']}",
                    f"Heading font {heading_font} and body font {body_font}",
                ],
                "role_scoped_rules": [
                    f"{p['layout_id']} ({p['pattern_key']}): applicable to {', '.join(p['generalization']['applicable_content_roles'])}"
                    for p in layout_patterns
                ],
                "slide_specific_exceptions": slide_specific_exceptions,
            },
        },
        "conflicts": conflicts_out,
    }
