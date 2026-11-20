"""Deterministic hard gates (benchmark spec 6.1). No model, no score: files exist, contracts validate, constraints hold.

Gates for stages that do not exist yet are reported ``not_run`` (so the overall result is ``incomplete``),
never silently passed.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from pptv01 import textcheck
from pptv01.contracts import (
    CONTRACT_FILES, GateReport, GateResult, ProductionPlan, SlidePlan, load_contract,
)

STAGE_FILES = ["production_plan", "research_brief", "slide_plan", "asset_requirements", "asset_registry",
               "slide_asset_mapping", "page_design", "qa_report"]


def check_gates(run_dir: Path) -> GateReport:
    run_dir = Path(run_dir)
    results: list[GateResult] = []
    run_id = "unknown"
    loaded: dict[str, object] = {}
    for name in STAGE_FILES:
        f = run_dir / f"{name}.json"
        if not f.is_file():
            results.append(GateResult(gate=f"contract:{name}", status="not_run", detail="file absent (stage not run or not implemented)"))
            continue
        try:
            obj = load_contract(CONTRACT_FILES[name], f)
        except (ValidationError, ValueError) as e:
            results.append(GateResult(gate=f"contract:{name}", status="fail", detail=str(e)[:200]))
            continue
        loaded[name] = obj
        run_id = getattr(obj, "run_id", run_id)
        results.append(GateResult(gate=f"contract:{name}", status="pass"))
    # run ids agree across files
    ids = {getattr(o, "run_id") for o in loaded.values()}
    results.append(GateResult(gate="run_id_consistent", status="pass" if len(ids) <= 1 else "fail",
                              detail="" if len(ids) <= 1 else f"differing run_ids {sorted(ids)}"))
    # hard page constraint the user stated, checked on the plan
    plan, sp = loaded.get("production_plan"), loaded.get("slide_plan")
    if isinstance(plan, ProductionPlan) and isinstance(sp, SlidePlan):
        lim = textcheck.page_limit(plan.source_utterance)
        n = len(sp.pages)
        if lim is None:
            results.append(GateResult(gate="page_constraint", status="pass", detail="user stated no page limit"))
        else:
            kind, v = lim
            ok = n <= v if kind == "max" else n == v
            results.append(GateResult(gate="page_constraint", status="pass" if ok else "fail",
                                      detail=f"user {kind} {v} pages; slide plan has {n}"))
    else:
        results.append(GateResult(gate="page_constraint", status="not_run", detail="needs production_plan and slide_plan"))
    # asset registry honesty: acquired assets carry source + licence (also enforced by the contract; re-asserted here)
    reg = loaded.get("asset_registry")
    if reg is not None:
        bad = [a.asset_id for a in reg.assets if a.status.value == "acquired" and not (a.source_url and a.license_or_terms)]
        results.append(GateResult(gate="assets_have_source_and_license", status="fail" if bad else "pass",
                                  detail=f"missing source/licence: {bad}" if bad else ""))
    else:
        results.append(GateResult(gate="assets_have_source_and_license", status="not_run", detail="no asset_registry yet"))
    for g in ("pptx_opens", "native_content_not_screenshots"):
        results.append(GateResult(gate=g, status="not_run", detail="stages G/H not implemented in V0.1 so far"))
    st = {r.status for r in results}
    overall = "fail" if "fail" in st else ("incomplete" if "not_run" in st else "pass")
    return GateReport(run_id=run_id if run_id != "unknown" else "unknown-run", results=results, overall=overall)
