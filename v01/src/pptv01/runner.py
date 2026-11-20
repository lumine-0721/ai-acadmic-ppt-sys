"""Linear runner: executes the implemented stages, reports the rest honestly.

Implemented now: A (production plan), B (provisional slide plan).
C-H are reported as ``not_implemented`` — never faked, never mocked.
"""

from __future__ import annotations

import json
import platform
import re
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from pptv01 import __version__
from pptv01.a_plan import ARTIFACT as A_FILE, run_stage_a
from pptv01.c_asset_requirements import ARTIFACT as C_FILE, run_stage_c
from pptv01.b_slide_plan import ARTIFACT as B_FILE, RESEARCH_ARTIFACT, ResearchFailed, run_stage_b
from pptv01.contracts import (
    AssetRegistry,
    AssetRequirements,
    PageDesign,
    ProductionPlan,
    ResearchRecord,
    SlidePlan,
    RunOverall,
    RunReport,
    SlideAssetMapping,
    StageRecord,
    StageStatus,
    UserInputRequests,
    UserInputRequestsSummary,
    load_contract,
    write_contract,
)
from pptv01.d_retrieve import REGISTRY_FILE, AssetRetrievalFailed, DServices, run_stage_d, services_from_env
from pptv01.e_mapping import ARTIFACT as E_FILE, run_stage_e
from pptv01.f_layout import ARTIFACT as F_FILE, run_stage_f
from pptv01.g_pptx import ARTIFACT as G_FILE, run_stage_g
from pptv01.llm import ChatClient, LLMConfigError, LLMError
from pptv01.retrieval import ResearchProvider, research_from_env
from pptv01.user_requests import USER_INPUT_REQUESTS_FILE, summarize_requests

STAGE_NAMES = {
    "A": "production_plan",
    "B": "provisional_slide_plan",
    "C": "asset_requirements",
    "D": "asset_retrieval",
    "E": "slide_asset_mapping",
    "F": "page_layout_design",
    "G": "pptx_generation",
    "H": "render_qa_repair",
}
IMPLEMENTED_STAGES = ("A", "B", "C", "D", "E", "F", "G")


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def new_run_id(now: datetime | None = None) -> str:
    now = now or _now()
    return f"run-{now:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


def code_version() -> tuple[str, bool | None]:
    """(git commit of the code that ran, dirty flag). 'unknown' outside a git checkout."""
    here = Path(__file__).resolve().parent
    try:
        commit = subprocess.run(
            ["git", "-C", str(here), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10, check=True
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "-C", str(here), "status", "--porcelain", "--", str(here.parent.parent)],
                capture_output=True, text=True, timeout=10, check=True,
            ).stdout.strip()
        )
        return commit, dirty
    except (OSError, subprocess.SubprocessError):
        return "unknown", None


def _research_summary(rec: ResearchRecord) -> dict[str, Any]:
    return {
        "provider": rec.provider, "status": rec.status.value, "notes": len(rec.notes),
        "filtered": len(rec.filtered), "queries": len(rec.queries), "query_errors": sum(q.status.value == "error" for q in rec.queries),
        "sources": [{"id": n.note_id, "title": n.title, "url": n.source_url,
                     "retrieved_at": n.retrieved_at.isoformat()} for n in rec.notes],
        "summary": rec.summary,
    }


def _assets_summary(reg: AssetRegistry) -> dict[str, Any]:
    rf = reg.relevance_filter
    return {
        "summary": reg.summary,
        "acquired": [{"id": a.asset_id, "type": a.type.value if a.type else None, "source": a.source_name,
                      "license": a.license_or_terms, "identifier": a.identifier} for a in reg.assets if a.status.value == "acquired"],
        "missing": [a.asset_id for a in reg.assets if a.status.value == "missing"],
        "source_calls": len(reg.sources), "source_errors": sum(c.status == "error" for c in reg.sources),
        "relevance_filter": None if rf is None else {"scorer": rf.scorer, "threshold": rf.threshold, "note": rf.note},
    }


