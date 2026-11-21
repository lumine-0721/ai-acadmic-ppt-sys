"""R6-1 tests: ThemeTokens extraction, zero hardcoded hex literals, contrast helpers, and snapshot parity."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from pptv01.contracts import (
    AssetRegistry,
    PageDesign,
    ProductionPlan,
    SCHEMA_VERSION,
    SlideAssetMapping,
    SlidePlan,
    UserInputRequests,
    load_contract,
)
from pptv01.f_layout.stage import _build_page_items, _concise_budget, _condense_points_for_budget
from pptv01.f_layout.layouts import build_layout_elements
from pptv01.g_build import compile_deck_ops
from pptv01.theme import (
    ACADEMIC_DEFAULT_THEME,
    _complete_graph_colors,
    _relative_luminance,
    _repair_text_color,
    _surface_variant,
    contrast_ratio,
    get_theme,
)

V01_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_ROOT = V01_ROOT / "tests" / "fixtures" / "r6_theme_snapshot"

# Explicit whitelist for hex color literals in f_layout/ and g_build/ (empty after R6-1 token extraction)
HEX_LITERAL_WHITELIST: frozenset[str] = frozenset()
_HEX_RE = re.compile(r"""["']#[0-9A-Fa-f]{6}["']""")


@pytest.mark.parametrize("tid", ["T36", "T12", "T26", "T02"])
def test_r6_1_smoke5_theme_token_snapshot_identical_rendering(tid: str, tmp_path: Path):
    """Extracting academic-default into ThemeTokens preserves element colors, fonts, sizes, coords, and DeckOps."""
    fx_dir = SNAPSHOT_ROOT / tid
    assert fx_dir.is_dir(), f"missing snapshot fixture {fx_dir}"

    for fn in (
        "production_plan.json",
        "slide_plan.json",
        "asset_registry.json",
        "user_input_requests.json",
        "slide_asset_mapping.json",
    ):
        shutil.copy2(fx_dir / fn, tmp_path / fn)

    reg = load_contract(AssetRegistry, tmp_path / "asset_registry.json")
    (tmp_path / "assets").mkdir(exist_ok=True)
    for asset in reg.assets:
        if not asset.file_path:
            continue
        dst = tmp_path / asset.file_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        src_asset = fx_dir / asset.file_path
        if src_asset.is_file():
            shutil.copy2(src_asset, dst)
        else:
            dst.write_bytes(b"stub-image-for-compile-ops")

    pp = load_contract(ProductionPlan, tmp_path / "production_plan.json")
    sp = load_contract(SlidePlan, tmp_path / "slide_plan.json")
    uir = load_contract(UserInputRequests, tmp_path / "user_input_requests.json")
    mp = load_contract(SlideAssetMapping, tmp_path / "slide_asset_mapping.json")

    pd_expected = load_contract(PageDesign, fx_dir / "page_design.json")
    ops_expected = json.loads((fx_dir / "deck_ops.json").read_text(encoding="utf-8"))

    # Replay the exact pre-R6-3 snapshot layout sequence with ThemeTokens to isolate token extraction invariance.
    reg_by_id = {a.asset_id: a for a in reg.assets}
    uir_by_id = {r.request_id: r for r in uir.requests}
    concise_budget = _concise_budget(pp)
    theme = get_theme("academic-default")
    built_pages = []
    for sp_page, exp_page in zip(sp.pages, pd_expected.pages):
        rv, ph, cits = _build_page_items(sp_page, mp, reg_by_id, uir_by_id, tmp_path)
        notes = [f"planning summary (notes only): {sp_page.content_summary.strip()}"] if sp_page.content_summary.strip() else []
        pts = _condense_points_for_budget(sp_page.key_points, concise_budget)[0] if concise_budget is not None else list(sp_page.key_points)
        if concise_budget is not None and _condense_points_for_budget(sp_page.key_points, concise_budget)[1]:
            notes.append(f"condensed text to <= {concise_budget} chars per user concise-text constraint")
        els = build_layout_elements(
            exp_page.layout_id or exp_page.layout_name or "section",
            sp_page,
            pts,
            rv,
            ph,
            cits,
            notes,
            concise=(concise_budget is not None),
            theme=theme,
            allow_sparse=False,
        )
        built_pages.append(
            exp_page.model_copy(
                update={
                    "elements": els,
                    "notes": "; ".join(notes),
                }
            )
        )
    pd_new = PageDesign(schema_version=SCHEMA_VERSION, run_id=pp.run_id, theme_id=theme.theme_id, pages=built_pages)
    ops_new = compile_deck_ops(sp, pd_new, reg, uir, tmp_path)

    assert pd_new.theme_id == "academic-default"
    assert len(pd_new.pages) == len(pd_expected.pages)
    for got_p, exp_p in zip(pd_new.pages, pd_expected.pages):
        assert got_p.page_id == exp_p.page_id
        assert got_p.layout_id == exp_p.layout_id
        assert len(got_p.elements) == len(exp_p.elements)
        for got_el, exp_el in zip(got_p.elements, exp_p.elements):
            assert got_el.element_id == exp_el.element_id
            assert got_el.kind == exp_el.kind
            assert got_el.color_hex == exp_el.color_hex
            assert got_el.fill_hex == exp_el.fill_hex
            assert got_el.line_hex == exp_el.line_hex
            assert got_el.font == exp_el.font
            assert got_el.font_size_pt == exp_el.font_size_pt
            assert (got_el.x_in, got_el.y_in, got_el.w_in, got_el.h_in) == (
                exp_el.x_in,
                exp_el.y_in,
                exp_el.w_in,
                exp_el.h_in,
            )

    assert json.loads(ops_new.model_dump_json()) == ops_expected


def test_r6_1_no_hardcoded_hex_outside_theme_tokens_and_whitelist():
    """Hex color literals in f_layout/ and g_build/ must live in theme/tokens.py, never inline."""
    offenders: list[str] = []
    for sub in ("f_layout", "g_build"):
        base = V01_ROOT / "src" / "pptv01" / sub
        for py in sorted(base.rglob("*.py")):
            rel = py.relative_to(V01_ROOT / "src" / "pptv01").as_posix()
            if rel in HEX_LITERAL_WHITELIST:
                continue
            for lineno, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                if _HEX_RE.search(line):
                    offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, "hardcoded hex color literal(s) found:\n" + "\n".join(offenders)


def test_r6_1_contrast_ratio_and_repair_text_color():
    """Ported presenton color utilities compute exact WCAG contrast and repair unreadable pairs."""
    assert contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0, abs=0.05)
    assert contrast_ratio("#FFFFFF", "#FFFFFF") == pytest.approx(1.0, abs=1e-6)
    assert _relative_luminance("#000000") == pytest.approx(0.0, abs=1e-6)
    assert _relative_luminance("#FFFFFF") == pytest.approx(1.0, abs=1e-6)

    # Low contrast (< 4.5): light gray on white or dark blue on deep navy must swap color
    repaired_light = _repair_text_color("#CBD5E1", "#FFFFFF")
    assert repaired_light != "#CBD5E1"
    assert contrast_ratio(repaired_light, "#FFFFFF") >= 4.5

    repaired_dark = _repair_text_color("#1B365D", "#0F2544")
    assert repaired_dark != "#1B365D"
    assert contrast_ratio(repaired_dark, "#0F2544") >= 4.5

    # Already accessible color is preserved
    assert _repair_text_color("#0F172A", "#FFFFFF") == "#0F172A"

    # Graph color completion yields 10 valid unique #RRGGBB colors preserving academic-default theme
    series = _complete_graph_colors(("#1B365D", "#0284C7", "#0D9488", "#2563EB"), "#1B365D")
    assert len(series) == 10
    assert len(set(series)) == 10
    assert series[:4] == ("#1B365D", "#0284C7", "#0D9488", "#2563EB")
    assert len(ACADEMIC_DEFAULT_THEME.graph_colors) == 10
    assert get_theme("academic-default") is ACADEMIC_DEFAULT_THEME
    assert get_theme(None) is ACADEMIC_DEFAULT_THEME

    # Surface variant returns valid #RRGGBB distinct from input
    variant = _surface_variant("#FFFFFF", 0.08)
    assert re.fullmatch(r"#[0-9A-F]{6}", variant)
    assert variant != "#FFFFFF"
