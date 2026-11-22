from __future__ import annotations

import re
from pathlib import Path

from PIL import Image

from pptv01.contracts import (
    CANVAS_W_IN,
    AssetRegistry,
    PageDesign,
    PageDesignEntry,
    PageRole,
    PointGroup,
    SlideAssetMapping,
    SlidePage,
    SlidePlan,
    UserInputRequests,
    load_contract,
)
from pptv01.f_layout import validate_page_geometry
from pptv01.f_layout.layouts import build_layout_elements
from pptv01.f_layout.preview import render_schematic_preview
from pptv01.f_layout.stage import _build_page_items, _concise_budget, _condense_points_for_budget
from pptv01.g_build import compile_deck_ops
from pptv01.h_qa.occupancy import score_occupancy
from pptv01.theme import ACADEMIC_DEFAULT_THEME, DEFAULT_THEME_ID, contrast_ratio, get_theme, list_themes

V01_ROOT = Path(__file__).resolve().parents[1]
R6_T12_DIR = V01_ROOT / "tests" / "fixtures" / "r6_theme_snapshot" / "T12"
_HEX_RE = re.compile(r'"#[0-9A-Fa-f]{6}"')


def _shared_content_page() -> SlidePage:
    return SlidePage(
        page_id="S002",
        title="双路径演化机制",
        page_role=PageRole.content,
        headline="材料蓄热差异与街区风道受阻共同决定了夜间热岛强度分布。",
        goal="对比两条主机制",
        key_points=[
            "低反照率沥青与混凝土在日间持续蓄积短波辐射热量",
            "绿色植被缺失降低日间潜热蒸散耗散能力",
            "高密度街谷阻挡夜间向冷天空的长波辐射冷却",
            "空调冷凝排热在峰荷时段直接抬高近地层气温",
            "机动交通排放与尾气废热维持夜间热负荷底噪",
            "连续迎风向建筑立面减弱边界层水平通风扩散",
        ],
        groups=[
            PointGroup(
                heading="下垫面能量失衡",
                points=[
                    "低反照率沥青与混凝土在日间持续蓄积短波辐射热量",
                    "绿色植被缺失降低日间潜热蒸散耗散能力",
                    "高密度街谷阻挡夜间向冷天空的长波辐射冷却",
                ],
            ),
            PointGroup(
                heading="人为热源与通风受阻",
                points=[
                    "空调冷凝排热在峰荷时段直接抬高近地层气温",
                    "机动交通排放与尾气废热维持夜间热负荷底噪",
                    "连续迎风向建筑立面减弱边界层水平通风扩散",
                ],
            ),
        ],
        content_summary="分两组比较物理机制。",
        speaker_notes="我们把城区夜间偏热的机制分成两组来看。左侧展示材料蓄热与蒸腾不足，右侧则给出人为热源和建筑阻风的作用。",
        visual_intent="双栏机制",
    )


