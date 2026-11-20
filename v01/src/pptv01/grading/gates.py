"""Deterministic hard gates (benchmark spec 6.1). No model, no score: files exist, contracts validate, constraints hold.

Gates for stages that do not exist yet are reported ``not_run`` (so the overall result is ``incomplete``),
never silently passed.
"""

from __future__ import annotations

import re
from pathlib import Path

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pydantic import ValidationError

from pptv01 import textcheck
from pptv01.contracts import (
    AssetRegistry,
    AssetStatus,
    CONTRACT_FILES,
    GateReport,
    GateResult,
    ProductionPlan,
    SlidePlan,
    UserInputRequests,
    UserRequestStatus,
    load_contract,
)
from pptv01.d_retrieve.license import needs_attribution

STAGE_FILES = [
    "production_plan",
    "research_brief",
    "slide_plan",
    "asset_requirements",
    "asset_registry",
    "user_input_requests",
    "slide_asset_mapping",
    "page_design",
    "qa_report",
]

_ASSET_BIND_RE = re.compile(r"^(?:ASSET|CHART|TABLE|DIAGRAM|ICON)_(A\d{3,})(?:__|$)")
_INPUT_BIND_RE = re.compile(r"^(?:INPUT|CHART_INPUT|TABLE_INPUT|DIAGRAM_INPUT)_(U\d{3,})(?:__|$)")


