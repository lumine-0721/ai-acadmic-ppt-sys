"""PowerPointComRenderer: DeckRenderer implementation driven by Windows PowerPoint COM."""

from __future__ import annotations

import platform
from pathlib import Path
from typing import Any, Callable

from pptv01.contracts import DeckOps
from pptv01.renderers.protocol import (
    BuildResult,
    ExportResult,
    MeasureResult,
    RendererInfo,
)

from .executor import build_presentation
from .export import export_presentation
from .measure import measure_presentation
from .session import open_powerpoint_session, probe_excel_com

COMMON_CJK_FONTS = ("Microsoft YaHei", "DengXian", "SimHei", "SimSun", "Calibri", "Arial")


def _probe_installed_windows_fonts() -> list[str]:
    fonts_dir = Path("C:/Windows/Fonts")
    if not fonts_dir.is_dir():
        return []
    found: list[str] = []
    # Map well-known Windows font filenames to family names
    file_map = {
        "msyh.ttc": ("Microsoft YaHei", "微软雅黑"),
        "deng.ttf": ("DengXian", "等线"),
        "dengl.ttf": ("DengXian Light", "等线 Light"),
        "simhei.ttf": ("SimHei", "黑体"),
        "simsun.ttc": ("SimSun", "宋体"),
        "calibri.ttf": ("Calibri",),
        "arial.ttf": ("Arial",),
    }
    for fname, families in file_map.items():
        if (fonts_dir / fname).is_file():
            for family in families:
                if family not in found:
                    found.append(family)
    return found


class PowerPointComRenderer:
    """Real Windows desktop Microsoft PowerPoint COM renderer (Office 16.0+ / Office 2021 baseline APIs only)."""

    name: str = "powerpoint-com"

    def __init__(
        self,
        *,
        app_factory: Callable[[], Any] | None = None,
        excel_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._app_factory = app_factory
        self._excel_factory = excel_factory

    def preflight(self) -> RendererInfo:
        """Probe PowerPoint COM, Excel COM, and installed fonts; raise RendererUnavailableError if unavailable."""
        with open_powerpoint_session(app_factory=self._app_factory) as sess:
            app = sess.app
            version = str(getattr(app, "Version", ""))
            build = str(getattr(app, "Build", ""))

        excel_ok, excel_ver, excel_bld = probe_excel_com(excel_factory=self._excel_factory)
        fonts = _probe_installed_windows_fonts()

        return RendererInfo(
            name=self.name,
            available=True,
            platform=f"{platform.system()} {platform.release()}",
            version=version,
            build=build,
            excel_available=excel_ok,
            excel_version=excel_ver,
            excel_build=excel_bld,
            fonts=fonts,
        )

    def build(self, ops: DeckOps, run_dir: Path) -> BuildResult:
        return build_presentation(ops, run_dir, app_factory=self._app_factory)

    def export(self, pptx: Path, out_dir: Path, width_px: int = 1920) -> ExportResult:
        return export_presentation(pptx, out_dir, width_px=width_px, app_factory=self._app_factory)

    def measure(self, pptx: Path, page_ids: list[str] | None = None) -> MeasureResult:
        return measure_presentation(pptx, page_ids=page_ids, app_factory=self._app_factory)
