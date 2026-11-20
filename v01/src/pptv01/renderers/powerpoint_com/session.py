"""Safe PowerPoint & Excel COM session management (Office 16.0 / Office 2021 baseline APIs).

Politeness & safety guarantees (HANDOFF v3 Hard Rule 11 & B8 fix):
  - Always instantiates via ``win32com.client.DispatchEx("PowerPoint.Application")``
    (NEVER calls ``GetActiveObject`` to attach to a user's session).
  - Records PIDs spawned by this session (``_spawned_pids = post_pids - pre_pids``).
  - Sets ``DisplayAlerts = ppAlertsNone`` and ``AutomationSecurity = msoAutomationSecurityForceDisable``.
  - Tracks ONLY presentations created/opened by this session and closes ONLY those.
  - NEVER calls ``app.Quit()`` or terminates a process if PowerPoint was already running
    before our session started (even if the user's PowerPoint has 0 open presentations).
  - Arms a watchdog timer (``timeout_s``, default ``PPTV01_COM_TIMEOUT_S`` or 120s) that
    terminates ONLY ``_spawned_pids`` if a COM call hangs on a modal dialog, and raises
    a clear ``RenderExecutionError`` without swallowing exceptions.
"""

from __future__ import annotations

import gc
import os
import platform
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from pptv01.renderers.protocol import RenderExecutionError, RendererUnavailableError

from .constants import MSO_AUTOMATION_SECURITY_FORCE_DISABLE, PP_ALERTS_NONE

DEFAULT_COM_TIMEOUT_S = 120.0
ENV_COM_TIMEOUT_S = "PPTV01_COM_TIMEOUT_S"

_COM_THREAD_STATE = threading.local()


def _resolve_timeout_s(timeout_s: float | None) -> float:
    if timeout_s is not None:
        return max(0.05, float(timeout_s))
    raw = os.environ.get(ENV_COM_TIMEOUT_S, "").strip()
    if raw:
        try:
            return max(0.05, float(raw))
        except ValueError:
            pass
    return DEFAULT_COM_TIMEOUT_S


def _ensure_com_initialized(pythoncom: Any) -> None:
    """Initialize COM STA once per thread; never thrash CoUninitialize() mid-process.

    Thrashing CoInitialize/CoUninitialize across PowerPoint + Excel sessions on
    the same Windows thread invalidates Office 16.0 RPC proxy bindings
    (RPC_S_UNKNOWN_IF / -2147023179).
    """
    if not getattr(_COM_THREAD_STATE, "initialized", False):
        pythoncom.CoInitialize()
        _COM_THREAD_STATE.initialized = True


def _list_pids(image_name: str) -> set[int]:
    """Return currently running PIDs for ``image_name`` on Windows (empty set elsewhere)."""
    if sys.platform != "win32":
        return set()
    try:
        proc = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        pids: set[int] = set()
        want = image_name.lower()
        for line in proc.stdout.splitlines():
            parts = [p.strip().strip('"') for p in line.split(",")]
            if len(parts) >= 2 and parts[0].lower() == want and parts[1].isdigit():
                pids.add(int(parts[1]))
        return pids
    except Exception:
        return set()


def _list_powerpnt_pids() -> set[int]:
    return _list_pids("POWERPNT.EXE")


def _terminate_pids(pids: set[int]) -> None:
    """Force-terminate ONLY the explicitly owned PIDs on Windows (never kills by image name)."""
    if not pids or sys.platform != "win32":
        return
    for pid in sorted(pids):
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception:
            pass


def _connect_powerpoint(win32_client: Any) -> Any:
    """Launch PowerPoint via DispatchEx only (never GetActiveObject)."""
    return win32_client.DispatchEx("PowerPoint.Application")


def _wait_pids_exit(pids: set[int], timeout_s: float = 6.0, *, image_name: str = "POWERPNT.EXE") -> bool:
    if not pids or sys.platform != "win32":
        return True
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        alive = _list_pids(image_name) & pids
        if not alive:
            return True
        time.sleep(0.15)
    return not bool(_list_pids(image_name) & pids)


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
    """Check whether Excel.Application COM can be instantiated via DispatchEx (needed for AddChart2)."""
    if excel_factory is None:
        try:
            pythoncom, win32_client = _import_com_modules()
        except RendererUnavailableError:
            return False, "", ""
        _ensure_com_initialized(pythoncom)
        pre_pids = _list_pids("EXCEL.EXE")
        xl = None
        spawned: set[int] = set()
        try:
            xl = win32_client.DispatchEx("Excel.Application")
            spawned = _list_pids("EXCEL.EXE") - pre_pids
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
                if spawned:
                    try:
                        xl.Quit()
                    except Exception:
                        pass
                xl = None
            gc.collect()
            if spawned:
                _wait_pids_exit(spawned, timeout_s=5.0, image_name="EXCEL.EXE")

    xl = None
    try:
        xl = excel_factory()
        ver = str(getattr(xl, "Version", ""))
        bld = str(getattr(xl, "Build", ""))
        return True, ver, bld
    except Exception:
        return False, "", ""
    finally:
        if xl is not None and not getattr(xl, "is_preexisting", False):
            try:
                xl.Quit()
            except Exception:
                pass


