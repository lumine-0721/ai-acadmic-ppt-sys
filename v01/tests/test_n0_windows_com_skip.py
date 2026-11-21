"""N0 tests: automatic skipping of @pytest.mark.windows_com tests when Windows/pywin32/PowerPoint is absent."""

from __future__ import annotations

import builtins
import sys
import types

from pptv01.renderers import PowerPointComRenderer, RendererUnavailableError
from tests.conftest import check_windows_com_skip_reason


def test_n0_windows_com_skips_on_non_windows():
    reason = check_windows_com_skip_reason(platform_name="linux")
    assert reason is not None
    assert "requires Windows desktop" in reason and "linux" in reason


def test_n0_windows_com_skips_when_pywin32_missing(monkeypatch):
    for mod in ("pythoncom", "win32com", "win32com.client"):
        monkeypatch.delitem(sys.modules, mod, raising=False)
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name in ("pythoncom", "win32com", "win32com.client"):
            raise ImportError(f"No module named '{name}'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    reason = check_windows_com_skip_reason(platform_name="win32")
    assert reason is not None
    assert "pywin32 not installed" in reason


def test_n0_windows_com_skips_when_powerpoint_unavailable(monkeypatch):
    """Inject fake pywin32 modules so this test exercises the PowerPoint preflight branch even on Linux without pywin32."""
    fake_pythoncom = types.ModuleType("pythoncom")
    fake_win32com = types.ModuleType("win32com")
    fake_win32com_client = types.ModuleType("win32com.client")
    fake_win32com.client = fake_win32com_client  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "pythoncom", fake_pythoncom)
    monkeypatch.setitem(sys.modules, "win32com", fake_win32com)
    monkeypatch.setitem(sys.modules, "win32com.client", fake_win32com_client)
    monkeypatch.setattr(
        PowerPointComRenderer,
        "preflight",
        lambda self: (_ for _ in ()).throw(RendererUnavailableError("PowerPoint.Application not registered")),
    )
    reason = check_windows_com_skip_reason(platform_name="win32")
    assert reason is not None
    assert "PowerPoint COM unavailable" in reason
