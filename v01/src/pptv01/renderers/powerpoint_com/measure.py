"""PowerPoint COM live text measurement: BoundHeight/BoundWidth, AutoSize, fonts.

Adapted from internal aprd/.../reconstructor_verifier.py:468 (read-only copy).
Opens deck.pptx strictly read-only & windowless (ReadOnly=-1, Untitled=0, WithWindow=0).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from pptv01.renderers.protocol import MeasureResult, RenderExecutionError, TextMeasurement

from .constants import MSO_FALSE, MSO_TRUE
from .session import open_powerpoint_session


def measure_presentation(
    pptx_path: Path,
    *,
    page_ids: list[str] | None = None,
    app_factory: Callable[[], Any] | None = None,
) -> MeasureResult:
    """Measure every text-bearing shape via PowerPoint's own layout engine (TextFrame2)."""
    pptx_abs = Path(pptx_path).resolve()
    if not pptx_abs.is_file():
        raise FileNotFoundError(f"deck.pptx not found: {pptx_abs}")

    measurements: list[TextMeasurement] = []
    pres_fonts: list[str] = []

    with open_powerpoint_session(app_factory=app_factory) as sess:
        app = sess.app
        version = str(getattr(app, "Version", ""))
        build = str(getattr(app, "Build", ""))
        try:
            pres = sess.track_presentation(app.Presentations.Open(str(pptx_abs), MSO_TRUE, MSO_FALSE, MSO_FALSE))
        except Exception as e:
            raise RenderExecutionError(f"PowerPoint failed to open {pptx_abs} for measurement: {e}") from None

        try:
            try:
                for fnt in pres.Fonts:
                    fname = str(getattr(fnt, "Name", "") or "").strip()
                    if fname and fname not in pres_fonts:
                        pres_fonts.append(fname)
            except Exception:
                pass

            n_slides = int(pres.Slides.Count)
            for idx in range(1, n_slides + 1):
                slide = pres.Slides(idx)
                pid = page_ids[idx - 1] if (page_ids and idx - 1 < len(page_ids)) else f"S{idx:03d}"
                for sh in slide.Shapes:
                    try:
                        if int(getattr(sh, "HasTextFrame", 0)) != MSO_TRUE:
                            continue
                        tf2 = sh.TextFrame2
                        if int(getattr(tf2, "HasText", 0)) != MSO_TRUE:
                            continue
                        tr = tf2.TextRange
                        txt = str(getattr(tr, "Text", "") or "")
                        if not txt.strip():
                            continue
                        alloc_w = float(sh.Width)
                        alloc_h = float(sh.Height)
                        bound_w = float(tr.BoundWidth)
                        bound_h = float(tr.BoundHeight)
                        font_name = ""
                        font_fe = ""
                        font_sz: float | None = None
                        try:
                            f = tr.Font
                            font_name = str(getattr(f, "Name", "") or "")
                            font_fe = str(getattr(f, "NameFarEast", "") or "")
                            raw_sz = getattr(f, "Size", None)
                            if raw_sz is not None and float(raw_sz) > 0:
                                font_sz = round(float(raw_sz), 2)
                        except Exception:
                            pass
                        auto_sz: int | None = None
                        try:
                            auto_sz = int(getattr(tf2, "AutoSize", 0))
                        except Exception:
                            pass
                        measurements.append(
                            TextMeasurement(
                                slide_index=idx,
                                page_id=pid,
                                shape_id=int(getattr(sh, "Id", 0)),
                                shape_name=str(getattr(sh, "Name", "")),
                                allocated_w_pt=round(alloc_w, 2),
                                allocated_h_pt=round(alloc_h, 2),
                                bound_w_pt=round(bound_w, 2),
                                bound_h_pt=round(bound_h, 2),
                                overflow_h_pt=round(max(0.0, bound_h - alloc_h), 2),
                                overflow_w_pt=round(max(0.0, bound_w - alloc_w), 2),
                                font_name=font_name,
                                font_name_fareast=font_fe,
                                font_size_pt=font_sz,
                                auto_size=auto_sz,
                                text_length=len(txt.strip()),
                            )
                        )
                    except Exception:
                        continue
        finally:
            sess.close_presentation(pres)

    return MeasureResult(
        renderer_name="powerpoint-com",
        renderer_version=version,
        renderer_build=build,
        measurements=measurements,
        presentation_fonts=pres_fonts,
    )
