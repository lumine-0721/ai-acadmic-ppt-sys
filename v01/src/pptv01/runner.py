"""Linear runner: executes the implemented stages, reports the rest honestly.

Implemented now: A (production plan), B (provisional slide plan).
C-H are reported as ``not_implemented`` — never faked, never mocked.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from pptv01 import __version__
from pptv01.a_plan import ARTIFACT as A_FILE, run_stage_a
from pptv01.bivia_judge import BiviaJudge, bivia_session, bivia_stage_substep_status, new_bivia_stats
from pptv01.c_asset_requirements import ARTIFACT as C_FILE, run_stage_c
from pptv01.b_slide_plan import ARTIFACT as B_FILE, RESEARCH_ARTIFACT, ResearchFailed, run_stage_b
from pptv01.contracts import (
    AssetRegistry,
    AssetRequirements,
    DeckOpKind,
    DeckOps,
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
from pptv01.g_build import ARTIFACT_OPS, OpsCompileError, compile_deck_ops
from pptv01.g_pptx import ARTIFACT as G_FILE, run_stage_g
from pptv01.h_qa import ARTIFACT as H_FILE, RenderExecutionError, RendererUnavailableError, run_stage_h
from pptv01.h_qa.stage import RenderFn
from pptv01.llm import ChatClient, LLMConfigError, LLMError
from pptv01.renderers import (
    DeckRenderer,
    RenderExecutionError as DeckRenderExecutionError,
    RendererUnavailableError as DeckRendererUnavailableError,
)
from pptv01.retrieval import ResearchProvider, research_from_env
from pptv01.user_requests import USER_INPUT_REQUESTS_FILE, mark_placeholders_rendered, summarize_requests

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
IMPLEMENTED_STAGES = ("A", "B", "C", "D", "E", "F", "G", "H")


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


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rel_to_run_dir(path: Path, run_dir: Path) -> str:
    """Return a POSIX path relative to ``run_dir`` even when one side is relative and the other is resolved."""
    return path.resolve().relative_to(run_dir.resolve()).as_posix()


def _input_hashes(run_dir: Path, rel_paths: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for rel in sorted(set(rel_paths)):
        p = run_dir / rel
        if p.is_file():
            out[rel] = _file_sha256(p)
    return out


def _provided_input_files(uir: UserInputRequests | None) -> list[str]:
    if uir is None:
        return []
    return sorted(
        r.provided.file
        for r in uir.requests
        if r.provided is not None and r.provided.file
    )


def _write_trace(run_dir: Path, stage: str, attempts: list[dict[str, Any]], model_id: str) -> str:
    rel = f"llm_trace/{stage}.json"
    p = run_dir / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"stage": stage, "model_id": model_id, "attempts": attempts}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return rel


def _bivia_tool_config(
    bivia: BiviaJudge | None,
    c_stats: dict[str, int],
    e_stats: dict[str, int],
    h_stats: dict[str, int],
) -> dict[str, Any]:
    if bivia is None:
        return {"enabled": False, "status": "not_run"}
    combined = {k: c_stats.get(k, 0) + e_stats.get(k, 0) + h_stats.get(k, 0) for k in new_bivia_stats()}
    total = sum(combined.values())
    status = "not_run" if (total > 0 and combined["not_run"] == total) else "active"
    return {
        "enabled": True,
        "status": status,
        "threshold": bivia.threshold,
        "counts": combined,
        "stages": {"C": c_stats, "E": e_stats, "H": h_stats},
    }


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
    bivia: BiviaJudge | None = None,
    render_fn: RenderFn | None = None,
    renderer: DeckRenderer | None = None,
    compile_only: bool = False,
    cross_render: bool = False,
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

    with bivia_session(bivia) as eff_bivia:
        return _run_stages(
            utterance, run_dir, run_id, client, resume=resume, on_missing=on_missing,
            research=research, assets=assets, vision_client=vision_client, eff_bivia=eff_bivia,
            render_fn=render_fn, renderer=renderer, compile_only=compile_only,
            cross_render=cross_render, clock=clock,
        )


def _run_stages(
    utterance: str, run_dir: Path, run_id: str, client: ChatClient, *,
    resume: bool, on_missing: Literal["placeholder", "wait"], research: ResearchProvider | None,
    assets: DServices | None, vision_client: ChatClient | None, eff_bivia: BiviaJudge | None,
    render_fn: RenderFn | None, renderer: DeckRenderer | None, compile_only: bool,
    cross_render: bool, clock: Callable[[], datetime],
) -> tuple[RunReport, Path]:
    c_bivia_stats = new_bivia_stats()
    e_bivia_stats = new_bivia_stats()
    h_bivia_stats = new_bivia_stats()

    started = clock()
    records: dict[str, StageRecord] = {}
    plan: ProductionPlan | None = None
    slide_plan: SlidePlan | None = None
    reqs: AssetRequirements | None = None
    notes: dict[str, Any] = {}
    failure: str | None = None
    defaults_summary: list[str] = []

    prev_records: dict[str, StageRecord] = {}
    if resume and (run_dir / "run_report.json").is_file():
        try:
            prev_rep = load_contract(RunReport, run_dir / "run_report.json")
            prev_records = {s.stage: s for s in prev_rep.stages}
        except Exception:
            prev_records = {}

    invalidated = False

    def _can_reuse(stage: str, req_files: list[str], current_inputs: dict[str, str]) -> bool:
        if not resume or invalidated:
            return False
        if not all((run_dir / rel).is_file() for rel in req_files):
            return False
        prev = prev_records.get(stage)
        if prev is not None:
            if prev.stale or prev.status is not StageStatus.done:
                return False
            if prev.input_hashes != current_inputs:
                return False
        return True

    def _log_stage(msg: str) -> None:
        print(f"[pptv01] {msg}", flush=True)

    def record(
        stage: str,
        status: StageStatus,
        t0: datetime | None,
        files: list[str],
        error: str | None = None,
        *,
        input_hashes: dict[str, str] | None = None,
        substeps: dict[str, str] | None = None,
    ) -> None:
        finished = clock() if t0 else None
        records[stage] = StageRecord(
            stage=stage, name=STAGE_NAMES[stage], status=status,
            started_at=t0, finished_at=finished, output_files=files,
            substeps=dict(substeps or {}),
            input_hashes=dict(input_hashes or {}), stale=False, stale_reason=None, error=error,
        )
        elapsed_s = (finished - t0).total_seconds() if (t0 and finished) else 0.0
        _log_stage(f"stage {stage} ({STAGE_NAMES[stage]}) {status.value} elapsed={elapsed_s:.2f}s")

    # ---- Stage A
    _log_stage("stage A (production_plan) start")
    t0 = clock()
    a_inputs = {"utterance_sha256": hashlib.sha256(utterance.encode("utf-8")).hexdigest()}
    if _can_reuse("A", [A_FILE], a_inputs):
        plan = load_contract(ProductionPlan, run_dir / A_FILE)
        files = [A_FILE] + (["llm_trace/A.json"] if (run_dir / "llm_trace/A.json").is_file() else [])
        record("A", StageStatus.done, t0, files, input_hashes=a_inputs)
        defaults_summary = [f"{d.field}={d.value}: {d.rationale}" for d in plan.defaults_applied]
    else:
        invalidated = True
        client.set_stage("A")
        try:
            plan, attempts = run_stage_a(utterance, run_id, client, run_dir)
            record("A", StageStatus.done, t0, [A_FILE, _write_trace(run_dir, "A", attempts, client.model_id)],
                   input_hashes=a_inputs)
            defaults_summary = [f"{d.field}={d.value}: {d.rationale}" for d in plan.defaults_applied]
        except (LLMError, ValueError) as e:
            trace = getattr(e, "attempts", None)
            files = [_write_trace(run_dir, "A", trace, client.model_id)] if trace else []
            failure = f"stage A failed: {type(e).__name__}: {str(e)[:500]}"
            record("A", StageStatus.failed, t0, files, failure, input_hashes=a_inputs)

    # ---- Stage B (research per A's retrieval_brief, then provisional slide plan)
    research_summary: dict[str, Any] = {}
    if plan is not None:
        _log_stage("stage B (provisional_slide_plan) start")
        t0 = clock()
        b_inputs = _input_hashes(run_dir, [A_FILE])
        if _can_reuse("B", [B_FILE, RESEARCH_ARTIFACT], b_inputs):
            rec = load_contract(ResearchRecord, run_dir / RESEARCH_ARTIFACT)
            slide_plan = load_contract(SlidePlan, run_dir / B_FILE)
            notes = {n.note_id: n for n in rec.notes}
            research_summary = _research_summary(rec)
            files = [RESEARCH_ARTIFACT, B_FILE] + (["llm_trace/B.json"] if (run_dir / "llm_trace/B.json").is_file() else [])
            record("B", StageStatus.done, t0, files, input_hashes=b_inputs)
        else:
            invalidated = True
            client.set_stage("B")
            try:
                provider = research if research is not None else research_from_env()
                slide_plan, attempts, rec = run_stage_b(plan, client, run_dir, provider, reuse_research=resume)
                notes = {n.note_id: n for n in rec.notes}
                research_summary = _research_summary(rec)
                record("B", StageStatus.done, t0,
                       [RESEARCH_ARTIFACT, B_FILE, _write_trace(run_dir, "B", attempts, client.model_id)],
                       input_hashes=b_inputs)
            except ResearchFailed as e:
                research_summary = _research_summary(e.record)
                failure = f"stage B failed: {e}"[:600]
                record("B", StageStatus.failed, t0, [RESEARCH_ARTIFACT], failure, input_hashes=b_inputs)
            except (LLMError, ValueError) as e:
                trace = getattr(e, "attempts", None)
                files = [RESEARCH_ARTIFACT] if (run_dir / RESEARCH_ARTIFACT).exists() else []
                if (run_dir / RESEARCH_ARTIFACT).exists():
                    research_summary = _research_summary(load_contract(ResearchRecord, run_dir / RESEARCH_ARTIFACT))
                if trace:
                    files.append(_write_trace(run_dir, "B", trace, client.model_id))
                failure = f"stage B failed: {type(e).__name__}: {str(e)[:500]}"
                record("B", StageStatus.failed, t0, files, failure, input_hashes=b_inputs)
    else:
        records["B"] = StageRecord(stage="B", name=STAGE_NAMES["B"], status=StageStatus.skipped,
                                   error="skipped: stage A failed")

    # ---- Stage C (asset requirements from the provisional slide plan)
    if slide_plan is not None:
        _log_stage("stage C (asset_requirements) start")
        t0 = clock()
        c_inputs = _input_hashes(run_dir, [A_FILE, RESEARCH_ARTIFACT, B_FILE])
        if _can_reuse("C", [C_FILE], c_inputs):
            reqs = load_contract(AssetRequirements, run_dir / C_FILE)
            files = [C_FILE] + (["llm_trace/C.json"] if (run_dir / "llm_trace/C.json").is_file() else [])
            record("C", StageStatus.done, t0, files, input_hashes=c_inputs)
        else:
            invalidated = True
            client.set_stage("C")
            try:
                c_bivia_stats = new_bivia_stats()
                reqs, attempts = run_stage_c(
                    plan,
                    slide_plan,
                    client,
                    run_dir,
                    notes,
                    bivia=eff_bivia,
                    bivia_stats_out=c_bivia_stats,
                )
                c_substeps = (
                    {"bivia_judge": bivia_stage_substep_status(c_bivia_stats)}
                    if eff_bivia is not None
                    else None
                )
                record(
                    "C",
                    StageStatus.done,
                    t0,
                    [C_FILE, _write_trace(run_dir, "C", attempts, client.model_id)],
                    input_hashes=c_inputs,
                    substeps=c_substeps,
                )
            except (LLMError, ValueError) as e:
                trace = getattr(e, "attempts", None)
                files = [_write_trace(run_dir, "C", trace, client.model_id)] if trace else []
                failure = f"stage C failed: {type(e).__name__}: {str(e)[:500]}"
                record("C", StageStatus.failed, t0, files, failure, input_hashes=c_inputs)
    else:
        records["C"] = StageRecord(stage="C", name=STAGE_NAMES["C"], status=StageStatus.skipped,
                                   error="skipped: stage B did not produce a slide plan")

    # ---- Stage D (asset registry: real retrieval; no LLM involved)
    assets_summary: dict[str, Any] = {}
    missing_assets: list[str] = []
    reg: AssetRegistry | None = None
    uir: UserInputRequests | None = None
    if reqs is not None:
        _log_stage("stage D (asset_retrieval) start")
        t0 = clock()
        d_inputs = _input_hashes(run_dir, [A_FILE, B_FILE, C_FILE])
        if _can_reuse("D", [REGISTRY_FILE, USER_INPUT_REQUESTS_FILE], d_inputs):
            reg = load_contract(AssetRegistry, run_dir / REGISTRY_FILE)
            uir = load_contract(UserInputRequests, run_dir / USER_INPUT_REQUESTS_FILE)
            missing_assets = [a.asset_id for a in reg.assets if a.status.value == "missing"]
            assets_summary = _assets_summary(reg)
            d_files = [REGISTRY_FILE, USER_INPUT_REQUESTS_FILE] + sorted(
                str(p.relative_to(run_dir)).replace("\\", "/")
                for p in (run_dir / "assets").glob("*") if p.is_file()
            )
            record("D", StageStatus.done, t0, d_files, input_hashes=d_inputs)
        else:
            invalidated = True
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
                record("D", StageStatus.done, t0, d_files, input_hashes=d_inputs)
            except AssetRetrievalFailed as e:
                assets_summary = _assets_summary(e.registry)
                missing_assets = [a.asset_id for a in e.registry.assets]
                if (run_dir / USER_INPUT_REQUESTS_FILE).is_file():
                    uir = load_contract(UserInputRequests, run_dir / USER_INPUT_REQUESTS_FILE)
                failure = f"stage D failed: {e}"[:600]
                d_err_files = [REGISTRY_FILE] + ([USER_INPUT_REQUESTS_FILE] if uir is not None else [])
                record("D", StageStatus.failed, t0, d_err_files, failure, input_hashes=d_inputs)
            except (LLMConfigError, ValueError) as e:
                failure = f"stage D failed: {type(e).__name__}: {str(e)[:500]}"
                record("D", StageStatus.failed, t0, [], failure, input_hashes=d_inputs)
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
        _log_stage("stage E (slide_asset_mapping) start")
        t0 = clock()
        e_inputs = _input_hashes(
            run_dir, [A_FILE, B_FILE, C_FILE, REGISTRY_FILE, USER_INPUT_REQUESTS_FILE, *_provided_input_files(uir)]
        )
        if _can_reuse("E", [E_FILE], e_inputs):
            mapping = load_contract(SlideAssetMapping, run_dir / E_FILE)
            e_files = [E_FILE] + (["llm_trace/E.json"] if (run_dir / "llm_trace/E.json").is_file() else [])
            record("E", StageStatus.done, t0, e_files, input_hashes=e_inputs)
        else:
            invalidated = True
            client.set_stage("E")
            try:
                e_bivia_stats = new_bivia_stats()
                mapping, attempts = run_stage_e(
                    plan,
                    slide_plan,
                    reqs,
                    reg,
                    uir,
                    client,
                    run_dir,
                    vision_client=vision_client,
                    bivia=eff_bivia,
                    bivia_stats_out=e_bivia_stats,
                )
                e_files = [E_FILE] + ([_write_trace(run_dir, "E", attempts, client.model_id)] if attempts else [])
                e_substeps = (
                    {"bivia_judge": bivia_stage_substep_status(e_bivia_stats)}
                    if eff_bivia is not None
                    else None
                )
                record("E", StageStatus.done, t0, e_files, input_hashes=e_inputs, substeps=e_substeps)
            except (LLMError, LLMConfigError, ValueError) as e:
                trace = getattr(e, "attempts", None)
                e_files = [_write_trace(run_dir, "E", trace, client.model_id)] if trace else []
                failure = f"stage E failed: {type(e).__name__}: {str(e)[:500]}"
                record("E", StageStatus.failed, t0, e_files, failure, input_hashes=e_inputs)
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
        _log_stage("stage F (page_layout_design) start")
        t0 = clock()
        f_inputs = _input_hashes(
            run_dir, [A_FILE, B_FILE, REGISTRY_FILE, USER_INPUT_REQUESTS_FILE, E_FILE, *_provided_input_files(uir)]
        )
        if _can_reuse("F", [F_FILE], f_inputs):
            design = load_contract(PageDesign, run_dir / F_FILE)
            f_files = [F_FILE] + sorted(
                p.relative_to(run_dir).as_posix()
                for p in (run_dir / "previews").glob("*.png") if p.is_file()
            )
            record("F", StageStatus.done, t0, f_files, input_hashes=f_inputs)
        else:
            invalidated = True
            try:
                design = run_stage_f(plan, slide_plan, reg, uir, mapping, run_dir, client=client)
                f_files = [F_FILE] + sorted(
                    p.relative_to(run_dir).as_posix()
                    for p in (run_dir / "previews").glob("*.png") if p.is_file()
                )
                record("F", StageStatus.done, t0, f_files, input_hashes=f_inputs)
            except (LLMError, LLMConfigError, ValueError) as e:
                trace = getattr(e, "attempts", None)
                f_files = [_write_trace(run_dir, "TH", trace, client.model_id)] if trace else []
                failure = f"stage F failed: {type(e).__name__}: {str(e)[:500]}"
                record("F", StageStatus.failed, t0, f_files, failure, input_hashes=f_inputs)
    else:
        records["F"] = StageRecord(
            stage="F", name=STAGE_NAMES["F"], status=StageStatus.skipped,
            error="skipped: stage E did not produce a slide asset mapping",
        )

    # ---- Stage G (compile deck_ops.json -> build deck.pptx via DeckRenderer or legacy fallback)
    deck_path: Path | None = None
    deck_ops: DeckOps | None = None
    if waiting_for_input:
        records["G"] = StageRecord(
            stage="G", name=STAGE_NAMES["G"], status=StageStatus.skipped,
            error="skipped: waiting for user input (see user_input_requests.json)",
        )
    elif design is not None and reg is not None and slide_plan is not None and not failure:
        _log_stage("stage G (pptx_generation) start")
        t0 = clock()
        g_inputs = _input_hashes(
            run_dir, [B_FILE, REGISTRY_FILE, USER_INPUT_REQUESTS_FILE, F_FILE, *_provided_input_files(uir)]
        )
        if not compile_only and _can_reuse("G", [ARTIFACT_OPS, G_FILE], g_inputs):
            deck_ops = load_contract(DeckOps, run_dir / ARTIFACT_OPS)
            deck_path = run_dir / G_FILE
            g_files = [ARTIFACT_OPS, G_FILE] + (["deck_dump.json"] if (run_dir / "deck_dump.json").is_file() else [])
            record("G", StageStatus.done, t0, g_files, input_hashes=g_inputs,
                   substeps={"compile_ops": "done", "render_pptx": "done"})
        else:
            invalidated = True
            try:
                tpl_rel: str | None = None
                env_tpl = os.environ.get("PPTV01_TEMPLATE_POTX", "").strip()
                if env_tpl:
                    tpl_src = Path(env_tpl)
                    if not tpl_src.is_file():
                        raise OpsCompileError(f"PPTV01_TEMPLATE_POTX not found: {env_tpl}")
                    tpl_dir = run_dir / "templates"
                    tpl_dir.mkdir(parents=True, exist_ok=True)
                    tpl_dst = tpl_dir / tpl_src.name
                    if tpl_src.resolve() != tpl_dst.resolve():
                        tpl_dst.write_bytes(tpl_src.read_bytes())
                    tpl_rel = _rel_to_run_dir(tpl_dst, run_dir)
                deck_ops = compile_deck_ops(slide_plan, design, reg, uir, run_dir, template_path=tpl_rel)
                if compile_only:
                    record(
                        "G", StageStatus.skipped, t0, [ARTIFACT_OPS],
                        error="render_pptx not_run: compile_only mode (deck_ops.json compiled, deck.pptx not built)",
                        input_hashes=g_inputs,
                        substeps={"compile_ops": "done", "render_pptx": "not_run"},
                    )
                elif renderer is not None:
                    try:
                        renderer.preflight()
                        build_res = renderer.build(deck_ops, run_dir)
                        deck_path = build_res.pptx_path
                        rendered_ph = {
                            op.request_id
                            for op in deck_ops.ops
                            if op.kind is DeckOpKind.add_placeholder_box and op.request_id
                        }
                        if rendered_ph:
                            mark_placeholders_rendered(run_dir, rendered_ph)
                        if (run_dir / USER_INPUT_REQUESTS_FILE).is_file():
                            uir = load_contract(UserInputRequests, run_dir / USER_INPUT_REQUESTS_FILE)
                            uir_summary = summarize_requests(uir)
                            post_g_uir_sha = _file_sha256(run_dir / USER_INPUT_REQUESTS_FILE)
                            g_inputs[USER_INPUT_REQUESTS_FILE] = post_g_uir_sha
                            for st_code in ("E", "F"):
                                if st_code in records and USER_INPUT_REQUESTS_FILE in records[st_code].input_hashes:
                                    records[st_code].input_hashes[USER_INPUT_REQUESTS_FILE] = post_g_uir_sha
                        g_files = [ARTIFACT_OPS, G_FILE]
                        if build_res.dump_path.is_file():
                            g_files.append(_rel_to_run_dir(build_res.dump_path, run_dir))
                        record(
                            "G", StageStatus.done, t0, g_files,
                            input_hashes=g_inputs,
                            substeps={"compile_ops": "done", "render_pptx": "done"},
                        )
                    except DeckRendererUnavailableError as e:
                        record(
                            "G", StageStatus.skipped, t0, [ARTIFACT_OPS],
                            error=f"render_pptx not_run: {e}",
                            input_hashes=g_inputs,
                            substeps={"compile_ops": "done", "render_pptx": "not_run"},
                        )
                else:
                    deck_path = run_stage_g(slide_plan, design, reg, uir, run_dir)
                    if (run_dir / USER_INPUT_REQUESTS_FILE).is_file():
                        uir = load_contract(UserInputRequests, run_dir / USER_INPUT_REQUESTS_FILE)
                        uir_summary = summarize_requests(uir)
                        post_g_uir_sha = _file_sha256(run_dir / USER_INPUT_REQUESTS_FILE)
                        g_inputs[USER_INPUT_REQUESTS_FILE] = post_g_uir_sha
                        for st_code in ("E", "F"):
                            if st_code in records and USER_INPUT_REQUESTS_FILE in records[st_code].input_hashes:
                                records[st_code].input_hashes[USER_INPUT_REQUESTS_FILE] = post_g_uir_sha
                    record(
                        "G", StageStatus.done, t0, [ARTIFACT_OPS, G_FILE],
                        input_hashes=g_inputs,
                        substeps={"compile_ops": "done", "render_pptx": "done"},
                    )
            except (OpsCompileError, DeckRenderExecutionError, OSError, ValueError) as e:
                g_err_files = [ARTIFACT_OPS] if (run_dir / ARTIFACT_OPS).is_file() else []
                failure = f"stage G failed: {type(e).__name__}: {str(e)[:500]}"
                record(
                    "G", StageStatus.failed, t0, g_err_files, failure,
                    input_hashes=g_inputs,
                    substeps={"compile_ops": "done" if g_err_files else "failed", "render_pptx": "failed" if g_err_files else "not_run"},
                )
    else:
        records["G"] = StageRecord(
            stage="G", name=STAGE_NAMES["G"], status=StageStatus.skipped,
            error="skipped: stage F did not produce page_design.json",
        )

    # ---- Stage H (render + QA + bounded repair)
    if waiting_for_input:
        records["H"] = StageRecord(
            stage="H", name=STAGE_NAMES["H"], status=StageStatus.skipped,
            error="skipped: waiting for user input (see user_input_requests.json)",
        )
    elif deck_path is not None and slide_plan is not None and not failure:
        _log_stage("stage H (render_qa_repair) start")
        t0 = clock()
        h_inputs = _input_hashes(
            run_dir, [B_FILE, REGISTRY_FILE, USER_INPUT_REQUESTS_FILE, F_FILE, G_FILE, *_provided_input_files(uir)]
        )
        if _can_reuse("H", [H_FILE], h_inputs) and list((run_dir / "render").glob("slide_*.png")):
            h_files = [H_FILE] + sorted(
                p.relative_to(run_dir).as_posix()
                for p in (run_dir / "render").glob("slide_*.png") if p.is_file()
            )
            record("H", StageStatus.done, t0, h_files, input_hashes=h_inputs)
        else:
            invalidated = True
            try:
                h_bivia_stats = new_bivia_stats()
                _qa_rep, rendered_pngs = run_stage_h(
                    deck_path,
                    slide_plan,
                    run_dir,
                    design=design,
                    reg=reg,
                    uir=uir,
                    mapping=mapping,
                    render_fn=render_fn,
                    deck_renderer=renderer,
                    template_path=deck_ops.template_path if deck_ops is not None else None,
                    cross_render=cross_render,
                    bivia=eff_bivia,
                    bivia_stats_out=h_bivia_stats,
                )
                h_files = [H_FILE] + [_rel_to_run_dir(p, run_dir) for p in rendered_pngs]
                if (run_dir / "render" / "deck.pdf").is_file():
                    h_files.append("render/deck.pdf")
                # Sync hashes if H's bounded repair updated page_design.json or deck.pptx
                if (run_dir / F_FILE).is_file():
                    post_f_sha = _file_sha256(run_dir / F_FILE)
                    h_inputs[F_FILE] = post_f_sha
                    if "G" in records and F_FILE in records["G"].input_hashes:
                        records["G"].input_hashes[F_FILE] = post_f_sha
                if (run_dir / G_FILE).is_file():
                    h_inputs[G_FILE] = _file_sha256(run_dir / G_FILE)
                h_substeps = (
                    {"bivia_judge": bivia_stage_substep_status(h_bivia_stats)}
                    if eff_bivia is not None
                    else None
                )
                record("H", StageStatus.done, t0, h_files, input_hashes=h_inputs, substeps=h_substeps)
            except (
                RendererUnavailableError,
                RenderExecutionError,
                DeckRendererUnavailableError,
                DeckRenderExecutionError,
                OSError,
                ValueError,
            ) as e:
                h_files = [H_FILE] if (run_dir / H_FILE).is_file() else []
                failure = f"stage H failed: {type(e).__name__}: {str(e)[:500]}"
                record("H", StageStatus.failed, t0, h_files, failure, input_hashes=h_inputs)
    else:
        records["H"] = StageRecord(
            stage="H", name=STAGE_NAMES["H"], status=StageStatus.skipped,
            error="skipped: stage G did not produce deck.pptx",
        )

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
            "bivia_judge": _bivia_tool_config(eff_bivia, c_bivia_stats, e_bivia_stats, h_bivia_stats),
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