class PowerPointSession:
    """Context manager wrapping a single PowerPoint COM session with PID ownership & watchdog timeout."""

    def __init__(
        self,
        *,
        app_factory: Callable[[], Any] | None = None,
        timeout_s: float | None = None,
        terminate_pids_fn: Callable[[set[int]], None] | None = None,
    ) -> None:
        self._app_factory = app_factory
        self._timeout_s = _resolve_timeout_s(timeout_s)
        self._terminate_pids_fn = terminate_pids_fn or _terminate_pids
        self._pythoncom: Any = None
        self.app: Any = None
        self._owned_presentations: list[Any] = []
        self._initial_open_count: int = 0
        self._spawned_pids: set[int] = set()
        self._owns_instance: bool = False
        self._prev_display_alerts: Any = None
        self._prev_automation_security: Any = None
        self._watchdog: threading.Timer | None = None
        self._timed_out: bool = False
        self._timeout_event = threading.Event()
        self._lock = threading.Lock()

    @property
    def spawned_pids(self) -> set[int]:
        return set(self._spawned_pids)

    @property
    def owns_instance(self) -> bool:
        return self._owns_instance

    @property
    def timed_out(self) -> bool:
        return self._timed_out

    def _on_timeout(self) -> None:
        with self._lock:
            self._timed_out = True
            owned_pids = set(self._spawned_pids)
        self._timeout_event.set()
        # NEVER terminate pre-existing user PowerPoint PIDs — only our own spawned PIDs
        if owned_pids:
            try:
                self._terminate_pids_fn(owned_pids)
            except Exception:
                pass

    def _start_watchdog(self) -> None:
        self._cancel_watchdog()
        t = threading.Timer(self._timeout_s, self._on_timeout)
        t.daemon = True
        t.start()
        self._watchdog = t

    def _cancel_watchdog(self) -> None:
        t = self._watchdog
        self._watchdog = None
        if t is not None:
            t.cancel()

    def check_timeout(self, operation: str = "COM operation") -> None:
        if self._timed_out:
            raise RenderExecutionError(
                f"PowerPoint {operation} timed out after {self._timeout_s:g}s "
                f"(watchdog terminated owned PID(s): {sorted(self._spawned_pids)})"
            )

    def track_presentation(self, pres: Any) -> Any:
        self.check_timeout("track_presentation")
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
            try:
                self._initial_open_count = int(self.app.Presentations.Count)
            except Exception:
                self._initial_open_count = 0
            raw_pids = getattr(self.app, "spawned_pids", None)
            if raw_pids is not None:
                self._spawned_pids = set(raw_pids)
            is_preexisting = bool(getattr(self.app, "is_preexisting", False))
            self._owns_instance = (not is_preexisting) and (self._initial_open_count == 0)
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
            self._start_watchdog()
            return self

        pythoncom, win32_client = _import_com_modules()
        self._pythoncom = pythoncom
        _ensure_com_initialized(pythoncom)
        pre_pids = _list_powerpnt_pids()
        last_err: Exception | None = None
        for _attempt in range(10):
            try:
                app = _connect_powerpoint(win32_client)
                _ = str(app.Version)
                init_count = int(app.Presentations.Count)
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
                _ = str(app.Version)
                self.app = app
                self._initial_open_count = init_count
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
        # Strictly own the instance ONLY when we actually spawned a new POWERPNT.EXE PID
        # and no pre-existing user presentations were open.
        self._owns_instance = bool(self._spawned_pids) and (self._initial_open_count == 0)
        self._start_watchdog()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self._cancel_watchdog()
        was_timed_out = self._timed_out

        # Close ONLY presentations created/opened by this session
        for pres in list(self._owned_presentations):
            try:
                pres.Close()
            except Exception:
                pass
        self._owned_presentations.clear()

        should_wait_exit = False
        if self.app is not None:
            if not self._owns_instance:
                # Pre-existing user PowerPoint instance (even if it had 0 open decks):
                # restore alert/security settings and NEVER call Quit().
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
                    remaining = 0 if was_timed_out else 1
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
            if should_wait_exit and self._spawned_pids:
                exited = _wait_pids_exit(self._spawned_pids, timeout_s=8.0)
                if not exited:
                    # Only ever terminate PIDs we spawned ourselves
                    self._terminate_pids_fn(self._spawned_pids)
                time.sleep(0.3)

        if was_timed_out and exc_type is not RenderExecutionError:
            raise RenderExecutionError(
                f"PowerPoint COM session timed out after {self._timeout_s:g}s "
                f"(watchdog terminated owned PID(s): {sorted(self._spawned_pids)})"
            )


@contextmanager
def open_powerpoint_session(
    app_factory: Callable[[], Any] | None = None,
    *,
    timeout_s: float | None = None,
    terminate_pids_fn: Callable[[set[int]], None] | None = None,
) -> Iterator[PowerPointSession]:
    with PowerPointSession(
        app_factory=app_factory,
        timeout_s=timeout_s,
        terminate_pids_fn=terminate_pids_fn,
    ) as sess:
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
