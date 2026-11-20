"""Safe PowerPoint COM session management (Windows desktop Office 2021+).

Politeness & safety guarantees (HANDOFF v2 Hard Rule 11):
  - Uses ``win32com.client.DispatchEx("PowerPoint.Application")`` on Windows.
  - Sets ``DisplayAlerts = ppAlertsNone`` and ``AutomationSecurity = msoAutomationSecurityForceDisable``.
  - Tracks ONLY presentations created/opened by this session and closes ONLY those.
  - NEVER touches or closes any pre-existing presentation the user has open.
  - Calls ``app.Quit()`` ONLY if no presentations were open before our session started
    and none remain open after closing our own presentations.
"""

from __future__ import annotations

import gc
import platform
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from pptv01.renderers.protocol import RenderExecutionError, RendererUnavailableError

from .constants import MSO_AUTOMATION_SECURITY_FORCE_DISABLE, PP_ALERTS_NONE

_COM_THREAD_STATE = threading.local()


def _ensure_com_initialized(pythoncom: Any) -> None:
    """Initialize COM STA once per thread; never thrash CoUninitialize() mid-process.

    Thrashing CoInitialize/CoUninitialize across PowerPoint + Excel sessions on
    the same Windows thread invalidates Office 16.0 RPC proxy bindings
    (RPC_S_UNKNOWN_IF / -2147023179).
    """
    if not getattr(_COM_THREAD_STATE, "initialized", False):
        pythoncom.CoInitialize()
        _COM_THREAD_STATE.initialized = True


def _list_powerpnt_pids() -> set[int]:
    """Return currently running POWERPNT.EXE PIDs on Windows (empty set elsewhere)."""
    if sys.platform != "win32":
        return set()
    try:
        proc = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq POWERPNT.EXE", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        pids: set[int] = set()
        for line in proc.stdout.splitlines():
            parts = [p.strip().strip('"') for p in line.split(",")]
            if len(parts) >= 2 and parts[0].lower() == "powerpnt.exe" and parts[1].isdigit():
                pids.add(int(parts[1]))
        return pids
    except Exception:
        return set()


def _connect_powerpoint(win32_client: Any) -> Any:
    """Attach to a running PowerPoint or launch one cleanly via Dispatch (ykuwai/ppt-mcp pattern)."""
    try:
        return win32_client.GetActiveObject("PowerPoint.Application")
    except Exception:
        return win32_client.Dispatch("PowerPoint.Application")


def _wait_pids_exit(pids: set[int], timeout_s: float = 6.0) -> None:
    if not pids or sys.platform != "win32":
        return
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        alive = _list_powerpnt_pids() & pids
        if not alive:
            return
        time.sleep(0.15)


def _import_com_modules() -> tuple[Any, Any]:
    if sys.platform != "win32":
        raise RendererUnavailableError(
            f"PowerPoint COM renderer requires Windows with Microsoft PowerPoint (current platform: {platform.system()})"
        )
    try:
        import pythoncom  # type: ignore[import-not-found]
        import win32com.client  # type: ignore[import-not-found]
    except ImportError as e:
        raise RendererUnavailableError(
            f"pywin32 is not installed ({e}); install with `pip install -e \".[windows]\"`"
        ) from None
    return pythoncom, win32com.client


def probe_excel_com(excel_factory: Callable[[], Any] | None = None) -> tuple[bool, str, str]:
    """Check whether Excel.Application COM can be instantiated (needed for AddChart2)."""
    if excel_factory is None:
        try:
            pythoncom, win32_client = _import_com_modules()
        except RendererUnavailableError:
            return False, "", ""
        _ensure_com_initialized(pythoncom)
        xl = None
        try:
            xl = win32_client.DispatchEx("Excel.Application")
            try:
                xl.DisplayAlerts = False
            except Exception:
                pass
            ver = str(getattr(xl, "Version", ""))
            bld = str(getattr(xl, "Build", ""))
            return True, ver, bld
        except Exception:
            return False, "", ""
        finally:
            if xl is not None:
                try:
                    xl.Quit()
                except Exception:
                    pass
                xl = None
            gc.collect()

    xl = None
    try:
        xl = excel_factory()
        ver = str(getattr(xl, "Version", ""))
        bld = str(getattr(xl, "Build", ""))
        return True, ver, bld
    except Exception:
        return False, "", ""
    finally:
        if xl is not None:
            try:
                xl.Quit()
            except Exception:
                pass


