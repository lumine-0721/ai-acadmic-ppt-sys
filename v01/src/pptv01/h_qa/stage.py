"""Stage H: LibreOffice headless rendering -> QA inspection -> bounded repair + recheck (qa_report.json)."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from pptv01.contracts import (
    AssetRegistry,
    PageDesign,
    PageId,
    QAIssue,
    QAReport,
    Recheck,
    SCHEMA_VERSION,
    Severity,
    SlideAssetMapping,
    SlidePlan,
    UserInputRequests,
    write_contract,
)

from .inspect import inspect_deck
from .render import RendererUnavailableError, render_pptx_to_pngs
from .repair import apply_repairs

ARTIFACT = "qa_report.json"
RENDER_DIR = "render"
MAX_REPAIR_ROUNDS = 2


RenderFn = Callable[[Path, Path], list[Path]]


def _issue_key(i: QAIssue):
    return (i.page_id, i.kind, i.element_id)


def run_stage_h(
    pptx_path: Path,
    slide_plan: SlidePlan,
    run_dir: Path,
    *,
    design: PageDesign | None = None,
    reg: AssetRegistry | None = None,
    uir: UserInputRequests | None = None,
    mapping: SlideAssetMapping | None = None,
    render_fn: RenderFn | None = None,
    max_repair_rounds: int = MAX_REPAIR_ROUNDS,
) -> tuple[QAReport, list[Path]]:
    """Render deck.pptx to render/slide_*.png, run QA, apply bounded repairs + recheck, write qa_report.json.

    If LibreOffice/pdftoppm are missing (and no render_fn is injected), still inspects deck.pptx,
    writes qa_report.json with rendered_pages=[] and the honest renderer error, then raises
    RendererUnavailableError so the pipeline never claims rendering succeeded.
    """
    pptx_path = Path(pptx_path)
    run_dir = Path(run_dir)
    render_out = run_dir / RENDER_DIR
    renderer = render_fn or render_pptx_to_pngs
    page_ids: list[PageId] = [p.page_id for p in slide_plan.pages]

    render_err: RendererUnavailableError | None = None
    rendered_pngs: list[Path] = []
    renderer_label = "libreoffice-headless"
    try:
        rendered_pngs = renderer(pptx_path, render_out)
    except RendererUnavailableError as e:
        render_err = e
        renderer_label = f"libreoffice-headless (unavailable: {str(e)[:120]})"

    current_issues = inspect_deck(
        pptx_path,
        slide_plan=slide_plan,
        design=design,
        reg=reg,
        uir=uir,
        mapping=mapping,
        rendered_pngs=rendered_pngs or None,
    )

    recorded_fixes: dict[tuple, tuple[QAIssue, str]] = {}
    for _round in range(max_repair_rounds):
        actionable = [i for i in current_issues if i.severity in (Severity.blocker, Severity.major)]
        if not actionable:
            break
        applied = apply_repairs(pptx_path, actionable, page_ids)
        if not applied:
            break

        # Re-render if renderer is available before recheck
        if render_err is None:
            rendered_pngs = renderer(pptx_path, render_out)

        rechecked_issues = inspect_deck(
            pptx_path,
            slide_plan=slide_plan,
            design=design,
            reg=reg,
            uir=uir,
            mapping=mapping,
            rendered_pngs=rendered_pngs or None,
        )
        still_present = {_issue_key(i) for i in rechecked_issues}

        for i in actionable:
            k = _issue_key(i)
            if k in applied:
                note = applied[k]
                if k in recorded_fixes:
                    prev_note = recorded_fixes[k][1]
                    note = f"{prev_note}; {note}"
                recorded_fixes[k] = (i, note)

        current_issues = rechecked_issues
        if not any(i.severity in (Severity.blocker, Severity.major) for i in current_issues):
            break

    # Build final QAReport issues list: all fixed issues with recheck outcome + any remaining unfixed issues
    remaining_keys = {_issue_key(i) for i in current_issues}
    final_issues: list[QAIssue] = []
    seen_keys: set[tuple] = set()

    for k, (orig, fix_note) in recorded_fixes.items():
        seen_keys.add(k)
        recheck = Recheck.failed if k in remaining_keys else Recheck.passed
        final_issues.append(orig.model_copy(update={
            "fix_applied": True,
            "fix_note": fix_note,
            "recheck": recheck,
        }))

    for i in current_issues:
        k = _issue_key(i)
        if k not in seen_keys:
            seen_keys.add(k)
            final_issues.append(i)

    n_fixed_ok = sum(1 for i in final_issues if i.fix_applied and i.recheck is Recheck.passed)
    n_unfixed = sum(
        1 for i in final_issues
        if (not i.fix_applied and i.severity in (Severity.blocker, Severity.major))
        or (i.fix_applied and i.recheck is Recheck.failed)
    )
    summary_parts = [
        f"{len(final_issues)} issue(s) recorded ({n_fixed_ok} fixed & passed recheck, {n_unfixed} unresolved blocker/major)",
    ]
    if render_err is not None:
        summary_parts.append(f"RENDER FAILED: {render_err}")

    report = QAReport(
        schema_version=SCHEMA_VERSION,
        run_id=slide_plan.run_id,
        renderer=renderer_label,
        rendered_pages=page_ids[:len(rendered_pngs)] if rendered_pngs else [],
        issues=final_issues,
        summary="; ".join(summary_parts),
    )
    write_contract(report, run_dir / ARTIFACT)

    if render_err is not None:
        raise render_err

    return report, rendered_pngs
