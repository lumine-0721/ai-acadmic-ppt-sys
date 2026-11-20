"""N2 tests: PowerPoint COM session isolation (DispatchEx only, PID ownership, watchdog timeout)."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from pptv01.renderers.powerpoint_com.session import (
    _connect_powerpoint,
    open_powerpoint_session,
)
from pptv01.renderers.protocol import RenderExecutionError
from tests.fake_com import FakePowerPointApp


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
    """User has an open PowerPoint with 0 open decks -> session must NOT call Quit()."""
    app = FakePowerPointApp()
    app.is_preexisting = True  # type: ignore[attr-defined]
    app.DisplayAlerts = 2
    app.AutomationSecurity = 1

    with open_powerpoint_session(app_factory=lambda: app) as sess:
        assert sess.owns_instance is False
        pres = sess.track_presentation(app.Presentations.Add(0))
        assert app.Presentations.Count == 1
        sess.close_presentation(pres)

    assert app.Presentations.Count == 0
    assert app.quit_called is False
    assert app.DisplayAlerts == 2
    assert app.AutomationSecurity == 1


def test_n2_preexisting_powerpoint_with_user_deck_left_untouched():
    """User has an open PowerPoint with 1 deck -> our session closes ONLY our own deck and never Quits."""
    app = FakePowerPointApp()
    app.is_preexisting = True  # type: ignore[attr-defined]
    user_pres = app.Presentations.Add(-1)
    user_pres.Slides.Add(1, 12)
    assert app.Presentations.Count == 1

    with open_powerpoint_session(app_factory=lambda: app) as sess:
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

    with open_powerpoint_session(app_factory=lambda: app) as sess:
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
            terminate_pids_fn=lambda pids: terminated.append(set(pids)),
        ) as sess:
            sess._timeout_event.wait(timeout=1.0)
            time.sleep(0.05)

    assert terminated == []
    assert app.quit_called is False
