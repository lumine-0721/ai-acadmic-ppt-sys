"""N2 tests: PowerPoint COM session isolation (DispatchEx only, PID ownership, watchdog timeout)."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from pptv01.renderers.powerpoint_com import session as com_session
from pptv01.renderers.powerpoint_com.session import (
    _connect_powerpoint,
    open_powerpoint_session,
)
from pptv01.renderers.protocol import RenderExecutionError, RendererUnavailableError
from tests.fake_com import FakePowerPointApp


def test_n2_refuses_when_powerpoint_already_running_before_launch_without_calling_dispatchex(monkeypatch):
    """Round-3 N2 (a): if POWERPNT.EXE PID already exists before launch, raise clear error and NEVER call DispatchEx."""
    dispatchex_calls: list[str] = []
    terminated: list[set[int]] = []

    fake_pythoncom = SimpleNamespace(CoInitialize=lambda: None)
    fake_win32 = SimpleNamespace(
        DispatchEx=lambda progid: (dispatchex_calls.append(progid), FakePowerPointApp())[1]
    )
    monkeypatch.setattr(com_session, "_import_com_modules", lambda: (fake_pythoncom, fake_win32))
    monkeypatch.setattr(com_session, "_ensure_com_initialized", lambda _pc: None)

    with pytest.raises(RendererUnavailableError) as excinfo:
        with open_powerpoint_session(
            list_powerpnt_pids_fn=lambda: {23456},
            list_excel_pids_fn=lambda: set(),
            terminate_pids_fn=lambda pids: terminated.append(set(pids)),
        ):
            pass

    msg = str(excinfo.value)
    assert "检测到 PowerPoint 已在运行" in msg and "23456" in msg
    assert "请先保存并关闭所有 PowerPoint 窗口" in msg
    assert dispatchex_calls == []
    assert terminated == []


def test_n2_refuses_when_dispatchex_spawns_no_new_pid_without_touching_instance(monkeypatch):
    """Round-3 N2 (b): if DispatchEx produces no new PID (attached to existing instance), abort immediately
    without calling Presentations.Add, Quit, or taskkill."""
    dispatchex_calls: list[str] = []
    terminated: list[set[int]] = []
    app = FakePowerPointApp()
    app.DisplayAlerts = 2
    app.AutomationSecurity = 1
    add_called = False
    orig_add = app.Presentations.Add

    def tracked_add(*args, **kwargs):
        nonlocal add_called
        add_called = True
        return orig_add(*args, **kwargs)

    app.Presentations.Add = tracked_add  # type: ignore[method-assign]

    fake_pythoncom = SimpleNamespace(CoInitialize=lambda: None)
    fake_win32 = SimpleNamespace(
        DispatchEx=lambda progid: (dispatchex_calls.append(progid), app)[1]
    )
    monkeypatch.setattr(com_session, "_import_com_modules", lambda: (fake_pythoncom, fake_win32))
    monkeypatch.setattr(com_session, "_ensure_com_initialized", lambda _pc: None)

    # Pre-launch returns empty; post-launch ALSO returns empty -> no new PID spawned!
    with pytest.raises(RendererUnavailableError) as excinfo:
        with open_powerpoint_session(
            list_powerpnt_pids_fn=lambda: set(),
            list_excel_pids_fn=lambda: set(),
            terminate_pids_fn=lambda pids: terminated.append(set(pids)),
        ):
            pass

    msg = str(excinfo.value)
    assert "未检测到新进程 PID" in msg
    assert "请先保存并关闭所有 PowerPoint 窗口" in msg
    assert dispatchex_calls == ["PowerPoint.Application"]
    assert add_called is False
    assert app.quit_called is False
    assert app.DisplayAlerts == 2
    assert app.AutomationSecurity == 1
    assert terminated == []


def test_n2_app_factory_pre_and_post_pid_guards():
    """Verify the same pre-launch & post-launch PID guards hold on the app_factory test seam."""
    factory_calls = 0

    def factory():
        nonlocal factory_calls
        factory_calls += 1
        return FakePowerPointApp()

    # Pre-existing PID -> factory never called
    with pytest.raises(RendererUnavailableError, match="检测到 PowerPoint 已在运行"):
        with open_powerpoint_session(
            app_factory=factory,
            list_powerpnt_pids_fn=lambda: {9001},
            list_excel_pids_fn=lambda: set(),
        ):
            pass
    assert factory_calls == 0

    # No new PID after factory -> refused without touching Presentations/Quit
    created_app = FakePowerPointApp()
    with pytest.raises(RendererUnavailableError, match="未检测到新进程 PID"):
        with open_powerpoint_session(
            app_factory=lambda: created_app,
            list_powerpnt_pids_fn=lambda: set(),
            list_excel_pids_fn=lambda: set(),
        ):
            pass
    assert created_app.quit_called is False
    assert created_app.Presentations.Count == 0


def test_n2_connect_powerpoint_uses_dispatchex_never_getactiveobject():
    calls: list[tuple[str, str]] = []

    class FakeWin32:
        def GetActiveObject(self, progid: str):
            calls.append(("GetActiveObject", progid))
            raise AssertionError("GetActiveObject must never be called")

        def Dispatch(self, progid: str):
            calls.append(("Dispatch", progid))
            raise AssertionError("Dispatch must not be used instead of DispatchEx")

        def DispatchEx(self, progid: str):
            calls.append(("DispatchEx", progid))
            return SimpleNamespace(Version="16.0")

    app = _connect_powerpoint(FakeWin32())
    assert app.Version == "16.0"
    assert calls == [("DispatchEx", "PowerPoint.Application")]


def test_n2_preexisting_empty_powerpoint_is_never_quit():
    """With explicit allow_existing_powerpoint=True opt-in, an existing empty PowerPoint is still NEVER Quit()."""
    app = FakePowerPointApp()
    app.is_preexisting = True  # type: ignore[attr-defined]
    app.DisplayAlerts = 2
    app.AutomationSecurity = 1

    with open_powerpoint_session(
        app_factory=lambda: app,
        list_excel_pids_fn=lambda: set(),
        allow_existing_powerpoint=True,
    ) as sess:
        assert sess.owns_instance is False
        pres = sess.track_presentation(app.Presentations.Add(0))
        assert app.Presentations.Count == 1
        sess.close_presentation(pres)

    assert app.Presentations.Count == 0
    assert app.quit_called is False
    assert app.DisplayAlerts == 2
    assert app.AutomationSecurity == 1


def test_n2_preexisting_powerpoint_with_user_deck_left_untouched():
    """With explicit allow_existing_powerpoint=True opt-in, our session closes ONLY our deck and never Quits."""
    app = FakePowerPointApp()
    app.is_preexisting = True  # type: ignore[attr-defined]
    user_pres = app.Presentations.Add(-1)
    user_pres.Slides.Add(1, 12)
    assert app.Presentations.Count == 1

    with open_powerpoint_session(
        app_factory=lambda: app,
        list_excel_pids_fn=lambda: set(),
        allow_existing_powerpoint=True,
    ) as sess:
        assert sess.owns_instance is False
        ours = sess.track_presentation(app.Presentations.Add(0))
        assert app.Presentations.Count == 2
        sess.close_presentation(ours)

    assert app.Presentations.Count == 1
    assert user_pres.closed is False
    assert user_pres.Slides.Count == 1
    assert app.quit_called is False


def test_n2_owned_fresh_instance_calls_quit_cleanly():
    app = FakePowerPointApp()
    app.is_preexisting = False  # type: ignore[attr-defined]
    app.spawned_pids = {43210}  # type: ignore[attr-defined]

    with open_powerpoint_session(
        app_factory=lambda: app,
        list_excel_pids_fn=lambda: set(),
    ) as sess:
        assert sess.owns_instance is True
        assert sess.spawned_pids == {43210}
        ours = sess.track_presentation(app.Presentations.Add(0))
        sess.close_presentation(ours)

    assert app.quit_called is True


def test_n2_watchdog_timeout_kills_only_owned_spawned_pid_and_raises():
    app = FakePowerPointApp()
    app.is_preexisting = False  # type: ignore[attr-defined]
    app.spawned_pids = {77777}  # type: ignore[attr-defined]
    terminated: list[set[int]] = []

    with pytest.raises(RenderExecutionError, match="timed out"):
        with open_powerpoint_session(
            app_factory=lambda: app,
            timeout_s=0.12,
            list_excel_pids_fn=lambda: set(),
            terminate_pids_fn=lambda pids: terminated.append(set(pids)),
        ) as sess:
            # Simulate a hung COM call blocked by a modal dialog
            sess._timeout_event.wait(timeout=1.0)
            time.sleep(0.05)

    assert terminated == [{77777}]


def test_n2_watchdog_timeout_never_kills_preexisting_user_powerpoint():
    app = FakePowerPointApp()
    app.is_preexisting = True  # type: ignore[attr-defined]
    app.spawned_pids = set()  # type: ignore[attr-defined]
    terminated: list[set[int]] = []

    with pytest.raises(RenderExecutionError, match="timed out"):
        with open_powerpoint_session(
            app_factory=lambda: app,
            timeout_s=0.12,
            list_excel_pids_fn=lambda: {41916},
            terminate_pids_fn=lambda pids: terminated.append(set(pids)),
            allow_existing_powerpoint=True,
        ) as sess:
            sess._timeout_event.wait(timeout=1.0)
            time.sleep(0.05)

    assert terminated == []
    assert app.quit_called is False


def test_n2_per_step_watchdog_reports_step_elapsed_and_kills_only_spawned_ppt_and_excel():
    """Per-step timeout reports stuck step, elapsed time, and kills ONLY newly spawned PPT+Excel PIDs."""
    app = FakePowerPointApp()
    app.is_preexisting = False  # type: ignore[attr-defined]
    app.spawned_pids = {55501}  # type: ignore[attr-defined]
    terminated: list[set[int]] = []
    # User already had Excel PID 10001 open; our chart op spawned Excel PID 88802
    excel_state = [{10001}]

    with pytest.raises(RenderExecutionError) as excinfo:
        with open_powerpoint_session(
            app_factory=lambda: app,
            timeout_s=5.0,
            step_timeout_s=0.12,
            terminate_pids_fn=lambda pids: terminated.append(set(pids)),
            list_excel_pids_fn=lambda: set(excel_state[0]),
        ) as sess:
            with sess.step("build:add_chart:OP007"):
                excel_state[0] = {10001, 88802}
                sess._timeout_event.wait(timeout=1.5)
                time.sleep(0.05)

    msg = str(excinfo.value)
    assert "step='build:add_chart:OP007'" in msg
    assert "after waiting" in msg
    assert "55501" in msg and "88802" in msg
    # Pre-existing user Excel PID 10001 MUST NEVER be killed
    killed_union = set().union(*terminated) if terminated else set()
    assert 55501 in killed_union and 88802 in killed_union
    assert 10001 not in killed_union


def test_n2_keyboard_interrupt_cleans_only_owned_pids_and_leaves_user_deck_intact():
    app = FakePowerPointApp()
    app.is_preexisting = False  # type: ignore[attr-defined]
    app.spawned_pids = {66601}  # type: ignore[attr-defined]
    terminated: list[set[int]] = []

    with pytest.raises(KeyboardInterrupt):
        with open_powerpoint_session(
            app_factory=lambda: app,
            list_excel_pids_fn=lambda: set(),
            terminate_pids_fn=lambda pids: terminated.append(set(pids)),
        ) as sess:
            sess.track_presentation(app.Presentations.Add(0))
            raise KeyboardInterrupt()

    assert app.Presentations.Count == 0
    assert app.quit_called is True
    assert terminated == [{66601}]


def test_n2_fake_com_session_never_calls_real_tasklist(monkeypatch):
    """Round-4 N2: fake-COM sessions using app_factory must NEVER invoke real OS _list_pids / tasklist."""
    def _forbid_tasklist(*args, **kwargs):
        raise AssertionError("fake-COM test must never invoke real _list_pids / tasklist")

    monkeypatch.setattr(com_session, "_list_pids", _forbid_tasklist)
    app = FakePowerPointApp()
    app.is_preexisting = False  # type: ignore[attr-defined]
    app.spawned_pids = {12345}  # type: ignore[attr-defined]
    terminated: list[set[int]] = []

    with pytest.raises(RenderExecutionError, match="timed out"):
        with open_powerpoint_session(
            app_factory=lambda: app,
            timeout_s=0.10,
            terminate_pids_fn=lambda pids: terminated.append(set(pids)),
        ) as sess:
            sess._timeout_event.wait(timeout=1.0)
            time.sleep(0.05)

    assert terminated == [{12345}]


def test_n2_failed_excel_baseline_never_kills_later_excel_pids():
    """Round-4 N2: if Excel baseline enumeration fails in __enter__, NEVER treat baseline as empty set to kill later Excel PIDs."""
    app = FakePowerPointApp()
    app.is_preexisting = False  # type: ignore[attr-defined]
    app.spawned_pids = {55502}  # type: ignore[attr-defined]
    terminated: list[set[int]] = []
    calls = {"n": 0}

    def flaky_excel_lister() -> set[int]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("tasklist transient failure")
        # Later call observes a live user/unrelated Excel PID 41916
        return {41916}

    with pytest.raises(RenderExecutionError, match="timed out"):
        with open_powerpoint_session(
            app_factory=lambda: app,
            timeout_s=0.12,
            list_excel_pids_fn=flaky_excel_lister,
            terminate_pids_fn=lambda pids: terminated.append(set(pids)),
        ) as sess:
            sess._timeout_event.wait(timeout=1.0)
            time.sleep(0.05)

    killed_union = set().union(*terminated) if terminated else set()
    assert 55502 in killed_union
    assert 41916 not in killed_union


def test_n2_list_pids_retries_and_raises_on_tasklist_failure(monkeypatch):
    """Round-4 N2: _list_pids must retry transient errors and raise RuntimeError instead of returning empty set."""
    attempts = {"n": 0}

    def fake_run(*args, **kwargs):
        attempts["n"] += 1
        return SimpleNamespace(returncode=1, stdout="", stderr="ERROR: access denied")

    monkeypatch.setattr(com_session.sys, "platform", "win32")
    monkeypatch.setattr(com_session.subprocess, "run", fake_run)
    monkeypatch.setattr(com_session.time, "sleep", lambda _s: None)

    with pytest.raises(RuntimeError, match="failed to enumerate EXCEL.EXE PIDs"):
        com_session._list_pids("EXCEL.EXE", retries=3, retry_delay_s=0.0)
    assert attempts["n"] == 3

    # Succeeds on retry #2
    attempts["n"] = 0

    def fake_run_retry_ok(*args, **kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise OSError("temporary failure")
        return SimpleNamespace(returncode=0, stdout='"EXCEL.EXE","41916","Console","1","50,000 K"\n', stderr="")

    monkeypatch.setattr(com_session.subprocess, "run", fake_run_retry_ok)
    assert com_session._list_pids("EXCEL.EXE", retries=3, retry_delay_s=0.0) == {41916}
    assert attempts["n"] == 2


def test_n2_no_getactiveobject_or_getobject_in_production_source():
    """HANDOFF v3 §5 N2 acceptance: rg GetActiveObject must have 0 hits in src/pptv01/."""
    from pathlib import Path
    import re

    src_root = Path(__file__).resolve().parents[1] / "src" / "pptv01"
    bad_hits: list[str] = []
    for py in src_root.rglob("*.py"):
        text = py.read_text("utf-8", errors="ignore")
        for m in re.finditer(r"\b(?:GetActiveObject|GetObject)\s*\(", text):
            bad_hits.append(f"{py.relative_to(src_root)}:{m.group(0)}")
    assert bad_hits == [], f"forbidden COM attach call(s) found in production code: {bad_hits}"
