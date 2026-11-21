"""Environment diagnostics for Windows Office COM rendering (`pptv01 doctor`).

Checks every prerequisite for reliable Windows desktop PowerPoint + Excel COM rendering:
  - Windows OS version
  - Python runtime version
  - pywin32 package & COM bindings
  - Genuine Microsoft PowerPoint COM (Version + Build; rejects WPS)
  - Microsoft Excel COM (Version + Build; required for native editable AddChart2 charts)
  - Installed CJK fonts (Microsoft YaHei / DengXian / SimHei)
  - Optional: python-pptx (read-only verifier `[verify]`), LibreOffice `soffice` & `pdftoppm` (`--cross-render`)

Never touches or closes user-opened Office documents (Hard Rule 11).
"""

from __future__ import annotations

import importlib.metadata
import json
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

_CLICK_TO_RUN_KEY = r"SOFTWARE\Microsoft\Office\ClickToRun\Configuration"


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    required: bool
    ok: bool
    detail: str
    fix_hint: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def classify_office_edition(product_release_ids: str) -> str:
    """Classify Office ClickToRun ProductReleaseIds into a human-readable edition label.

    Never raises or rejects an edition; COM compatibility is governed by using only Office 2021 / Office 16 baseline APIs.
    """
    raw = (product_release_ids or "").strip()
    if not raw:
        return "未知"
    low = raw.lower()
    if any(tok in low for tok in ("o365", "m365", "365")):
        return "Microsoft 365 订阅"
    if any(tok in low for tok in ("2021", "2024", "ltsc")):
        return "Office 2021·LTSC"
    return "其他"


def read_office_click_to_run_config() -> dict[str, str]:
    """Read HKLM\\SOFTWARE\\Microsoft\\Office\\ClickToRun\\Configuration on Windows (empty dict on non-Windows/missing)."""
    if sys.platform != "win32":
        return {}
    try:
        import winreg
    except ImportError:
        return {}
    wanted = ("ProductReleaseIds", "VersionToReport", "UpdateChannel", "Platform")
    out: dict[str, str] = {}
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _CLICK_TO_RUN_KEY) as key:
            for field_name in wanted:
                try:
                    val, _ = winreg.QueryValueEx(key, field_name)
                    if val is not None:
                        out[field_name] = str(val)
                except OSError:
                    continue
    except OSError:
        return {}
    return out


@dataclass(frozen=True)
class DoctorReport:
    ok: bool
    checks: list[DoctorCheck]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": [asdict(c) for c in self.checks],
        }


def _probe_os() -> DoctorCheck:
    is_win = sys.platform == "win32"
    ver = f"{platform.system()} {platform.release()} ({platform.version()})"
    if is_win:
        return DoctorCheck("windows_os", required=True, ok=True, detail=ver)
    return DoctorCheck(
        "windows_os",
        required=True,
        ok=False,
        detail=f"{ver} (not Windows)",
        fix_hint="COM rendering requires Windows desktop + Microsoft PowerPoint. On Linux/macOS use `--compile-only` to generate deck_ops.json.",
    )


def _probe_python() -> DoctorCheck:
    ver = platform.python_version()
    ok = sys.version_info >= (3, 11)
    return DoctorCheck(
        "python",
        required=True,
        ok=ok,
        detail=f"Python {ver} ({sys.executable})",
        fix_hint="" if ok else "Install Python >= 3.11 and recreate v01/.venv.",
    )


def _probe_pywin32() -> DoctorCheck:
    if sys.platform != "win32":
        return DoctorCheck(
            "pywin32",
            required=True,
            ok=False,
            detail="not applicable on non-Windows platform",
            fix_hint="Install on Windows with: pip install -e '.[windows]'",
        )
    try:
        import pythoncom  # noqa: F401
        import win32com.client  # noqa: F401
    except Exception as e:  # noqa: BLE001
        return DoctorCheck(
            "pywin32",
            required=True,
            ok=False,
            detail=f"import failed: {type(e).__name__}: {e}",
            fix_hint="Run: pip install -e '.[windows]' && python -m pywin32_postinstall -install",
        )
    try:
        ver = importlib.metadata.version("pywin32")
    except importlib.metadata.PackageNotFoundError:
        ver = "installed (version metadata unavailable)"
    return DoctorCheck("pywin32", required=True, ok=True, detail=f"pywin32 {ver}")


