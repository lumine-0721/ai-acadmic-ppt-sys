#!/usr/bin/env python3
"""Rebuild the committed PPTX fixtures under fixtures/ with python-pptx.

Usage (from the repo root):
    python tools/build_pptx_fixtures.py

Outputs
  fixtures/aprd/source-api-demo.pptx          3 slides: inherited title geometry, direct + theme colours, group + table
  fixtures/aprd/hostile-template.pptx         template with layouts and ZERO slides (APRD must synthesise pages from layouts)
  fixtures/apc/out/instrument-gain-v002.pptx  2-slide legacy deck on slideLayout6 (Title Only)

The decks are tiny synthetic stand-ins for files that used to live outside the repo
(C:/Users/86139/...). They contain no real research data.
"""
from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.dml import MSO_THEME_COLOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Emu, Pt

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "fixtures"
TITLE_ONLY = 5  # slideLayout6.xml in the default python-pptx template


def _fix_core_props(prs: Presentation, title: str) -> None:
    props = prs.core_properties
    props.title = title
    props.author = "fixture-builder"
    props.last_modified_by = "fixture-builder"
    import datetime as _dt

    stamp = _dt.datetime(2026, 11, 13, 0, 0, 0)
    props.created = stamp
    props.modified = stamp


def build_demo(path: Path) -> None:
    prs = Presentation()
    layout = prs.slide_layouts[TITLE_ONLY]
    # Title placeholder geometry lives on the layout, so slide 1 inherits it.
    title_ph = next(p for p in layout.placeholders if p.placeholder_format.type is not None and p.placeholder_format.idx == 0)
    title_ph.left, title_ph.top, title_ph.width, title_ph.height = Emu(548640), Emu(365760), Emu(8046720), Emu(914400)

    s1 = prs.slides.add_slide(layout)
    s1.shapes.title.text = "Source API Demo"

    s2 = prs.slides.add_slide(layout)
    s2.shapes.title.text = "Colour resolution"
    rect = s2.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(914400), Emu(1600200), Emu(3200400), Emu(1371600))
    rect.fill.solid()
    rect.fill.fore_color.rgb = RGBColor(0xDC, 0xE6, 0xF1)
    box = s2.shapes.add_textbox(Emu(914400), Emu(3429000), Emu(5486400), Emu(914400))
    run = box.text_frame.paragraphs[0].add_run()
    run.text = "Theme colour with brightness 0.25"
    run.font.size = Pt(18)
    run.font.color.theme_color = MSO_THEME_COLOR.ACCENT_1
    run.font.color.brightness = 0.25

    s3 = prs.slides.add_slide(layout)
    s3.shapes.title.text = "Group and table"
    group = s3.shapes.add_group_shape()
    a = group.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(914400), Emu(1600200), Emu(1371600), Emu(685800))
    b = group.shapes.add_shape(MSO_SHAPE.OVAL, Emu(2514600), Emu(1600200), Emu(685800), Emu(685800))
    for shape in (a, b):
        shape.fill.solid()
        shape.fill.fore_color.rgb = RGBColor(0x1F, 0x4E, 0x79)
    s3.shapes.add_table(2, 2, Emu(914400), Emu(3200400), Emu(4572000), Emu(914400))
    _fix_core_props(prs, "source-api-demo")
    prs.save(path)


def build_hostile(path: Path) -> None:
    prs = Presentation()  # default template: 11 layouts, no slides
    assert len(prs.slides) == 0
    _fix_core_props(prs, "hostile-template")
    prs.save(path)


def build_legacy(path: Path) -> None:
    prs = Presentation()
    layout = prs.slide_layouts[TITLE_ONLY]
    s1 = prs.slides.add_slide(layout)
    s1.shapes.title.text = "仪器增益标定：问题与目标"
    s2 = prs.slides.add_slide(layout)
    s2.shapes.title.text = "增益线性度在 1-100 倍范围内保持稳定"
    _fix_core_props(prs, "instrument-gain-v002")
    prs.save(path)


def main() -> None:
    for sub in ("aprd", "apc/out"):
        (FIXTURES / sub).mkdir(parents=True, exist_ok=True)
    build_demo(FIXTURES / "aprd" / "source-api-demo.pptx")
    build_hostile(FIXTURES / "aprd" / "hostile-template.pptx")
    build_legacy(FIXTURES / "apc" / "out" / "instrument-gain-v002.pptx")
    print("wrote PPTX fixtures under", FIXTURES)


if __name__ == "__main__":
    main()
