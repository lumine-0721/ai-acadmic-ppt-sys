"""Route an existing QA report to the module that owns the defect."""

from __future__ import annotations

ROUTES = {
    "text_overflow": "aprd_layout",
    "object_overlap": "aprd_layout",
    "density": "apc_split",
    "missing_citation": "seaa",
    "unreadable_figure": "seaa",
    "design_rule": "aprd",
    "render_failure": "backend",
}


def route_report(report: dict | None) -> dict:
    issues = []
    rerun = set()
    for issue in (report or {}).get("issues") or []:
        code = issue.get("code")
        target = ROUTES.get(code, "report_only")
        issues.append({
            "slide_id": issue.get("slide_id"),
            "code": code,
            "route": target,
            "rerun_all_evidence": False,
        })
        if target != "report_only":
            rerun.add(target)
    return {
        "issues": issues,
        "routes": sorted(rerun),
        "rerun_seaa_for_overflow": any(item["code"] == "text_overflow" and item["route"] == "seaa" for item in issues),
    }