def _probe_powerpoint_not_running(list_pids_fn: Callable[[], set[int]] | None = None) -> DoctorCheck:
    """Blocking check: refuse COM automation when PowerPoint is already running (single-instance COM hazard)."""
    if sys.platform != "win32" and list_pids_fn is None:
        return DoctorCheck(
            "powerpoint_not_running",
            required=True,
            ok=False,
            detail="not applicable on non-Windows platform",
            fix_hint="Run `pptv01 doctor` on Windows desktop before COM rendering.",
        )
    from pptv01.renderers.powerpoint_com.session import _list_powerpnt_pids

    pids = set((list_pids_fn or _list_powerpnt_pids)())
    if pids:
        return DoctorCheck(
            "powerpoint_not_running",
            required=True,
            ok=False,
            detail=f"检测到 PowerPoint 正在运行（PID {sorted(pids)}）— 阻断 COM 自动化",
            fix_hint="PowerPoint 只允许单实例运行，请先保存并关闭所有 PowerPoint 窗口（含任务管理器中残留的 POWERPNT.EXE）再重新运行。",
            metadata={"running_pids": sorted(pids)},
        )
    return DoctorCheck(
        "powerpoint_not_running",
        required=True,
        ok=True,
        detail="no running POWERPNT.EXE process detected",
        metadata={"running_pids": []},
    )


def _probe_powerpoint() -> DoctorCheck:
    from pptv01.renderers.powerpoint_com import PowerPointComRenderer
    from pptv01.renderers.protocol import RendererUnavailableError

    try:
        info = PowerPointComRenderer().preflight()
    except RendererUnavailableError as e:
        return DoctorCheck(
            "powerpoint_com",
            required=True,
            ok=False,
            detail=str(e),
            fix_hint=(
                "Install desktop Microsoft PowerPoint (Office 2021/365, not WPS), sign in/activate so no modal "
                "dialog blocks automation, and verify `PowerPoint.Application` COM registration."
            ),
        )
    except Exception as e:  # noqa: BLE001
        return DoctorCheck(
            "powerpoint_com",
            required=True,
            ok=False,
            detail=f"{type(e).__name__}: {e}",
            fix_hint="Close blocking PowerPoint dialogs, restart PowerPoint once, and retry `pptv01 doctor`.",
        )
    c2r = read_office_click_to_run_config()
    release_ids = c2r.get("ProductReleaseIds", "")
    edition = classify_office_edition(release_ids)
    ver_report = c2r.get("VersionToReport", "")
    channel = c2r.get("UpdateChannel", "")
    plat = c2r.get("Platform", "")

    parts = [f"Microsoft PowerPoint {info.version} (Build {info.build})", f"产品类型：{edition}"]
    if release_ids:
        parts.append(f"ProductReleaseIds={release_ids}")
    if ver_report:
        parts.append(f"VersionToReport={ver_report}")
    if plat:
        parts.append(f"Platform={plat}")
    if channel:
        parts.append(f"UpdateChannel={channel}")

    return DoctorCheck(
        "powerpoint_com",
        required=True,
        ok=True,
        detail=" | ".join(parts),
        metadata={
            "version": info.version,
            "build": info.build,
            "product_edition": edition,
            "ProductReleaseIds": release_ids,
            "VersionToReport": ver_report,
            "UpdateChannel": channel,
            "Platform": plat,
        },
    )


def _probe_excel() -> DoctorCheck:
    """Safely verify Microsoft Excel COM availability without touching user-opened workbooks."""
    if sys.platform != "win32":
        return DoctorCheck(
            "excel_com",
            required=True,
            ok=False,
            detail="not applicable on non-Windows platform",
            fix_hint="Microsoft Excel on Windows is required for native editable PowerPoint charts (AddChart2).",
        )
    from pptv01.renderers.powerpoint_com.session import probe_excel_com

    ok, ver, bld = probe_excel_com()
    if ok:
        return DoctorCheck(
            "excel_com",
            required=True,
            ok=True,
            detail=f"Microsoft Excel {ver} (Build {bld})",
        )
    return DoctorCheck(
        "excel_com",
        required=True,
        ok=False,
        detail="Excel.Application unavailable",
        fix_hint="Install Microsoft Excel alongside PowerPoint (required for native editable charts via AddChart2).",
    )


