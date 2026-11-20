"""PowerPoint COM export: deck.pptx -> render/slide_001.png... + render/deck.pdf.

Adapted from internal aprd/.../reconstructor_verifier.py:468 (read-only copy)
and ykuwai/ppt-mcp src/ppt_com/export.py (MIT, 2155ecc9050e).
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable

from pptv01.renderers.protocol import ExportResult, RenderExecutionError

from .constants import MSO_FALSE, MSO_TRUE, PP_SAVE_AS_PDF
from .session import open_powerpoint_session

_SLIDE_PNG_RE = re.compile(r"(\d+)\.png$", re.I)


def _slide_sort_key(p: Path) -> tuple[int, str]:
    m = _SLIDE_PNG_RE.search(p.name)
    return (int(m.group(1)) if m else 10**9, p.name.lower())


def export_presentation(
    pptx_path: Path,
    out_dir: Path,
    *,
    width_px: int = 1920,
    height_px: int | None = None,
    export_pdf: bool = True,
    app_factory: Callable[[], Any] | None = None,
) -> ExportResult:
    """Export slides via PowerPoint COM to slide_001.png... and deck.pdf."""
    pptx_abs = Path(pptx_path).resolve()
    if not pptx_abs.is_file():
        raise FileNotFoundError(f"deck.pptx not found: {pptx_abs}")

    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    height_px = height_px or max(1, round(width_px * 9 / 16))

    with open_powerpoint_session(app_factory=app_factory) as sess:
        app = sess.app
        version = str(getattr(app, "Version", ""))
        build = str(getattr(app, "Build", ""))
        # Open read-only, untitled=False, windowless (ReadOnly=-1, Untitled=0, WithWindow=0)
        try:
            pres = sess.track_presentation(app.Presentations.Open(str(pptx_abs), MSO_TRUE, MSO_FALSE, MSO_FALSE))
        except Exception as e:
            raise RenderExecutionError(f"PowerPoint failed to open {pptx_abs} for export: {e}") from None

        try:
            n_slides = int(pres.Slides.Count)
            # Export PNGs into a clean temporary directory so stale files cannot mix in
            with tempfile.TemporaryDirectory(dir=str(out_dir)) as tmp_png_dir:
                with sess.step("export:png"):
                    pres.Export(str(Path(tmp_png_dir).resolve()), "PNG", int(width_px), int(height_px))
                raw_pngs = sorted(
                    [p for p in Path(tmp_png_dir).iterdir() if p.is_file() and p.suffix.lower() == ".png"],
                    key=_slide_sort_key,
                )
                if len(raw_pngs) != n_slides:
                    raise RenderExecutionError(
                        f"PowerPoint Export produced {len(raw_pngs)} PNG(s) for {n_slides} slide(s)"
                    )
                # Remove old slide_*.png before writing fresh ones
                for old in out_dir.glob("slide_*.png"):
                    old.unlink(missing_ok=True)

                png_paths: list[Path] = []
                for idx, src_png in enumerate(raw_pngs, start=1):
                    dst = out_dir / f"slide_{idx:03d}.png"
                    shutil.copy2(src_png, dst)
                    png_paths.append(dst)

            pdf_path: Path | None = None
            if export_pdf:
                pdf_target = out_dir / "deck.pdf"
                try:
                    # SaveAs(path, ppSaveAsPDF=32) avoids pywin32 late-binding PrintRange marshaling error
                    with sess.step("export:pdf"):
                        pres.SaveAs(str(pdf_target), PP_SAVE_AS_PDF)
                    if pdf_target.is_file():
                        pdf_path = pdf_target
                except Exception as e:
                    raise RenderExecutionError(f"PowerPoint PDF export (SaveAs ppSaveAsPDF) failed: {e}") from None
        finally:
            sess.close_presentation(pres)

    return ExportResult(
        png_paths=png_paths,
        pdf_path=pdf_path,
        renderer_name="powerpoint-com",
        renderer_version=version,
        renderer_build=build,
    )
