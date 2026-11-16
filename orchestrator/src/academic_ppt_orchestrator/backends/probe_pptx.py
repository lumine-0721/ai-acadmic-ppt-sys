"""Editable PPTX probe. Uses python-pptx, the same library APRD already depends on.

This is not the production design backend and does not apply APRD geometry.
"""

from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.util import Inches, Pt


def write_probe(path: Path, ir: dict) -> None:
    slides = ir.get("slide_specifications") or [{"slide_id": "S000", "on_slide_text": ""}]
    path.parent.mkdir(parents=True, exist_ok=True)
    presentation = Presentation()
    presentation.slide_width = Inches(13.333)
    presentation.slide_height = Inches(7.5)
    blank = presentation.slide_layouts[6]
    project = str((ir.get("deck_metadata") or {}).get("project_id") or "")
    for spec in slides:
        slide = presentation.slides.add_slide(blank)
        title = slide.shapes.add_textbox(Inches(0.6), Inches(0.4), Inches(12), Inches(0.8))
        title_frame = title.text_frame
        title_frame.text = str(spec.get("slide_id") or "")
        title_frame.paragraphs[0].font.size = Pt(28)
        body = slide.shapes.add_textbox(Inches(0.6), Inches(1.5), Inches(12), Inches(4.5))
        body_frame = body.text_frame
        body_frame.word_wrap = True
        text = str(spec.get("on_slide_text") or spec.get("summary") or "")
        notes = spec.get("speaker_notes")
        if notes:
            text = text + chr(10) + chr(10) + "Notes: " + str(notes)
        body_frame.text = text
        body_frame.paragraphs[0].font.size = Pt(18)
        if project:
            slide.notes_slide.notes_text_frame.text = "project_id=" + project + " probe=python-pptx production_backend=false"
    presentation.save(str(path))

