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
from typing import Any, Callable

from pptv01 import __version__
from pptv01.a_plan import ARTIFACT as A_FILE, run_stage_a
from pptv01.c_asset_requirements import ARTIFACT as C_FILE, run_stage_c
from pptv01.b_slide_plan import ARTIFACT as B_FILE, RESEARCH_ARTIFACT, ResearchFailed, run_stage_b
from pptv01.contracts import (
    ProductionPlan,
    ResearchRecord,
    SlidePlan,
    RunOverall,
    RunReport,
    StageRecord,
    StageStatus,
    load_contract,
    write_contract,
)
from pptv01.llm import ChatClient, LLMError
from pptv01.retrieval import ResearchProvider, research_from_env

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
IMPLEMENTED_STAGES = ("A", "B", "C")


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
    research: ResearchProvider | None = None,
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
        client.set_stage("C")
        try:
            _, attempts = run_stage_c(plan, slide_plan, client, run_dir, notes)
            record("C", StageStatus.done, t0, [C_FILE, _write_trace(run_dir, "C", attempts, client.model_id)])
        except (LLMError, ValueError) as e:
            trace = getattr(e, "attempts", None)
            files = [_write_trace(run_dir, "C", trace, client.model_id)] if trace else []
            failure = f"stage C failed: {type(e).__name__}: {str(e)[:500]}"
            record("C", StageStatus.failed, t0, files, failure)
    else:
        records["C"] = StageRecord(stage="C", name=STAGE_NAMES["C"], status=StageStatus.skipped,
                                   error="skipped: stage B did not produce a slide plan")

    for code in "DEFGH":
        records[code] = StageRecord(stage=code, name=STAGE_NAMES[code], status=StageStatus.not_implemented)

    stages = [records[c] for c in "ABCDEFGH"]
    if failure:
        overall = RunOverall.failed
    elif all(s.status is StageStatus.done for s in stages):
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
        },
        stages=stages,
        defaults_summary=defaults_summary,
        missing_assets=[],
        revision_count=0,
        failure_reason=failure,
    )
    write_contract(report, run_dir / "run_report.json")
    return report, run_dir
