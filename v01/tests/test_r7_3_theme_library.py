"""R7-3 tests: 8-theme library, WCAG contrast >= 4.5, topic-driven selection, title fonts, and doctor font probe."""

from __future__ import annotations

import hashlib
import io
import shutil
from pathlib import Path

import pytest
from PIL import Image

from pptv01.contracts import (
    AssetRegistry,
    ElementKind,
    ProductionPlan,
    SlideAssetMapping,
    SlidePlan,
    UserInputRequests,
    load_contract,
    write_contract,
)
from pptv01.doctor import _probe_theme_fonts
from pptv01.f_layout.stage import run_stage_f
from pptv01.g_build import compile_deck_ops
from pptv01.grading.gates import check_gates
from pptv01.h_qa.occupancy import check_deck_occupancy
from pptv01.theme import (
    ACADEMIC_DEFAULT_THEME,
    DEFAULT_THEME_ID,
    THEMES,
    contrast_ratio,
    get_theme,
    list_themes,
    load_selection_index,
    select_theme,
)

V01_ROOT = Path(__file__).resolve().parents[1]
R6_SNAPSHOT_ROOT = V01_ROOT / "tests" / "fixtures" / "r6_theme_snapshot"


def test_r7_3_theme_library_has_8_themes_with_metadata_and_wcag_contrast():
    ids = list_themes()
    assert len(ids) == 8, f"expected 8 themes, got {ids}"
    assert DEFAULT_THEME_ID in ids
    assert get_theme(DEFAULT_THEME_ID) == ACADEMIC_DEFAULT_THEME

    index = load_selection_index()
    presets = index.get("presets") or {}
    assert set(presets) >= set(ids)

    required_meta = ("mood", "tone", "formality", "density", "scheme", "best_for", "avoid_for")
    for tid in ids:
        meta = presets[tid]
        for field in required_meta:
            assert field in meta and meta[field], f"{tid} missing selection-index field {field}"

        tok = get_theme(tid)
        pairs = [
            ("background_text on background", tok.background_text, tok.background),
            ("text_secondary on background", tok.text_secondary, tok.background),
            ("text_muted on background", tok.text_muted, tok.background),
            ("primary_deep on surface_light", tok.primary_deep, tok.surface_light),
            ("background_text on surface_light", tok.background_text, tok.surface_light),
            ("background_text on surface_fact", tok.background_text, tok.surface_fact),
            ("caption_on_light on background", tok.caption_on_light, tok.background),
            ("citation_on_light on background", tok.citation_on_light, tok.background),
            ("primary_text on primary_deep", tok.primary_text, tok.primary_deep),
            ("text_on_dark_soft on primary_deep", tok.text_on_dark_soft, tok.primary_deep),
            ("caption_on_dark on primary_deep", tok.caption_on_dark, tok.primary_deep),
            ("citation_on_dark on primary", tok.citation_on_dark, tok.primary),
            ("placeholder_text on placeholder_fill", tok.placeholder_text, tok.placeholder_fill),
            ("dark_placeholder_text on dark_placeholder_fill", tok.dark_placeholder_text, tok.dark_placeholder_fill),
        ]
        for label, fg, bg in pairs:
            cr = contrast_ratio(fg, bg)
            assert cr >= 4.5, f"{tid} {label} ({fg} on {bg}) contrast {cr:.2f} < 4.5"