def _probe_cjk_fonts() -> DoctorCheck:
    from pptv01.renderers.powerpoint_com.renderer import _probe_installed_windows_fonts

    fonts = _probe_installed_windows_fonts()
    fonts_cf = {f.casefold(): f for f in fonts}
    preferred = [
        ("Microsoft YaHei", ("microsoft yahei", "微软雅黑")),
        ("DengXian", ("dengxian", "等线")),
        ("SimHei", ("simhei", "黑体")),
        ("SimSun", ("simsun", "宋体")),
        ("Noto Sans CJK SC", ("noto sans cjk sc", "noto sans sc")),
    ]
    found: list[str] = []
    for label, aliases in preferred:
        if any(a in fonts_cf for a in aliases):
            found.append(label)
    if found:
        return DoctorCheck(
            "cjk_fonts",
            required=True,
            ok=True,
            detail=f"found CJK fonts: {', '.join(found)} ({len(fonts)} total indexed)",
        )
    return DoctorCheck(
        "cjk_fonts",
        required=True,
        ok=False,
        detail=f"none of Microsoft YaHei / DengXian / SimHei found ({len(fonts)} fonts indexed)",
        fix_hint="Install Windows Chinese font pack (Microsoft YaHei / DengXian) so CJK slides render without tofu boxes.",
    )


def _probe_python_pptx() -> DoctorCheck:
    try:
        import pptx  # noqa: F401
        ver = importlib.metadata.version("python-pptx")
        return DoctorCheck("python_pptx_verify", required=False, ok=True, detail=f"python-pptx {ver} (read-only verifier)")
    except Exception:  # noqa: BLE001
        return DoctorCheck(
            "python_pptx_verify",
            required=False,
            ok=False,
            detail="not installed (optional read-only verifier)",
            fix_hint="Install with: pip install -e '.[verify]'",
        )


def _probe_optional_binary(name: str, bin_names: tuple[str, ...], version_arg: str, purpose: str) -> DoctorCheck:
    found_path = None
    for bn in bin_names:
        p = shutil.which(bn)
        if p:
            found_path = p
            break
    if not found_path:
        return DoctorCheck(
            name,
            required=False,
            ok=False,
            detail=f"not installed (optional — only needed for {purpose})",
            fix_hint=f"Optional: install {bin_names[0]} if you want `{purpose}`.",
        )
    ver_line = found_path
    try:
        res = subprocess.run([found_path, version_arg], capture_output=True, text=True, timeout=5)
        out = (res.stdout or res.stderr or "").strip().splitlines()
        if out:
            ver_line = f"{out[0].strip()} ({found_path})"
    except Exception:
        pass
    return DoctorCheck(name, required=False, ok=True, detail=ver_line)


def run_doctor(
    *,
    probes: list[Callable[[], DoctorCheck]] | None = None,
) -> DoctorReport:
    """Run all environment checks and return a structured DoctorReport."""
    active_probes = probes or [
        _probe_os,
        _probe_python,
        _probe_pywin32,
        _probe_powerpoint_not_running,
        _probe_powerpoint,
        _probe_excel,
        _probe_cjk_fonts,
        _probe_python_pptx,
        lambda: _probe_optional_binary("soffice", ("soffice", "libreoffice"), "--version", "--cross-render"),
        lambda: _probe_optional_binary("pdftoppm", ("pdftoppm",), "-v", "--cross-render"),
    ]
    checks = [p() for p in active_probes]
    all_required_ok = all(c.ok for c in checks if c.required)
    return DoctorReport(ok=all_required_ok, checks=checks)


def format_doctor_report(report: DoctorReport, *, as_json: bool = False) -> str:
    if as_json:
        return json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
    lines: list[str] = ["pptv01 doctor — Windows Office COM environment check", ""]
    for c in report.checks:
        if c.ok:
            tag = "[OK]  "
        elif c.required:
            tag = "[FAIL]"
        else:
            tag = "[OPT] "
        req_s = "required" if c.required else "optional"
        lines.append(f"  {tag} {c.name:<20} ({req_s:<8}) {c.detail}")
        if not c.ok and c.fix_hint:
            lines.append(f"         -> {c.fix_hint}")
    lines.append("")
    lines.append(f"overall: {'READY' if report.ok else 'NOT READY (fix required [FAIL] items above)'}")
    return "\n".join(lines)
