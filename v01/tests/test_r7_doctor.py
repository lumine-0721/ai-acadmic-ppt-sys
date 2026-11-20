"""R7 / N1 tests: pptv01 doctor diagnostics, Office ClickToRun edition classification, and real Windows check."""

from __future__ import annotations

import json

import pytest

from pptv01 import cli
from pptv01.doctor import DoctorCheck, classify_office_edition, format_doctor_report, run_doctor


@pytest.mark.parametrize(
    "release_ids,expected",
    [
        ("O365ProPlusRetail", "Microsoft 365 订阅"),
        ("OneNoteFreeRetail,M365BusinessRetail", "Microsoft 365 订阅"),
        ("HomeStudent2021Retail", "Office 2021·LTSC"),
        ("ProPlus2021Volume", "Office 2021·LTSC"),
        ("StandardLTSCVolume", "Office 2021·LTSC"),
        ("ProPlus2019Retail", "其他"),
        ("", "未知"),
    ],
)
def test_n1_classify_office_edition(release_ids, expected):
    assert classify_office_edition(release_ids) == expected


def test_r7_doctor_reports_ready_when_required_checks_pass_and_optional_missing():
    probes = [
        lambda: DoctorCheck("windows_os", required=True, ok=True, detail="Windows 11"),
        lambda: DoctorCheck("python", required=True, ok=True, detail="Python 3.13.6"),
        lambda: DoctorCheck("pywin32", required=True, ok=True, detail="pywin32 312"),
        lambda: DoctorCheck("powerpoint_com", required=True, ok=True, detail="Microsoft PowerPoint 16.0 (Build 20527)"),
        lambda: DoctorCheck("excel_com", required=True, ok=True, detail="Microsoft Excel 16.0 (Build 20527.0)"),
        lambda: DoctorCheck("cjk_fonts", required=True, ok=True, detail="found CJK fonts: Microsoft YaHei"),
        lambda: DoctorCheck("soffice", required=False, ok=False, detail="not installed (optional)", fix_hint="Optional"),
    ]
    rep = run_doctor(probes=probes)
    assert rep.ok is True
    txt = format_doctor_report(rep)
    assert "READY" in txt and "[OK]" in txt and "[OPT]" in txt
    data = json.loads(format_doctor_report(rep, as_json=True))
    assert data["ok"] is True and len(data["checks"]) == 7


def test_r7_doctor_reports_not_ready_with_fix_hints_when_required_missing():
    probes = [
        lambda: DoctorCheck("windows_os", required=True, ok=True, detail="Windows 11"),
        lambda: DoctorCheck(
            "powerpoint_com",
            required=True,
            ok=False,
            detail="PowerPoint.Application unavailable",
            fix_hint="Install desktop Microsoft PowerPoint",
        ),
        lambda: DoctorCheck(
            "excel_com",
            required=True,
            ok=False,
            detail="Excel.Application unavailable",
            fix_hint="Install Microsoft Excel",
        ),
    ]
    rep = run_doctor(probes=probes)
    assert rep.ok is False
    txt = format_doctor_report(rep)
    assert "NOT READY" in txt
    assert "[FAIL]" in txt
    assert "Install desktop Microsoft PowerPoint" in txt
    assert "Install Microsoft Excel" in txt


def test_r7_cli_doctor_exit_code_reflects_report(monkeypatch, capsys):
    ok_rep = run_doctor(
        probes=[lambda: DoctorCheck("windows_os", required=True, ok=True, detail="Windows 11")]
    )
    monkeypatch.setattr(cli, "run_doctor", lambda: ok_rep)
    rc = cli.main(["doctor", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True

    bad_rep = run_doctor(
        probes=[lambda: DoctorCheck("powerpoint_com", required=True, ok=False, detail="missing", fix_hint="install")]
    )
    monkeypatch.setattr(cli, "run_doctor", lambda: bad_rep)
    rc2 = cli.main(["doctor"])
    assert rc2 == 1
    assert "NOT READY" in capsys.readouterr().out


@pytest.mark.windows_com
def test_r7_windows_real_doctor_all_required_green(capsys):
    """Run real `pptv01 doctor` on this Windows desktop machine and verify edition metadata + required checks pass."""
    rc = cli.main(["doctor", "--json"])
    captured = capsys.readouterr().out
    data = json.loads(captured)
    assert rc == 0, f"pptv01 doctor failed: {captured}"
    assert data["ok"] is True
    by_name = {c["name"]: c for c in data["checks"]}
    for req in ("windows_os", "python", "pywin32", "powerpoint_com", "excel_com", "cjk_fonts"):
        assert by_name[req]["ok"] is True, f"{req} failed: {by_name[req]}"
    assert "16.0" in by_name["powerpoint_com"]["detail"]
    assert "产品类型：" in by_name["powerpoint_com"]["detail"]
    meta = by_name["powerpoint_com"]["metadata"]
    assert meta["product_edition"] in ("Microsoft 365 订阅", "Office 2021·LTSC", "其他", "未知")
    assert "16.0" in by_name["excel_com"]["detail"]