def test_r7_3_smoke_four_tasks_select_at_least_three_distinct_themes_with_rationale(tmp_path: Path):
    picked: dict[str, str] = {}
    for tid in ("T36", "T12", "T26", "T02"):
        fx_dir = R6_SNAPSHOT_ROOT / tid
        run_d = tmp_path / tid
        run_d.mkdir(parents=True, exist_ok=True)
        for fn in ("production_plan.json", "slide_plan.json", "asset_registry.json", "user_input_requests.json", "slide_asset_mapping.json"):
            shutil.copy2(fx_dir / fn, run_d / fn)

        pp = load_contract(ProductionPlan, run_d / "production_plan.json")
        sp = load_contract(SlidePlan, run_d / "slide_plan.json")
        reg = load_contract(AssetRegistry, run_d / "asset_registry.json")
        uir = load_contract(UserInputRequests, run_d / "user_input_requests.json")
        mp = load_contract(SlideAssetMapping, run_d / "slide_asset_mapping.json")

        sel = select_theme(pp, sp)
        assert sel.theme_id in THEMES
        assert sel.rationale.strip()
        picked[tid] = sel.theme_id

        (run_d / "assets").mkdir(exist_ok=True)
        updated_assets = []
        for idx_a, a in enumerate(reg.assets):
            if not a.file_path:
                updated_assets.append(a)
                continue
            dst = run_d / a.file_path
            dst.parent.mkdir(parents=True, exist_ok=True)
            src = fx_dir / a.file_path
            if src.is_file():
                shutil.copy2(src, dst)
                updated_assets.append(a)
            else:
                col = ((45 + idx_a * 31) % 255, (85 + idx_a * 37) % 255, (135 + idx_a * 23) % 255)
                buf = io.BytesIO()
                img_obj = Image.new("RGB", (max(8, min(a.width or 800, 128)), max(8, min(a.height or 600, 128))), col)
                img_obj.save(buf, "JPEG" if dst.suffix.lower() in (".jpg", ".jpeg") else "PNG")
                data = buf.getvalue()
                dst.write_bytes(data)
                updated_assets.append(a.model_copy(update={"sha256": hashlib.sha256(data).hexdigest()}))
        reg = reg.model_copy(update={"assets": updated_assets})
        write_contract(reg, run_d / "asset_registry.json")

        # End-to-end Stage F + compile_deck_ops carries selected theme_id + rationale + bg_canvas & title_font
        pd = run_stage_f(pp, sp, reg, uir, mp, run_d)
        assert pd.theme_id == sel.theme_id
        assert pd.theme_rationale == sel.rationale
        tok = get_theme(pd.theme_id)
        for pg in pd.pages:
            if tok.has_tinted_canvas:
                assert any(el.element_id == "bg_canvas" for el in pg.elements), f"{tid}/{pg.page_id} missing bg_canvas"
            for el in pg.elements:
                if el.kind is ElementKind.title:
                    assert el.font == tok.title_font

        occ = check_deck_occupancy(pd)
        assert occ.passed, f"{tid} ({pd.theme_id}) fill_rate failed: {occ.detail}"
        ops = compile_deck_ops(sp, pd, reg, uir, run_d)
        # bg_canvas shape must precede other z=0 elements on every tinted slide
        if tok.has_tinted_canvas:
            by_slide: dict[str, list[str]] = {}
            for op in ops.ops:
                if op.kind.value == "add_shape":
                    by_slide.setdefault(op.page_id, []).append(op.params.get("shape_name", ""))
            for pid, shapes in by_slide.items():
                if "bg_canvas" in shapes and len(shapes) > 1:
                    assert shapes[0] == "bg_canvas", f"{tid}/{pid} bg_canvas not drawn first: {shapes[:4]}"

        rep = check_gates(run_d)
        by_gate = {r.gate: r for r in rep.results}
        assert by_gate["fill_rate"].status == "pass", by_gate["fill_rate"].detail

    assert len(set(picked.values())) >= 3, f"expected >= 3 distinct themes across smoke4, got {picked}"


def test_r7_3_doctor_theme_font_probe_detects_missing_and_reports_fallback():
    all_present = {"Microsoft YaHei", "DengXian", "SimHei", "SimSun"}
    ok_res = _probe_theme_fonts(installed_fonts=all_present)
    assert ok_res.ok is True
    assert "all 8 theme fonts installed" in ok_res.detail

    missing_res = _probe_theme_fonts(installed_fonts={"Microsoft YaHei"})
    assert missing_res.ok is False
    assert "missing theme font(s):" in missing_res.detail
    assert "fallback recommendation: Microsoft YaHei" in missing_res.detail


def test_r7_3_llm_selector_falls_back_cleanly_on_invalid_output():
    class _BadLLM:
        def complete(self, prompt: str) -> str:
            return '{"theme_id": "nonexistent-theme", "rationale": "x"}'

    pp = load_contract(ProductionPlan, R6_SNAPSHOT_ROOT / "T12" / "production_plan.json")
    sp = load_contract(SlidePlan, R6_SNAPSHOT_ROOT / "T12" / "slide_plan.json")
    sel = select_theme(pp, sp, llm=_BadLLM())
    assert sel.theme_id == "warm-espresso"
    assert sel.method == "rule"