def _write_trace(run_dir: Path, stage: str, attempts: list[dict[str, Any]], model_id: str) -> str:
    rel = f"llm_trace/{stage}.json"
    p = run_dir / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"stage": stage, "model_id": model_id, "attempts": attempts}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return rel


def run_pipeline(
    utterance: str,
    out_root: Path,
    client: ChatClient,
    *,
    run_id: str | None = None,
    resume: bool = False,
    on_missing: Literal["placeholder", "wait"] = "placeholder",
    research: ResearchProvider | None = None,
    assets: DServices | None = None,
    vision_client: ChatClient | None = None,
    clock: Callable[[], datetime] = _now,
) -> tuple[RunReport, Path]:
    """Run implemented stages for one utterance. Returns (report, run_dir).

    ``client`` is mandatory: a real run builds it from env (cli); there is no
    default and no fallback client in this package.
    """
    utterance = utterance.strip()
    run_id = run_id or new_run_id(clock())
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", run_id):
        raise ValueError(f"invalid run_id {run_id!r}")
    run_dir = Path(out_root) / run_id
    if run_dir.exists() and not resume:
        raise FileExistsError(f"run directory already exists: {run_dir} (use resume only for the agent backend)")
    run_dir.mkdir(parents=True, exist_ok=resume)

    started = clock()
    records: dict[str, StageRecord] = {}
    plan: ProductionPlan | None = None
    slide_plan: SlidePlan | None = None
    reqs: AssetRequirements | None = None
    notes: dict[str, Any] = {}
    failure: str | None = None
    defaults_summary: list[str] = []

    def record(stage: str, status: StageStatus, t0: datetime | None, files: list[str], error: str | None = None) -> None:
        records[stage] = StageRecord(
            stage=stage, name=STAGE_NAMES[stage], status=status,
            started_at=t0, finished_at=clock() if t0 else None, output_files=files, error=error,
        )

    # ---- Stage A
    t0 = clock()
    if resume and (run_dir / A_FILE).is_file():
        plan = load_contract(ProductionPlan, run_dir / A_FILE)
        files = [A_FILE] + (["llm_trace/A.json"] if (run_dir / "llm_trace/A.json").is_file() else [])
        record("A", StageStatus.done, t0, files)
        defaults_summary = [f"{d.field}={d.value}: {d.rationale}" for d in plan.defaults_applied]
    else:
        client.set_stage("A")
        try:
            plan, attempts = run_stage_a(utterance, run_id, client, run_dir)
            record("A", StageStatus.done, t0, [A_FILE, _write_trace(run_dir, "A", attempts, client.model_id)])
            defaults_summary = [f"{d.field}={d.value}: {d.rationale}" for d in plan.defaults_applied]
        except (LLMError, ValueError) as e:
            trace = getattr(e, "attempts", None)
            files = [_write_trace(run_dir, "A", trace, client.model_id)] if trace else []
            failure = f"stage A failed: {type(e).__name__}: {str(e)[:500]}"
            record("A", StageStatus.failed, t0, files, failure)

    # ---- Stage B (research per A's retrieval_brief, then provisional slide plan)
    research_summary: dict[str, Any] = {}
    if plan is not None:
        t0 = clock()
        if resume and (run_dir / B_FILE).is_file() and (run_dir / RESEARCH_ARTIFACT).is_file():
            rec = load_contract(ResearchRecord, run_dir / RESEARCH_ARTIFACT)
            slide_plan = load_contract(SlidePlan, run_dir / B_FILE)
            notes = {n.note_id: n for n in rec.notes}
            research_summary = _research_summary(rec)
            files = [RESEARCH_ARTIFACT, B_FILE] + (["llm_trace/B.json"] if (run_dir / "llm_trace/B.json").is_file() else [])
            record("B", StageStatus.done, t0, files)
        else:
            client.set_stage("B")
            try:
                provider = research if research is not None else research_from_env()
                slide_plan, attempts, rec = run_stage_b(plan, client, run_dir, provider, reuse_research=resume)
                notes = {n.note_id: n for n in rec.notes}
                research_summary = _research_summary(rec)
                record("B", StageStatus.done, t0,
                       [RESEARCH_ARTIFACT, B_FILE, _write_trace(run_dir, "B", attempts, client.model_id)])
            except ResearchFailed as e:
                research_summary = _research_summary(e.record)
                failure = f"stage B failed: {e}"[:600]
                record("B", StageStatus.failed, t0, [RESEARCH_ARTIFACT], failure)
            except (LLMError, ValueError) as e:
                trace = getattr(e, "attempts", None)
                files = [RESEARCH_ARTIFACT] if (run_dir / RESEARCH_ARTIFACT).exists() else []
                if (run_dir / RESEARCH_ARTIFACT).exists():
                    research_summary = _research_summary(load_contract(ResearchRecord, run_dir / RESEARCH_ARTIFACT))
                if trace:
                    files.append(_write_trace(run_dir, "B", trace, client.model_id))
                failure = f"stage B failed: {type(e).__name__}: {str(e)[:500]}"
                record("B", StageStatus.failed, t0, files, failure)
    else:
        records["B"] = StageRecord(stage="B", name=STAGE_NAMES["B"], status=StageStatus.skipped,
                                   error="skipped: stage A failed")

    # ---- Stage C (asset requirements from the provisional slide plan)
    if slide_plan is not None:
        t0 = clock()
        if resume and (run_dir / C_FILE).is_file():
            reqs = load_contract(AssetRequirements, run_dir / C_FILE)
            files = [C_FILE] + (["llm_trace/C.json"] if (run_dir / "llm_trace/C.json").is_file() else [])
            record("C", StageStatus.done, t0, files)
        else:
            client.set_stage("C")
            try:
                reqs, attempts = run_stage_c(plan, slide_plan, client, run_dir, notes)
                record("C", StageStatus.done, t0, [C_FILE, _write_trace(run_dir, "C", attempts, client.model_id)])
            except (LLMError, ValueError) as e:
                trace = getattr(e, "attempts", None)
                files = [_write_trace(run_dir, "C", trace, client.model_id)] if trace else []
                failure = f"stage C failed: {type(e).__name__}: {str(e)[:500]}"
                record("C", StageStatus.failed, t0, files, failure)
    else:
        records["C"] = StageRecord(stage="C", name=STAGE_NAMES["C"], status=StageStatus.skipped,
                                   error="skipped: stage B did not produce a slide plan")

    # ---- Stage D (asset registry: real retrieval; no LLM involved)
    assets_summary: dict[str, Any] = {}
    missing_assets: list[str] = []
    reg: AssetRegistry | None = None
    uir: UserInputRequests | None = None
    if reqs is not None:
        t0 = clock()
        if resume and (run_dir / REGISTRY_FILE).is_file() and (run_dir / USER_INPUT_REQUESTS_FILE).is_file():
            reg = load_contract(AssetRegistry, run_dir / REGISTRY_FILE)
            uir = load_contract(UserInputRequests, run_dir / USER_INPUT_REQUESTS_FILE)
            missing_assets = [a.asset_id for a in reg.assets if a.status.value == "missing"]
            assets_summary = _assets_summary(reg)
            d_files = [REGISTRY_FILE, USER_INPUT_REQUESTS_FILE] + sorted(
                str(p.relative_to(run_dir)).replace("\\", "/")
                for p in (run_dir / "assets").glob("*") if p.is_file()
            )
            record("D", StageStatus.done, t0, d_files)
        else:
            svc = None
            try:
                svc = assets if assets is not None else services_from_env()
                reg = run_stage_d(plan, slide_plan, reqs, run_dir, svc, on_missing=on_missing)
                uir = load_contract(UserInputRequests, run_dir / USER_INPUT_REQUESTS_FILE)
                missing_assets = [a.asset_id for a in reg.assets if a.status.value == "missing"]
                assets_summary = _assets_summary(reg)
                d_files = [REGISTRY_FILE, USER_INPUT_REQUESTS_FILE] + sorted(
                    str(p.relative_to(run_dir)).replace("\\", "/")
                    for p in (run_dir / "assets").glob("*") if p.is_file()
                )
                record("D", StageStatus.done, t0, d_files)
            except AssetRetrievalFailed as e:
                assets_summary = _assets_summary(e.registry)
                missing_assets = [a.asset_id for a in e.registry.assets]
                if (run_dir / USER_INPUT_REQUESTS_FILE).is_file():
                    uir = load_contract(UserInputRequests, run_dir / USER_INPUT_REQUESTS_FILE)
                failure = f"stage D failed: {e}"[:600]
                d_err_files = [REGISTRY_FILE] + ([USER_INPUT_REQUESTS_FILE] if uir is not None else [])
                record("D", StageStatus.failed, t0, d_err_files, failure)
            except (LLMConfigError, ValueError) as e:
                failure = f"stage D failed: {type(e).__name__}: {str(e)[:500]}"
                record("D", StageStatus.failed, t0, [], failure)
            finally:
                if svc is not None and assets is None:
                    svc.close()
    else:
        records["D"] = StageRecord(stage="D", name=STAGE_NAMES["D"], status=StageStatus.skipped,
                                   error="skipped: stage C did not produce asset requirements")

    uir_summary: UserInputRequestsSummary | None = summarize_requests(uir) if uir is not None else None
    waiting_for_input = bool(
        not failure and on_missing == "wait" and uir_summary is not None and uir_summary.open_count > 0
    )

    # ---- Stage E (slide asset mapping)
    mapping: SlideAssetMapping | None = None
    if waiting_for_input:
        records["E"] = StageRecord(
            stage="E", name=STAGE_NAMES["E"], status=StageStatus.skipped,
            error="skipped: waiting for user input (see user_input_requests.json)",
        )
    elif reg is not None and slide_plan is not None and plan is not None and not failure:
        t0 = clock()
        if resume and (run_dir / E_FILE).is_file():
            mapping = load_contract(SlideAssetMapping, run_dir / E_FILE)
            e_files = [E_FILE] + (["llm_trace/E.json"] if (run_dir / "llm_trace/E.json").is_file() else [])
            record("E", StageStatus.done, t0, e_files)
        else:
            client.set_stage("E")
            try:
                mapping, attempts = run_stage_e(
                    plan, slide_plan, reqs, reg, uir, client, run_dir, vision_client=vision_client
                )
                e_files = [E_FILE] + ([_write_trace(run_dir, "E", attempts, client.model_id)] if attempts else [])
                record("E", StageStatus.done, t0, e_files)
            except (LLMError, LLMConfigError, ValueError) as e:
                trace = getattr(e, "attempts", None)
                e_files = [_write_trace(run_dir, "E", trace, client.model_id)] if trace else []
                failure = f"stage E failed: {type(e).__name__}: {str(e)[:500]}"
                record("E", StageStatus.failed, t0, e_files, failure)
    else:
        records["E"] = StageRecord(
            stage="E", name=STAGE_NAMES["E"], status=StageStatus.skipped,
            error="skipped: stage D did not produce an asset registry",
        )

    # ---- Stage F (page layout design + schematic previews)
    design: PageDesign | None = None
    if waiting_for_input:
        records["F"] = StageRecord(
            stage="F", name=STAGE_NAMES["F"], status=StageStatus.skipped,
            error="skipped: waiting for user input (see user_input_requests.json)",
        )
    elif mapping is not None and reg is not None and slide_plan is not None and plan is not None and not failure:
        t0 = clock()
        if resume and (run_dir / F_FILE).is_file():
            design = load_contract(PageDesign, run_dir / F_FILE)
            f_files = [F_FILE] + sorted(
                p.relative_to(run_dir).as_posix()
                for p in (run_dir / "previews").glob("*.png") if p.is_file()
            )
            record("F", StageStatus.done, t0, f_files)
        else:
            try:
                design = run_stage_f(plan, slide_plan, reg, uir, mapping, run_dir)
                f_files = [F_FILE] + sorted(
                    p.relative_to(run_dir).as_posix()
                    for p in (run_dir / "previews").glob("*.png") if p.is_file()
                )
                record("F", StageStatus.done, t0, f_files)
            except ValueError as e:
                failure = f"stage F failed: {type(e).__name__}: {str(e)[:500]}"
                record("F", StageStatus.failed, t0, [], failure)
    else:
        records["F"] = StageRecord(
            stage="F", name=STAGE_NAMES["F"], status=StageStatus.skipped,
            error="skipped: stage E did not produce a slide asset mapping",
        )

    # ---- Stage G (python-pptx generation of deck.pptx)
    deck_path: Path | None = None
    if waiting_for_input:
        records["G"] = StageRecord(
            stage="G", name=STAGE_NAMES["G"], status=StageStatus.skipped,
            error="skipped: waiting for user input (see user_input_requests.json)",
        )
    elif design is not None and reg is not None and slide_plan is not None and not failure:
        t0 = clock()
        if resume and (run_dir / G_FILE).is_file():
            deck_path = run_dir / G_FILE
            record("G", StageStatus.done, t0, [G_FILE])
        else:
            try:
                deck_path = run_stage_g(slide_plan, design, reg, uir, run_dir)
                if (run_dir / USER_INPUT_REQUESTS_FILE).is_file():
                    uir = load_contract(UserInputRequests, run_dir / USER_INPUT_REQUESTS_FILE)
                    uir_summary = summarize_requests(uir)
                record("G", StageStatus.done, t0, [G_FILE])
            except (OSError, ValueError) as e:
                failure = f"stage G failed: {type(e).__name__}: {str(e)[:500]}"
                record("G", StageStatus.failed, t0, [], failure)
    else:
        records["G"] = StageRecord(
            stage="G", name=STAGE_NAMES["G"], status=StageStatus.skipped,
            error="skipped: stage F did not produce page_design.json",
        )

    for code in "H":
        if waiting_for_input:
            records[code] = StageRecord(
                stage=code, name=STAGE_NAMES[code], status=StageStatus.skipped,
                error="skipped: waiting for user input (see user_input_requests.json)",
            )
        else:
            records[code] = StageRecord(stage=code, name=STAGE_NAMES[code], status=StageStatus.not_implemented)

    stages = [records[c] for c in "ABCDEFGH"]
    if failure:
        overall = RunOverall.failed
    elif waiting_for_input:
        overall = RunOverall.needs_input
    elif all(s.status is StageStatus.done for s in stages):
        if uir_summary and (uir_summary.open_count > 0 or uir_summary.placeholder_count > 0):
            overall = RunOverall.complete_with_placeholders
        else:
            overall = RunOverall.complete
    else:
        overall = RunOverall.incomplete

    commit, dirty = code_version()
    report = RunReport(
        run_id=run_id,
        source_utterance=utterance,
        overall=overall,
        started_at=started,
        finished_at=clock(),
        model_id=client.model_id,
        llm_backend=client.describe().get("backend"),
        code_commit=commit,
        code_dirty=dirty,
        tool_config={
            "pptv01_version": __version__,
            "python": platform.python_version(),
            "llm": client.describe(),
            "implemented_stages": list(IMPLEMENTED_STAGES),
            "research": research_summary or {"status": "not_run"},
            "assets": assets_summary or {"status": "not_run"},
        },
        stages=stages,
        defaults_summary=defaults_summary,
        missing_assets=missing_assets,
        user_input_requests=uir_summary,
        revision_count=0,
        failure_reason=failure,
    )
    write_contract(report, run_dir / "run_report.json")
    return report, run_dir