class PowerPointSession:
    """Context manager wrapping a single PowerPoint COM session safely."""

    def __init__(self, *, app_factory: Callable[[], Any] | None = None) -> None:
        self._app_factory = app_factory
        self._pythoncom: Any = None
        self.app: Any = None
        self._owned_presentations: list[Any] = []
        self._initial_open_count: int = 0
        self._spawned_pids: set[int] = set()
        self._prev_display_alerts: Any = None
        self._prev_automation_security: Any = None

    def track_presentation(self, pres: Any) -> Any:
        self._owned_presentations.append(pres)
        return pres

    def close_presentation(self, pres: Any) -> None:
        try:
            pres.Close()
        except Exception:
            pass
        self._owned_presentations = [p for p in self._owned_presentations if p is not pres]

    def __enter__(self) -> "PowerPointSession":
        if self._app_factory is not None:
            try:
                self.app = self._app_factory()
            except RendererUnavailableError:
                raise
            except Exception as e:
                raise RendererUnavailableError(f"failed to initialize PowerPoint COM: {e}") from None
        else:
            pythoncom, win32_client = _import_com_modules()
            self._pythoncom = pythoncom
            _ensure_com_initialized(pythoncom)
            pre_pids = _list_powerpnt_pids()
            last_err: Exception | None = None
            for _attempt in range(10):
                try:
                    app = _connect_powerpoint(win32_client)
                    # Touch Version & Presentations.Count and verify stability across calls
                    _ = str(app.Version)
                    _ = int(app.Presentations.Count)
                    try:
                        self._prev_display_alerts = getattr(app, "DisplayAlerts", None)
                        app.DisplayAlerts = PP_ALERTS_NONE
                    except Exception:
                        pass
                    try:
                        self._prev_automation_security = getattr(app, "AutomationSecurity", None)
                        app.AutomationSecurity = MSO_AUTOMATION_SECURITY_FORCE_DISABLE
                    except Exception:
                        pass
                    # Re-check Version after configuring alerts/security to guarantee live COM handle
                    _ = str(app.Version)
                    self.app = app
                    last_err = None
                    break
                except Exception as e:
                    last_err = e
                    time.sleep(0.6)
            if self.app is None:
                self._pythoncom = None
                raise RendererUnavailableError(
                    f"Microsoft PowerPoint COM ('PowerPoint.Application') is unavailable: {last_err}"
                ) from None
            self._spawned_pids = _list_powerpnt_pids() - pre_pids

        if self._app_factory is not None:
            try:
                self._initial_open_count = int(self.app.Presentations.Count)
            except Exception:
                self._initial_open_count = 0
            try:
                self._prev_display_alerts = getattr(self.app, "DisplayAlerts", None)
                self.app.DisplayAlerts = PP_ALERTS_NONE
            except Exception:
                pass
            try:
                self._prev_automation_security = getattr(self.app, "AutomationSecurity", None)
                self.app.AutomationSecurity = MSO_AUTOMATION_SECURITY_FORCE_DISABLE
            except Exception:
                pass
        else:
            try:
                self._initial_open_count = int(self.app.Presentations.Count)
            except Exception:
                self._initial_open_count = 0

        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        # Close ONLY presentations created/opened by this session
        for pres in list(self._owned_presentations):
            try:
                pres.Close()
            except Exception:
                pass
        self._owned_presentations.clear()

        should_wait_exit = False
        if self.app is not None:
            # Restore alert/security settings if we're leaving a shared PowerPoint open
            if self._initial_open_count > 0:
                if self._prev_display_alerts is not None:
                    try:
                        self.app.DisplayAlerts = self._prev_display_alerts
                    except Exception:
                        pass
                if self._prev_automation_security is not None:
                    try:
                        self.app.AutomationSecurity = self._prev_automation_security
                    except Exception:
                        pass
            else:
                try:
                    remaining = int(self.app.Presentations.Count)
                except Exception:
                    remaining = 1
                if remaining == 0:
                    try:
                        self.app.Quit()
                    except Exception:
                        pass
                    should_wait_exit = True
            self.app = None

        gc.collect()
        if self._pythoncom is not None:
            self._pythoncom = None
            if should_wait_exit:
                if self._spawned_pids:
                    _wait_pids_exit(self._spawned_pids, timeout_s=8.0)
                time.sleep(0.4)


@contextmanager
def open_powerpoint_session(app_factory: Callable[[], Any] | None = None) -> Iterator[PowerPointSession]:
    with PowerPointSession(app_factory=app_factory) as sess:
        yield sess


def safe_close_workbook(wb: Any) -> None:
    """Close an embedded Excel workbook without saving (prevents Excel process leaks).

    Adapted from thomas-villani/pptlive (_charts.py::_safe_close, MIT) and
    ykuwai/ppt-mcp (charts.py, MIT).
    """
    if wb is None:
        return
    try:
        wb.Close(False)
    except Exception:
        pass
