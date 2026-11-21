"""Safe PowerPoint & Excel COM session management (Office 16.0 / Office 2021 baseline APIs).

Politeness & safety guarantees (HANDOFF v3 Hard Rule 11 & B8 / N2):
  - Always instantiates via ``win32com.client.DispatchEx("PowerPoint.Application")``
    (NEVER calls ``GetActiveObject``, ``GetObject``, or plain ``Dispatch``).
  - Records PIDs spawned by this session (``_spawned_pids = post_ppt_pids - pre_ppt_pids``)
    and baseline Excel PIDs (``_pre_excel_pids``) so any spawned Excel child is also tracked.
  - Sets ``DisplayAlerts = ppAlertsNone`` and ``AutomationSecurity = msoAutomationSecurityForceDisable``.
  - Tracks ONLY presentations created/opened by this session and closes ONLY those.
  - NEVER calls ``app.Quit()``, minimizes, or terminates a PowerPoint/Excel process that
    was already running before our session started (even if it has 0 open presentations).
  - Arms BOTH a per-session total watchdog (default ``PPTV01_COM_TIMEOUT_S`` or 120s) AND
    per-step call watchdogs via ``with sess.step("build:add_chart")`` (default ``PPTV01_COM_STEP_TIMEOUT_S`` or 45s).
  - On timeout, terminates ONLY our own spawned PowerPoint/Excel PIDs and raises a clear
    ``RenderExecutionError`` stating the stuck step, elapsed time, and owned PIDs.
  - Even on ``KeyboardInterrupt`` (Ctrl+C), cleans up ONLY our own presentations & spawned PIDs.
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
DEFAULT_COM_STEP_TIMEOUT_S = 45.0
ENV_COM_TIMEOUT_S = "PPTV01_COM_TIMEOUT_S"
ENV_COM_STEP_TIMEOUT_S = "PPTV01_COM_STEP_TIMEOUT_S"
ENV_ALLOW_EXISTING_PPT = "PPTV01_ALLOW_EXISTING_POWERPOINT"

_COM_THREAD_STATE = threading.local()


def _resolve_allow_existing(allow_existing: bool | None) -> bool:
    if allow_existing is not None:
        return bool(allow_existing)
    raw = os.environ.get(ENV_ALLOW_EXISTING_PPT, "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _resolve_timeout_s(timeout_s: float | None, *, env_var: str = ENV_COM_TIMEOUT_S, default: float = DEFAULT_COM_TIMEOUT_S) -> float:
    if timeout_s is not None:
        return max(0.05, float(timeout_s))
    raw = os.environ.get(env_var, "").strip()
    if raw:
        try:
            return max(0.05, float(raw))
        except ValueError:
            pass
    return default


def _ensure_com_initialized(pythoncom: Any) -> None:
    """Initialize COM STA once per thread; never thrash CoUninitialize() mid-process.

    Thrashing CoInitialize/CoUninitialize across PowerPoint + Excel sessions on
    the same Windows thread invalidates Office 16.0 RPC proxy bindings
    (RPC_S_UNKNOWN_IF / -2147023179).
    """
    if not getattr(_COM_THREAD_STATE, "initialized", False):
        pythoncom.CoInitialize()
        _COM_THREAD_STATE.initialized = True


def _list_pids(image_name: str, *, retries: int = 3, retry_delay_s: float = 0.15) -> set[int]:
    """Return currently running PIDs for ``image_name`` on Windows (empty set elsewhere).

    Retries transient ``tasklist`` failures and raises ``RuntimeError`` if enumeration
    still fails — NEVER silently falls back to an empty set on Windows (which could
    cause an unknown baseline to be mistaken for "no processes running").
    """
    if sys.platform != "win32":
        return set()
    last_err: str = ""
    attempts = max(1, int(retries))
    for attempt in range(attempts):
        try:
            proc = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if proc.returncode != 0:
                last_err = f"tasklist exited with code {proc.returncode}: {(proc.stderr or proc.stdout).strip()}"
            else:
                pids: set[int] = set()
                want = image_name.lower()
                for line in proc.stdout.splitlines():
                    parts = [p.strip().strip('"') for p in line.split(",")]
                    if len(parts) >= 2 and parts[0].lower() == want and parts[1].isdigit():
                        pids.add(int(parts[1]))
                return pids
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
        if attempt + 1 < attempts:
            time.sleep(retry_delay_s)
    raise RuntimeError(f"failed to enumerate {image_name} PIDs via tasklist after {attempts} attempts: {last_err}")


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
    """Launch PowerPoint via DispatchEx only (never attaches to an existing user session)."""
    return win32_client.DispatchEx("PowerPoint.Application")


def _wait_pids_exit(pids: set[int], timeout_s: float = 6.0, *, image_name: str = "POWERPNT.EXE") -> bool:
    if not pids or sys.platform != "win32":
        return True
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            alive = _list_pids(image_name, retries=1) & pids
        except Exception:
            alive = set(pids)
        if not alive:
            return True
        time.sleep(0.15)
    try:
        return not bool(_list_pids(image_name, retries=1) & pids)
    except Exception:
        return False


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
        try:
            pre_pids: set[int] | None = _list_pids("EXCEL.EXE")
        except Exception:
            pre_pids = None
        xl = None
        spawned: set[int] = set()
        try:
            xl = win32_client.DispatchEx("Excel.Application")
            if pre_pids is not None:
                try:
                    spawned = _list_pids("EXCEL.EXE") - pre_pids
                except Exception:
                    spawned = set()
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
            if spawned:
                exited = _wait_pids_exit(spawned, timeout_s=5.0, image_name="EXCEL.EXE")
                if not exited:
                    _terminate_pids(spawned)

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
    """Context manager wrapping a single PowerPoint COM session with PID ownership & dual watchdogs."""

    def __init__(
        self,
        *,
        app_factory: Callable[[], Any] | None = None,
        timeout_s: float | None = None,
        step_timeout_s: float | None = None,
        terminate_pids_fn: Callable[[set[int]], None] | None = None,
        list_excel_pids_fn: Callable[[], set[int]] | None = None,
        list_powerpnt_pids_fn: Callable[[], set[int]] | None = None,
        allow_existing_powerpoint: bool | None = None,
    ) -> None:
        self._app_factory = app_factory
        self._timeout_s = _resolve_timeout_s(timeout_s, env_var=ENV_COM_TIMEOUT_S, default=DEFAULT_COM_TIMEOUT_S)
        self._step_timeout_s = _resolve_timeout_s(
            step_timeout_s, env_var=ENV_COM_STEP_TIMEOUT_S, default=DEFAULT_COM_STEP_TIMEOUT_S
        )
        self._terminate_pids_fn = terminate_pids_fn or _terminate_pids
        if list_excel_pids_fn is not None:
            self._list_excel_pids_fn = list_excel_pids_fn
        elif app_factory is not None:
            # Fake-COM test seam must NEVER hit real OS tasklist unless explicitly injected.
            self._list_excel_pids_fn = lambda: set()
        else:
            self._list_excel_pids_fn = lambda: _list_pids("EXCEL.EXE")
        self._list_powerpnt_pids_fn = list_powerpnt_pids_fn
        self._allow_existing = _resolve_allow_existing(allow_existing_powerpoint)
        self._pythoncom: Any = None
        self.app: Any = None
        self._owned_presentations: list[Any] = []
        self._initial_open_count: int = 0
        self._spawned_pids: set[int] = set()
        self._pre_excel_pids: set[int] | None = None
        self._active_chart_pre_excel_pids: set[int] | None = None
        self._spawned_excel_pids: set[int] = set()
        self._dispatched: bool = False
        self._owns_instance: bool = False
        self._prev_display_alerts: Any = None
        self._prev_automation_security: Any = None
        self._session_watchdog: threading.Timer | None = None
        self._step_watchdog: threading.Timer | None = None
        self._current_step: str = "session_init"
        self._started_monotonic: float = time.monotonic()
        self._step_started_monotonic: float = self._started_monotonic
        self._timed_out: bool = False
        self._timeout_message: str = ""
        self._timeout_event = threading.Event()
        self._lock = threading.Lock()

    @property
    def spawned_pids(self) -> set[int]:
        return set(self._spawned_pids)

    @property
    def spawned_excel_pids(self) -> set[int]:
        return set(self._spawned_excel_pids)

    @property
    def owns_instance(self) -> bool:
        return self._owns_instance

    @property
    def timed_out(self) -> bool:
        return self._timed_out

    def snapshot_excel_pids(self) -> set[int] | None:
        """Capture an explicit Excel PID baseline immediately before an embedded-chart op."""
        if not self._dispatched or not self._owns_instance or self._pre_excel_pids is None:
            return None
        try:
            return set(self._list_excel_pids_fn())
        except Exception:
            return None

    def record_spawned_excel_pids(self, before_pids: set[int] | None = None) -> set[int]:
        """Record Excel PIDs spawned during an explicit chart step against its before_pids snapshot.

        NEVER diffs against the whole-session baseline so an unrelated user Excel opened mid-session
        outside ``add_chart`` is never treated as spawned by us.
        """
        if not self._dispatched or not self._owns_instance or before_pids is None:
            return set()
        try:
            cur = set(self._list_excel_pids_fn())
        except Exception:
            return set()
        newly_spawned = cur - before_pids
        if newly_spawned:
            self._spawned_excel_pids |= newly_spawned
        return set(self._spawned_excel_pids)

    def _collect_spawned_excel_pids(self) -> set[int]:
        if self._active_chart_pre_excel_pids is None:
            return set()
        return self.record_spawned_excel_pids(self._active_chart_pre_excel_pids)

    def _on_timeout(self, *, kind: str, limit_s: float) -> None:
        with self._lock:
            if self._timed_out:
                return
            self._timed_out = True
            step_name = self._current_step
            elapsed = time.monotonic() - (
                self._step_started_monotonic if kind == "step" else self._started_monotonic
            )
            owned_ppt = set(self._spawned_pids)
            if self._active_chart_pre_excel_pids is not None:
                self._collect_spawned_excel_pids()
            owned_xl = set(self._spawned_excel_pids)
            to_kill = owned_ppt | owned_xl
            self._timeout_message = (
                f"PowerPoint COM {kind} timed out during step={step_name!r} "
                f"after waiting {elapsed:.1f}s (limit={limit_s:g}s; "
                f"owned powerpoint_pids={sorted(owned_ppt)}, excel_pids={sorted(owned_xl)})"
            )
        self._timeout_event.set()
        # NEVER terminate pre-existing user PowerPoint/Excel PIDs — ONLY our own spawned PIDs
        if to_kill:
            try:
                self._terminate_pids_fn(to_kill)
            except Exception:
                pass

    def _start_session_watchdog(self) -> None:
        self._cancel_session_watchdog()
        t = threading.Timer(self._timeout_s, self._on_timeout, kwargs={"kind": "session", "limit_s": self._timeout_s})
        t.daemon = True
        t.start()
        self._session_watchdog = t

    def _cancel_session_watchdog(self) -> None:
        t = self._session_watchdog
        self._session_watchdog = None
        if t is not None:
            t.cancel()

    def _cancel_step_watchdog(self) -> None:
        t = self._step_watchdog
        self._step_watchdog = None
        if t is not None:
            t.cancel()

    def check_timeout(self, operation: str = "COM operation") -> None:
        if self._timed_out:
            msg = self._timeout_message or (
                f"PowerPoint {operation} timed out (owned powerpoint_pids={sorted(self._spawned_pids)})"
            )
            raise RenderExecutionError(msg)

    @contextmanager
    def step(
        self,
        name: str,
        *,
        timeout_s: float | None = None,
        track_excel: bool | None = None,
    ) -> Iterator[None]:
        """Mark the active COM step and arm a per-call watchdog timer."""
        self.check_timeout(name)
        limit = min(self._timeout_s, self._step_timeout_s if timeout_s is None else max(0.05, float(timeout_s)))
        should_track_excel = ("add_chart" in name) if track_excel is None else bool(track_excel)
        before_xl = self.snapshot_excel_pids() if should_track_excel else None
        with self._lock:
            self._current_step = name
            self._step_started_monotonic = time.monotonic()
            self._active_chart_pre_excel_pids = before_xl
        t = threading.Timer(limit, self._on_timeout, kwargs={"kind": "step", "limit_s": limit})
        t.daemon = True
        self._step_watchdog = t
        t.start()
        try:
            yield
            if before_xl is not None:
                self.record_spawned_excel_pids(before_xl)
            self.check_timeout(name)
        except BaseException:
            if before_xl is not None:
                self.record_spawned_excel_pids(before_xl)
            if self._timed_out:
                raise RenderExecutionError(self._timeout_message) from None
            raise
        finally:
            with self._lock:
                self._active_chart_pre_excel_pids = None
            self._cancel_step_watchdog()

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
        self._started_monotonic = time.monotonic()
        self._step_started_monotonic = self._started_monotonic
        self._current_step = "session_connect"
        self._dispatched = False
        try:
            self._pre_excel_pids = set(self._list_excel_pids_fn())
        except Exception:
            # NEVER equate enumeration failure with "no Excel running" — keep baseline None so
            # we never taskkill Excel based on an unknown baseline.
            self._pre_excel_pids = None

        if self._app_factory is not None:
            pre_pids: set[int] = set()
            if self._list_powerpnt_pids_fn is not None:
                try:
                    pre_pids = set(self._list_powerpnt_pids_fn())
                except Exception as e:
                    raise RendererUnavailableError(f"无法枚举 POWERPNT.EXE 进程（{e}），拒绝启动 PowerPoint COM。") from None
                if pre_pids and not self._allow_existing:
                    raise RendererUnavailableError(
                        f"检测到 PowerPoint 已在运行（PID {sorted(pre_pids)}）。"
                        "PowerPoint 只允许一个实例，请先保存并关闭所有 PowerPoint 窗口后再运行。"
                    )
            try:
                app = self._app_factory()
            except RendererUnavailableError:
                raise
            except Exception as e:
                raise RendererUnavailableError(f"failed to initialize PowerPoint COM: {e}") from None

            if self._list_powerpnt_pids_fn is not None:
                try:
                    post_pids = set(self._list_powerpnt_pids_fn())
                except Exception as e:
                    app = None
                    gc.collect()
                    raise RendererUnavailableError(f"启动后无法枚举 POWERPNT.EXE 进程（{e}）。") from None
                spawned = post_pids - pre_pids
                if not spawned and not self._allow_existing:
                    app = None
                    gc.collect()
                    raise RendererUnavailableError(
                        "启动 PowerPoint 后未检测到新进程 PID（说明 COM 连到了已有 PowerPoint 实例）。"
                        "请先保存并关闭所有 PowerPoint 窗口后再运行。"
                    )
                self._spawned_pids = spawned
            else:
                raw_pids = getattr(app, "spawned_pids", None)
                if raw_pids is not None:
                    self._spawned_pids = set(raw_pids)

            is_preexisting = bool(getattr(app, "is_preexisting", False))
            if is_preexisting and not self._allow_existing:
                app = None
                gc.collect()
                raise RendererUnavailableError(
                    "检测到 PowerPoint 已在运行。PowerPoint 只允许一个实例，请先保存并关闭所有 PowerPoint 窗口后再运行。"
                )

            self.app = app
            self._dispatched = True
            try:
                self._initial_open_count = int(self.app.Presentations.Count)
            except Exception:
                self._initial_open_count = 0
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
            self._start_session_watchdog()
            return self

        pid_lister = self._list_powerpnt_pids_fn or _list_powerpnt_pids
        try:
            pre_pids = set(pid_lister())
        except Exception as e:
            raise RendererUnavailableError(f"无法枚举 POWERPNT.EXE 进程（{e}），拒绝启动 PowerPoint COM。") from None
        if pre_pids and not self._allow_existing:
            raise RendererUnavailableError(
                f"检测到 PowerPoint 已在运行（PID {sorted(pre_pids)}）。"
                "PowerPoint 只允许一个实例，请先保存并关闭所有 PowerPoint 窗口后再运行。"
            )

        pythoncom, win32_client = _import_com_modules()
        self._pythoncom = pythoncom
        _ensure_com_initialized(pythoncom)

        # Re-check PIDs right before DispatchEx in case a user opened PowerPoint during module import
        try:
            pre_pids = set(pid_lister())
        except Exception as e:
            self._pythoncom = None
            raise RendererUnavailableError(f"无法枚举 POWERPNT.EXE 进程（{e}），拒绝启动 PowerPoint COM。") from None
        if pre_pids and not self._allow_existing:
            self._pythoncom = None
            raise RendererUnavailableError(
                f"检测到 PowerPoint 已在运行（PID {sorted(pre_pids)}）。"
                "PowerPoint 只允许一个实例，请先保存并关闭所有 PowerPoint 窗口后再运行。"
            )

        last_err: Exception | None = None
        spawned: set[int] = set()
        for _attempt in range(10):
            try:
                app = _connect_powerpoint(win32_client)
                post_pids = set(pid_lister())
                spawned = post_pids - pre_pids
                if not spawned and not self._allow_existing:
                    # PowerPoint attached to an existing instance instead of starting a new process:
                    # release COM proxy immediately without touching Presentations/DisplayAlerts/Quit/taskkill.
                    app = None
                    gc.collect()
                    self._pythoncom = None
                    raise RendererUnavailableError(
                        "启动 PowerPoint 后未检测到新进程 PID（说明 COM 连到了已有 PowerPoint 实例）。"
                        "请先保存并关闭所有 PowerPoint 窗口后再运行。"
                    )
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
            except RendererUnavailableError:
                raise
            except Exception as e:
                last_err = e
                time.sleep(0.6)
        if self.app is None:
            self._pythoncom = None
            raise RendererUnavailableError(
                f"Microsoft PowerPoint COM ('PowerPoint.Application') is unavailable: {last_err}"
            ) from None

        self._spawned_pids = spawned
        self._dispatched = True
        # Strictly own the instance ONLY when we actually spawned a new POWERPNT.EXE PID
        # and no pre-existing user presentations were open.
        self._owns_instance = bool(self._spawned_pids) and (self._initial_open_count == 0)
        self._start_session_watchdog()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self._cancel_step_watchdog()
        self._cancel_session_watchdog()
        was_timed_out = self._timed_out
        interrupted = exc_type is KeyboardInterrupt

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
                    remaining = 0 if (was_timed_out or interrupted) else 1
                if remaining == 0 or interrupted:
                    try:
                        self.app.Quit()
                    except Exception:
                        pass
                    should_wait_exit = True
            self.app = None

        gc.collect()
        # Clean up only Excel PIDs explicitly recorded during add_chart steps (never diff whole-session baseline!)
        owned_xl_early = set(self._spawned_excel_pids)
        if owned_xl_early and (was_timed_out or interrupted):
            self._terminate_pids_fn(owned_xl_early)

        if self._pythoncom is not None:
            self._pythoncom = None
            if should_wait_exit and self._spawned_pids:
                exited = _wait_pids_exit(self._spawned_pids, timeout_s=8.0)
                if not exited:
                    # Only ever terminate PIDs we spawned ourselves
                    self._terminate_pids_fn(self._spawned_pids)
                time.sleep(0.3)
            # Clean up any embedded Excel server we explicitly spawned (never touches pre-existing user Excel!)
            owned_xl = set(self._spawned_excel_pids)
            if owned_xl:
                xl_exited = _wait_pids_exit(owned_xl, timeout_s=2.0, image_name="EXCEL.EXE")
                if not xl_exited:
                    self._terminate_pids_fn(owned_xl)
        elif interrupted and self._owns_instance and self._spawned_pids:
            self._terminate_pids_fn(self._spawned_pids)

        if was_timed_out and exc_type is not RenderExecutionError and not interrupted:
            raise RenderExecutionError(
                self._timeout_message
                or f"PowerPoint COM session timed out after {self._timeout_s:g}s (owned PIDs: {sorted(self._spawned_pids)})"
            )


@contextmanager
def open_powerpoint_session(
    app_factory: Callable[[], Any] | None = None,
    *,
    timeout_s: float | None = None,
    step_timeout_s: float | None = None,
    terminate_pids_fn: Callable[[set[int]], None] | None = None,
    list_excel_pids_fn: Callable[[], set[int]] | None = None,
    list_powerpnt_pids_fn: Callable[[], set[int]] | None = None,
    allow_existing_powerpoint: bool | None = None,
) -> Iterator[PowerPointSession]:
    with PowerPointSession(
        app_factory=app_factory,
        timeout_s=timeout_s,
        step_timeout_s=step_timeout_s,
        terminate_pids_fn=terminate_pids_fn,
        list_excel_pids_fn=list_excel_pids_fn,
        list_powerpnt_pids_fn=list_powerpnt_pids_fn,
        allow_existing_powerpoint=allow_existing_powerpoint,
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
