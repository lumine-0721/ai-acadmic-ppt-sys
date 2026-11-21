"""Stage H: PowerPoint COM render + live measurement QA + bounded PageDesign repair/recheck.

Optional LibreOffice headless rendering is kept only as a secondary cross-render
compatibility check (--cross-render) or legacy fallback when no DeckRenderer is passed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

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
from pptv01.g_build import compile_deck_ops
from pptv01.renderers.protocol import (
    DeckRenderer,
    MeasureResult,
    RenderExecutionError as DeckRenderExecutionError,
    RendererUnavailableError as DeckRendererUnavailableError,
)

from .inspect import inspect_deck
from .occupancy import check_deck_occupancy
from .render import RendererUnavailableError, render_pptx_to_pngs
from .repair import apply_repairs, repair_page_design

ARTIFACT = "qa_report.json"
RENDER_DIR = "render"
CROSS_RENDER_DIR = "render_cross_check"
MAX_REPAIR_ROUNDS = 2


RenderFn = Callable[[Path, Path], list[Path]]


def _issue_key(i: QAIssue):
    return (i.page_id, i.kind, i.element_id)


def _format_provenance(name: str, version: str, build: str) -> str:
    if version and build:
        return f"{name} {version} (build {build})"
    if version:
        return f"{name} {version}"
    return name


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
    deck_renderer: DeckRenderer | None = None,
    template_path: str | None = None,
    cross_render: bool = False,
    installed_fonts: list[str] | None = None,
    max_repair_rounds: int = MAX_REPAIR_ROUNDS,
) -> tuple[QAReport, list[Path]]:
    """Render deck.pptx to render/slide_*.png, measure, inspect QA, run bounded repairs + recheck."""
    pptx_path = Path(pptx_path)
    run_dir = Path(run_dir)
    render_out = run_dir / RENDER_DIR
    page_ids: list[PageId] = [p.page_id for p in slide_plan.pages]

    effective_template_path = template_path
    if effective_template_path is None and (run_dir / "deck_ops.json").is_file():
        try:
            from pptv01.contracts import DeckOps, load_contract
            effective_template_path = load_contract(DeckOps, run_dir / "deck_ops.json").template_path
        except Exception:
            effective_template_path = None

    render_err: Exception | None = None
    rendered_pngs: list[Path] = []
    measure_res: MeasureResult | None = None
    renderer_label = deck_renderer.name if deck_renderer is not None else "libreoffice-headless"
    render_provenance = renderer_label
    sys_fonts = installed_fonts

    def _export_and_measure(cur_pptx: Path) -> tuple[list[Path], MeasureResult | None, str]:
        if deck_renderer is not None:
            exp = deck_renderer.export(cur_pptx, render_out)
            meas = deck_renderer.measure(cur_pptx, page_ids=page_ids)
            prov = _format_provenance(exp.renderer_name, exp.renderer_version, exp.renderer_build)
            return exp.png_paths, meas, prov
        fn = render_fn or render_pptx_to_pngs
        pngs = fn(cur_pptx, render_out)
        return pngs, None, "libreoffice-headless"

    try:
        if deck_renderer is not None and sys_fonts is None:
            info = deck_renderer.preflight()
            sys_fonts = info.fonts or None
        rendered_pngs, measure_res, render_provenance = _export_and_measure(pptx_path)
    except (RendererUnavailableError, DeckRendererUnavailableError, DeckRenderExecutionError) as e:
        render_err = e
        renderer_label = f"{renderer_label} (unavailable: {str(e)[:120]})"
        render_provenance = renderer_label

    capacity_log: list[dict[str, Any]] = []
    missing_fonts: list[str] = []
    current_design = design

    current_issues = inspect_deck(
        pptx_path,
        slide_plan=slide_plan,
        design=current_design,
        reg=reg,
        uir=uir,
        mapping=mapping,
        rendered_pngs=rendered_pngs or None,
        measure_result=measure_res,
        installed_fonts=sys_fonts,
        capacity_log=capacity_log,
        missing_fonts_out=missing_fonts,
    )

    recorded_fixes: dict[tuple, tuple[QAIssue, str]] = {}
    for _round in range(max_repair_rounds):
        actionable = [i for i in current_issues if i.severity in (Severity.blocker, Severity.major)]
        if not actionable:
            break

        if deck_renderer is not None and current_design is not None and reg is not None and render_err is None:
            # R3 rule: repair edits page_design.json -> recompile deck_ops.json -> COM rebuild -> re-export & re-measure
            repaired_design, applied = repair_page_design(current_design, actionable, measure_result=measure_res)
            if not applied:
                break
            current_design = repaired_design
            write_contract(current_design, run_dir / "page_design.json")
            new_ops = compile_deck_ops(
                slide_plan,
                current_design,
                reg,
                uir,
                run_dir,
                template_path=effective_template_path,
            )
            build_res = deck_renderer.build(new_ops, run_dir)
            pptx_path = build_res.pptx_path
            rendered_pngs, measure_res, render_provenance = _export_and_measure(pptx_path)
        else:
            applied = apply_repairs(pptx_path, actionable, page_ids)
            if not applied:
                break
            if render_err is None:
                rendered_pngs, measure_res, render_provenance = _export_and_measure(pptx_path)

        capacity_log = []
        missing_fonts = []
        rechecked_issues = inspect_deck(
            pptx_path,
            slide_plan=slide_plan,
            design=current_design,
            reg=reg,
            uir=uir,
            mapping=mapping,
            rendered_pngs=rendered_pngs or None,
            measure_result=measure_res,
            installed_fonts=sys_fonts,
            capacity_log=capacity_log,
            missing_fonts_out=missing_fonts,
        )

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

    # Optional secondary LibreOffice cross-render check (never replaces PowerPoint primary evidence)
    cross_notes: list[str] = []
    if cross_render and deck_renderer is not None and render_err is None:
        try:
            lo_pngs = render_pptx_to_pngs(pptx_path, run_dir / CROSS_RENDER_DIR)
            cross_notes.append(f"libreoffice cross-render produced {len(lo_pngs)} PNG(s) in {CROSS_RENDER_DIR}/")
        except (RendererUnavailableError, Exception) as e:
            cross_notes.append(f"libreoffice cross-render skipped/unavailable: {str(e)[:160]}")

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
    if missing_fonts:
        summary_parts.append(f"missing fonts: {', '.join(missing_fonts)}")
    if render_err is not None:
        summary_parts.append(f"RENDER FAILED: {render_err}")

    occupancy_rows: list[dict[str, Any]] = []
    diagnostics: list[str] = []
    if current_design is not None:
        occ_report = check_deck_occupancy(current_design, measure_result=measure_res)
        occupancy_rows = [r.to_dict() for r in occ_report.pages]
        diagnostics.append(f"fill_rate: {occ_report.detail}")

    report = QAReport(
        schema_version=SCHEMA_VERSION,
        run_id=slide_plan.run_id,
        renderer=renderer_label,
        render_provenance=render_provenance,
        rendered_pages=page_ids[:len(rendered_pngs)] if rendered_pngs else [],
        issues=final_issues,
        missing_fonts=missing_fonts,
        capacity_vs_measured=capacity_log,
        occupancy=occupancy_rows,
        diagnostics=diagnostics,
        cross_render_notes=cross_notes,
        summary="; ".join(summary_parts),
    )
    write_contract(report, run_dir / ARTIFACT)

    if render_err is not None:
        raise render_err

    return report, rendered_pngs