def _grayscale_structure_clusters(img_paths: list[Path], threshold: float = 6.0) -> int:
    """Cluster grayscale structural edge/zone vectors (invariant to pure global tint shifts)."""
    vecs: list[list[float]] = []
    for p in img_paths:
        with Image.open(p) as im:
            g = im.convert("L").resize((48, 27), Image.Resampling.BILINEAR)
            pixels = list(g.get_flattened_data()) if hasattr(g, "get_flattened_data") else list(g.getdata())
            med = sorted(pixels)[len(pixels) // 2]
            centered = [abs(px - med) for px in pixels]
            edges: list[float] = []
            w, h = 48, 27
            for y in range(h):
                for x in range(w):
                    cur = pixels[y * w + x]
                    dx = abs(cur - pixels[y * w + min(w - 1, x + 1)])
                    dy = abs(cur - pixels[min(h - 1, y + 1) * w + x])
                    edges.append(min(255.0, float(dx + dy) + 0.5 * centered[y * w + x]))
            vecs.append(edges)

    reps: list[list[float]] = []
    for v in vecs:
        matched = False
        for rep in reps:
            l1 = sum(abs(a - b) for a, b in zip(v, rep)) / len(v)
            if l1 < threshold:
                matched = True
                break
        if not matched:
            reps.append(v)
    return len(reps)


def test_r8_4_skeleton_tokens_differ_across_themes_and_academic_default_invariant():
    ids = list_themes()
    assert len(ids) == 8
    def_tok = get_theme(DEFAULT_THEME_ID)
    assert def_tok == ACADEMIC_DEFAULT_THEME
    base_triple = (def_tok.bg_motif, def_tok.title_style, def_tok.accent_position)

    for tid in ids:
        tok = get_theme(tid)
        assert tok.bg_motif in {"none", "top_band", "left_rail", "split_zone", "fine_grid", "corner_bracket"}
        assert tok.title_style in {"left_rule", "filled_header", "numbered_title", "centered_rule"}
        assert tok.accent_position in {"under_title", "left_bar", "corner_badge", "none"}
        assert tok.card_style in {"outlined", "filled_soft", "accent_top", "borderless"}
        if tid != DEFAULT_THEME_ID:
            cur_triple = (tok.bg_motif, tok.title_style, tok.accent_position)
            diffs = sum(a != b for a, b in zip(cur_triple, base_triple))
            assert diffs >= 2, f"{tid} must differ from academic-default on >=2 of 3 skeleton traits, got {cur_triple}"


def test_r8_4_zero_hardcoded_hex_and_wcag_contrast_on_skeleton_surfaces():
    for sub in ("f_layout", "g_build"):
        for py in (V01_ROOT / "src" / "pptv01" / sub).rglob("*.py"):
            hits = _HEX_RE.findall(py.read_text(encoding="utf-8"))
            assert hits == [], f"hardcoded hex literals {hits} found in {py}"

    for tid in list_themes():
        tok = get_theme(tid)
        for label, fg, bg in (
            ("primary_text on primary", tok.primary_text, tok.primary),
            ("background_text on surface_warm", tok.background_text, tok.surface_warm),
            ("primary_deep on surface_warm", tok.primary_deep, tok.surface_warm),
            ("primary_deep on background", tok.primary_deep, tok.background),
            ("text_secondary on surface_warm", tok.text_secondary, tok.surface_warm),
        ):
            cr = contrast_ratio(fg, bg)
            assert cr >= 4.5, f"{tid} {label} ({fg} on {bg}) contrast {cr:.2f} < 4.5"


def test_r8_4_grayscale_skeletons_yield_at_least_4_distinct_structures(tmp_path: Path):
    page = _shared_content_page()
    preview_paths: list[Path] = []
    signatures: list[tuple] = []

    for tid in list_themes():
        tok = get_theme(tid)
        els = build_layout_elements("two-content", page, page.key_points, [], [], [], [], theme=tok)
        entry = PageDesignEntry(
            page_id="S002",
            layout_id="two-content",
            layout_name="two-content",
            elements=els,
            canvas_width_in=CANVAS_W_IN,
        )
        geom_errs = validate_page_geometry(entry)
        assert not geom_errs, f"{tid} geometry errors: {geom_errs}"
        occ = score_occupancy(entry)
        assert occ.passed, f"{tid} occupancy failed: {occ}"

        if tok.has_tinted_canvas or tok.theme_id != DEFAULT_THEME_ID:
            # Verify bg_canvas precedes bg_motif_* in z_index=0 sort order
            ordered = sorted(els, key=lambda e: (e.z_index, 0 if e.element_id == "bg_canvas" else 1, e.element_id))
            if any(e.element_id == "bg_canvas" for e in ordered):
                assert ordered[0].element_id == "bg_canvas"

        sig = tuple(
            sorted(
                (
                    el.element_id,
                    round(el.x_in, 2),
                    round(el.y_in, 2),
                    round(el.w_in, 2),
                    round(el.h_in, 2),
                    el.align,
                    el.line_hex is not None,
                    el.fill_hex != tok.background if el.fill_hex else False,
                )
                for el in els
            )
        )
        signatures.append(sig)
        out_png = render_schematic_preview(entry, tmp_path, f"previews/{tid}.png")
        assert out_png.is_file()
        preview_paths.append(out_png)

    assert len(set(signatures)) >= 6
    n_clusters = _grayscale_structure_clusters(preview_paths, threshold=6.0)
    assert n_clusters >= 4, f"expected >= 4 grayscale skeleton clusters across 8 themes, got {n_clusters}"


def test_r8_4_compile_deck_ops_handles_all_8_theme_skeletons(tmp_path: Path):
    pp = load_contract(
        __import__("pptv01.contracts", fromlist=["ProductionPlan"]).ProductionPlan,
        R6_T12_DIR / "production_plan.json",
    )
    sp = load_contract(SlidePlan, R6_T12_DIR / "slide_plan.json")
    reg = load_contract(AssetRegistry, R6_T12_DIR / "asset_registry.json")
    uir = load_contract(UserInputRequests, R6_T12_DIR / "user_input_requests.json")
    mp = load_contract(SlideAssetMapping, R6_T12_DIR / "slide_asset_mapping.json")

    wd = tmp_path / "run"
    wd.mkdir()
    (wd / "assets").mkdir()
    for a in reg.assets:
        if a.file_path:
            dst = wd / a.file_path
            dst.parent.mkdir(parents=True, exist_ok=True)
            src = R6_T12_DIR / a.file_path
            dst.write_bytes(src.read_bytes() if src.is_file() else b"stub")

    for tid in list_themes():
        tok = get_theme(tid)
        design_pages = []
        reg_by_id = {a.asset_id: a for a in reg.assets}
        uir_by_id = {r.request_id: r for r in uir.requests}
        concise_budget = _concise_budget(pp)
        for sp_page in sp.pages:
            rv, ph, cits = _build_page_items(sp_page, mp, reg_by_id, uir_by_id, wd)
            pts = _condense_points_for_budget(sp_page.key_points, concise_budget)[0] if concise_budget else list(sp_page.key_points)
            lid = "single-column" if len(pts) >= 2 and not rv else ("image-right" if rv else "section")
            els = build_layout_elements(lid, sp_page, pts, rv, ph, cits, [], concise=bool(concise_budget), theme=tok)
            entry = PageDesignEntry(page_id=sp_page.page_id, layout_id=lid, layout_name=lid, elements=els)
            assert validate_page_geometry(entry) == []
            design_pages.append(entry)
        pd = PageDesign(run_id=pp.run_id, theme_id=tid, theme_method="explicit", theme_rationale="test", pages=design_pages)
        ops = compile_deck_ops(sp, pd, reg, uir, wd, write_file=False)
        assert ops.ops