def _check_pptx_visual_gates(
    run_dir: Path,
    reg: AssetRegistry | None,
    uir: UserInputRequests | None,
    sp: SlidePlan | None,
) -> list[GateResult]:
    deck_path = run_dir / "deck.pptx"
    if not deck_path.is_file():
        return [
            GateResult(gate="no_fabricated_visuals", status="not_run", detail="deck.pptx absent (stage G not run)"),
            GateResult(gate="placeholders_marked", status="not_run", detail="deck.pptx absent (stage G not run)"),
            GateResult(gate="pptx_opens", status="not_run", detail="deck.pptx absent (stage G not run)"),
            GateResult(gate="native_content_not_screenshots", status="not_run", detail="deck.pptx absent (stage G not run)"),
            GateResult(gate="attribution_present", status="not_run", detail="deck.pptx absent (stage G not run)"),
        ]
    try:
        prs = Presentation(str(deck_path))
    except Exception as e:  # noqa: BLE001
        msg = f"cannot open deck.pptx: {type(e).__name__}: {e}"[:200]
        return [
            GateResult(gate="no_fabricated_visuals", status="fail", detail=msg),
            GateResult(gate="placeholders_marked", status="fail", detail=msg),
            GateResult(gate="pptx_opens", status="fail", detail=msg),
            GateResult(gate="native_content_not_screenshots", status="fail", detail=msg),
            GateResult(gate="attribution_present", status="fail", detail=msg),
        ]

    valid_assets: set[str] = set()
    if reg is not None:
        valid_assets = {
            a.asset_id
            for a in reg.assets
            if a.status in (AssetStatus.acquired, AssetStatus.drawn, AssetStatus.substituted)
        }
    valid_inputs: set[str] = set()
    if uir is not None:
        for r in uir.requests:
            if r.status is UserRequestStatus.provided and r.provided and (run_dir / r.provided.file).is_file():
                valid_inputs.add(r.request_id)
                if r.asset_id:
                    valid_assets.add(r.asset_id)

    unbound: list[str] = []
    for idx, slide in enumerate(prs.slides, 1):
        for sh in slide.shapes:
            is_visual = (
                sh.shape_type is MSO_SHAPE_TYPE.PICTURE
                or getattr(sh, "has_chart", False)
                or getattr(sh, "has_table", False)
            )
            if not is_visual:
                continue
            name = sh.name or ""
            m_a = _ASSET_BIND_RE.match(name)
            m_u = _INPUT_BIND_RE.match(name)
            if m_a and m_a.group(1) in valid_assets:
                continue
            if m_u and m_u.group(1) in valid_inputs:
                continue
            unbound.append(f"slide {idx}: {name or sh.shape_type.name}")

    fab_res = GateResult(
        gate="no_fabricated_visuals",
        status="fail" if unbound else "pass",
        detail=f"unbound visual shape(s): {', '.join(unbound[:6])}" if unbound else "all visual elements bound to verified assets/inputs",
    )

    # placeholders_marked
    if uir is None:
        ph_res = GateResult(
            gate="placeholders_marked",
            status="not_run",
            detail="user_input_requests.json absent",
        )
    else:
        need_ph = [
            r for r in uir.requests
            if r.status in (UserRequestStatus.open, UserRequestStatus.placeholder_rendered)
        ]
        if not need_ph:
            ph_res = GateResult(
                gate="placeholders_marked",
                status="pass",
                detail="no open/placeholder requests requiring placeholders",
            )
        else:
            page_to_slide = {p.page_id: i for i, p in enumerate(sp.pages)} if sp is not None else {}
            missing_ph: list[str] = []
            for r in need_ph:
                target_slides = (
                    [prs.slides[page_to_slide[pid]] for pid in r.page_ids if pid in page_to_slide and page_to_slide[pid] < len(prs.slides)]
                    if page_to_slide
                    else list(prs.slides)
                )
                if not target_slides:
                    missing_ph.append(f"{r.request_id} (no matching slide)")
                    continue
                for sl in target_slides:
                    has_shape = any(
                        (sh.name or "").startswith(f"PLACEHOLDER_{r.request_id}")
                        and getattr(sh, "has_text_frame", False)
                        and "待补充：" in sh.text_frame.text
                        and r.request_id in sh.text_frame.text
                        for sh in sl.shapes
                    )
                    notes_text = ""
                    if sl.has_notes_slide and sl.notes_slide.notes_text_frame is not None:
                        notes_text = sl.notes_slide.notes_text_frame.text
                    has_note = r.request_id in notes_text
                    if not (has_shape and has_note):
                        missing_ph.append(r.request_id)
                        break
            ph_res = GateResult(
                gate="placeholders_marked",
                status="fail" if missing_ph else "pass",
                detail=f"missing marked placeholder/note for: {missing_ph}" if missing_ph else f"{len(need_ph)} placeholder(s) properly marked",
            )

    # pptx_opens
    n_slides = len(prs.slides)
    if n_slides == 0:
        opens_res = GateResult(gate="pptx_opens", status="fail", detail="deck.pptx has 0 slides")
    elif sp is not None and n_slides != len(sp.pages):
        opens_res = GateResult(
            gate="pptx_opens",
            status="fail",
            detail=f"deck.pptx has {n_slides} slides, planned {len(sp.pages)}",
        )
    else:
        opens_res = GateResult(gate="pptx_opens", status="pass", detail=f"opened deck.pptx with {n_slides} slides")

    # native_content_not_screenshots
    slide_area = float(prs.slide_width) * float(prs.slide_height)
    screenshot_slides: list[str] = []
    for idx, slide in enumerate(prs.slides, 1):
        native_text_count = sum(
            1
            for sh in slide.shapes
            if (getattr(sh, "has_text_frame", False) and sh.text_frame.text.strip())
            or getattr(sh, "has_chart", False)
            or getattr(sh, "has_table", False)
        )
        big_pics = [
            sh
            for sh in slide.shapes
            if sh.shape_type is MSO_SHAPE_TYPE.PICTURE
            and (float(sh.width) * float(sh.height)) >= 0.85 * slide_area
        ]
        if len(slide.shapes) == 0 or (big_pics and native_text_count == 0):
            screenshot_slides.append(f"slide {idx}")
    native_res = GateResult(
        gate="native_content_not_screenshots",
        status="fail" if screenshot_slides else "pass",
        detail=(
            f"slide(s) look like full-page screenshots or empty: {', '.join(screenshot_slides)}"
            if screenshot_slides
            else f"all {n_slides} slide(s) contain native text/shapes"
        ),
    )

    # attribution_present: any CC BY / BY-SA asset embedded in deck.pptx must have attribution on slide or notes
    if reg is None:
        attr_res = GateResult(gate="attribution_present", status="not_run", detail="asset_registry.json absent")
    else:
        reg_by_id = {a.asset_id: a for a in reg.assets}
        missing_attr: list[str] = []
        for idx, slide in enumerate(prs.slides, 1):
            slide_text = "\n".join(
                sh.text_frame.text
                for sh in slide.shapes
                if getattr(sh, "has_text_frame", False)
            )
            notes_text = ""
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
                notes_text = slide.notes_slide.notes_text_frame.text
            combined = f"{slide_text}\n{notes_text}"
            for sh in slide.shapes:
                m_a = _ASSET_BIND_RE.match(sh.name or "")
                if not m_a:
                    continue
                aid = m_a.group(1)
                a = reg_by_id.get(aid)
                if a and needs_attribution(a.license_or_terms or ""):
                    credit_ok = bool(
                        (a.attribution and a.attribution in combined)
                        or (a.author and a.author in combined)
                    )
                    if not credit_ok:
                        missing_attr.append(f"slide {idx}:{aid}")
        attr_res = GateResult(
            gate="attribution_present",
            status="fail" if missing_attr else "pass",
            detail=f"missing attribution on slide/notes for: {missing_attr}" if missing_attr else "all attribution-required assets credited",
        )

    return [fab_res, ph_res, opens_res, native_res, attr_res]


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
    uir = loaded.get("user_input_requests")
    results.extend(
        _check_pptx_visual_gates(
            run_dir,
            reg if isinstance(reg, AssetRegistry) else None,
            uir if isinstance(uir, UserInputRequests) else None,
            sp if isinstance(sp, SlidePlan) else None,
        )
    )
    st = {r.status for r in results}
    overall = "fail" if "fail" in st else ("incomplete" if "not_run" in st else "pass")
    return GateReport(run_id=run_id if run_id != "unknown" else "unknown-run", results=results, overall=overall)
