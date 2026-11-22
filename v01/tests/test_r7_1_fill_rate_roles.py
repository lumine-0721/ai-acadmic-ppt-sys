"""R7-1 tests: role-based fill_rate exemption (never layout_id bypass) and hard-fail gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from pptv01.contracts import DesignElement, ElementKind, PageDesign, PageDesignEntry, write_contract
from pptv01.grading.gates import check_gates
from pptv01.h_qa.occupancy import VOID_MAX_IN, check_deck_occupancy, score_occupancy

V01_ROOT = Path(__file__).resolve().parents[1]
SMOKE6_DESIGN_DIR = V01_ROOT / "tests" / "fixtures" / "smoke6_page_design"

EXPECTED_SMOKE6_FLAGS: dict[str, list[str]] = {
    "T36": ["S003", "S005"],
    "T12": ["S002", "S005", "S007"],
    # T26 S001 is a bullet-bearing cover with 2.24in void; R8-3 enforces content occupancy on bullet-bearing covers
    "T26": ["S001"],
}


@pytest.mark.parametrize("tid", ["T36", "T12", "T26"])
def test_r7_1_smoke6_fixtures_flag_exact_underfilled_sections_as_fail(tid: str, tmp_path: Path):
    """On Smoke 6 fixtures, fill_rate flags T36 S003/S005 and T12 S002/S005/S007 as hard fail; T26 passes."""
    assert VOID_MAX_IN == 1.60
    design = PageDesign.model_validate_json((SMOKE6_DESIGN_DIR / f"{tid}.json").read_text(encoding="utf-8"))
    occ = check_deck_occupancy(design)

    flagged_ids = [r.page_id for r in occ.flagged_pages]
    assert flagged_ids == EXPECTED_SMOKE6_FLAGS[tid], f"{tid}: got {flagged_ids}, detail={occ.detail}"

    if tid == "T26":
        s4 = next(r for r in occ.pages if r.page_id == "S004")
        assert s4.layout_id == "section"
        assert s4.exempt is False
        assert s4.page_role == "content"
        assert s4.passed is True

    write_contract(design, tmp_path / "page_design.json")
    rep = check_gates(tmp_path)
    fill_gate = next(r for r in rep.results if r.gate == "fill_rate")
    if EXPECTED_SMOKE6_FLAGS[tid]:
        assert fill_gate.status == "fail"
        assert rep.overall == "fail"
        for pid in EXPECTED_SMOKE6_FLAGS[tid]:
            assert pid in fill_gate.detail
    else:
        assert fill_gate.status == "pass"


def test_r7_1_role_based_exemption_distinguishes_divider_from_content_bullets():
    """Section with 3 bullets is NOT exempt (fails void); pure divider section and single-line quote ARE exempt."""
    # 1. Section with 3 upper bullets -> content page -> NOT exempt -> fails fill_rate
    content_section = PageDesignEntry(
        page_id="S003",
        layout_id="section",
        layout_name="section",
        elements=[
            DesignElement(
                element_id="bg_left_band",
                kind=ElementKind.shape,
                x_in=0.0,
                y_in=0.0,
                w_in=4.35,
                h_in=7.5,
                z_index=0,
                fill_hex="#0F2544",
                line_hex="#0F2544",
            ),
            DesignElement(
                element_id="title",
                kind=ElementKind.title,
                x_in=0.65,
                y_in=1.80,
                w_in=3.05,
                h_in=1.20,
                z_index=2,
                text="核心机制",
                font="Microsoft YaHei",
                font_size_pt=28.0,
            ),
            DesignElement(
                element_id="bullets",
                kind=ElementKind.bullet,
                x_in=4.95,
                y_in=1.50,
                w_in=7.10,
                h_in=1.50,
                z_index=2,
                text="• 第一条机制说明\n• 第二条机制说明\n• 第三条机制说明",
                font="Microsoft YaHei",
                font_size_pt=18.0,
            ),
        ],
    )
    r_content = score_occupancy(content_section)
    assert r_content.exempt is False
    assert r_content.page_role == "content"
    assert r_content.passed is False
    assert r_content.max_void_in >= VOID_MAX_IN

    # 2. Pure section divider (title + optional single-line subtitle, NO bullets) -> exempt
    divider_section = PageDesignEntry(
        page_id="S002",
        layout_id="section",
        layout_name="section",
        elements=[
            DesignElement(
                element_id="title",
                kind=ElementKind.title,
                x_in=1.20,
                y_in=2.60,
                w_in=10.50,
                h_in=1.10,
                z_index=2,
                text="第二部分：方法与框架",
                font="Microsoft YaHei",
                font_size_pt=36.0,
            ),
            DesignElement(
                element_id="subtitle",
                kind=ElementKind.body,
                x_in=1.20,
                y_in=3.85,
                w_in=10.50,
                h_in=0.50,
                z_index=2,
                text="聚焦核心模型与验证思路",
                font="Microsoft YaHei",
                font_size_pt=18.0,
            ),
        ],
    )
    r_div = score_occupancy(divider_section)
    assert r_div.exempt is True
    assert r_div.page_role == "section_divider"
    assert "section_divider" in r_div.exempt_reason
    assert r_div.passed is True

    # 3. Pure quote statement (single pull quote, NO bullets) -> exempt
    pure_quote = PageDesignEntry(
        page_id="S005",
        layout_id="quote",
        layout_name="quote",
        elements=[
            DesignElement(
                element_id="title",
                kind=ElementKind.title,
                x_in=0.65,
                y_in=0.45,
                w_in=12.03,
                h_in=0.72,
                z_index=2,
                text="总结",
                font="Microsoft YaHei",
                font_size_pt=24.0,
            ),
            DesignElement(
                element_id="quote_text",
                kind=ElementKind.body,
                x_in=0.95,
                y_in=2.40,
                w_in=11.40,
                h_in=1.20,
                z_index=2,
                text="设计必须服务于问题本身，而不是为版式凑数。",
                font="Microsoft YaHei",
                font_size_pt=28.0,
            ),
        ],
    )
    r_quote = score_occupancy(pure_quote)
    assert r_quote.exempt is True
    assert r_quote.page_role == "quote_statement"
    assert "quote_statement" in r_quote.exempt_reason
    assert r_quote.passed is True
