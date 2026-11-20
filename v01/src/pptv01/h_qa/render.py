"""LibreOffice headless -> PDF -> pdftoppm PNG renderer for Stage H.

Never skips or fakes rendering: if ``soffice`` or ``pdftoppm`` is missing, raises
``RendererUnavailableError`` explicitly.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image


class RendererUnavailableError(RuntimeError):
    """Raised when soffice or pdftoppm is missing on the host."""


class RenderExecutionError(RuntimeError):
    """Raised when soffice or pdftoppm fails during conversion."""


_SOFFICE_CANDIDATES = [
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/usr/bin/soffice",
    "/usr/local/bin/soffice",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
]


def find_render_tools(
    soffice_bin: str | None = None,
    pdftoppm_bin: str | None = None,
) -> tuple[str | None, str | None]:
    soffice = soffice_bin or shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        for cand in _SOFFICE_CANDIDATES:
            if Path(cand).is_file():
                soffice = cand
                break
    pdftoppm = pdftoppm_bin or shutil.which("pdftoppm")
    return soffice, pdftoppm


def has_render_tools() -> bool:
    s, p = find_render_tools()
    return bool(s and p)


def render_pptx_to_pngs(
    pptx_path: Path,
    out_dir: Path,
    *,
    dpi: int = 150,
    soffice_bin: str | None = None,
    pdftoppm_bin: str | None = None,
    timeout_s: float = 120.0,
) -> list[Path]:
    """Render ``pptx_path`` to ``out_dir/slide_001.png`` ... using LibreOffice + pdftoppm."""
    pptx_path = Path(pptx_path).resolve()
    out_dir = Path(out_dir)
    if not pptx_path.is_file():
        raise FileNotFoundError(f"deck file not found: {pptx_path}")

    soffice, pdftoppm = find_render_tools(soffice_bin, pdftoppm_bin)
    missing = [name for name, path in (("soffice", soffice), ("pdftoppm", pdftoppm)) if not path]
    if missing:
        raise RendererUnavailableError(
            f"cannot render PPTX to PNG: missing required tool(s) {', '.join(missing)} "
            "(install LibreOffice and Poppler pdftoppm)"
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("slide_*.png"):
        old.unlink()

    with tempfile.TemporaryDirectory(prefix="pptv01_render_") as tmp:
        tmp_p = Path(tmp)
        profile_dir = tmp_p / "lo_profile"
        profile_dir.mkdir()
        pdf_dir = tmp_p / "pdf"
        pdf_dir.mkdir()
        png_dir = tmp_p / "png"
        png_dir.mkdir()

        profile_uri = profile_dir.as_uri()
        cmd_lo = [
            soffice,
            f"-env:UserInstallation={profile_uri}",
            "--headless",
            "--norestore",
            "--convert-to",
            "pdf",
            str(pptx_path),
            "--outdir",
            str(pdf_dir),
        ]
        try:
            r_lo = subprocess.run(cmd_lo, capture_output=True, text=True, timeout=timeout_s, check=False)
        except (OSError, subprocess.SubprocessError) as e:
            raise RenderExecutionError(f"soffice invocation failed: {e}") from e
        if r_lo.returncode != 0:
            raise RenderExecutionError(
                f"soffice exited {r_lo.returncode}: {(r_lo.stderr or r_lo.stdout).strip()[:300]}"
            )
        pdfs = sorted(pdf_dir.glob("*.pdf"))
        if not pdfs:
            raise RenderExecutionError("soffice produced no PDF output")

        cmd_ppm = [
            pdftoppm,
            "-png",
            "-r",
            str(dpi),
            str(pdfs[0]),
            str(png_dir / "page"),
        ]
        try:
            r_ppm = subprocess.run(cmd_ppm, capture_output=True, text=True, timeout=timeout_s, check=False)
        except (OSError, subprocess.SubprocessError) as e:
            raise RenderExecutionError(f"pdftoppm invocation failed: {e}") from e
        if r_ppm.returncode != 0:
            raise RenderExecutionError(
                f"pdftoppm exited {r_ppm.returncode}: {(r_ppm.stderr or r_ppm.stdout).strip()[:300]}"
            )

        raw_pngs = sorted(
            png_dir.glob("page-*.png"),
            key=lambda p: int(p.stem.rsplit("-", 1)[-1]) if p.stem.rsplit("-", 1)[-1].isdigit() else 0,
        )
        if not raw_pngs:
            raise RenderExecutionError("pdftoppm produced no PNG pages")

        rendered: list[Path] = []
        for idx, src in enumerate(raw_pngs, start=1):
            dest = out_dir / f"slide_{idx:03d}.png"
            shutil.copy2(src, dest)
            with Image.open(dest) as im:
                im.verify()
            rendered.append(dest)
        return rendered
