"""Deterministic hard gates (benchmark spec 6.1). No model, no score: files exist, contracts validate, constraints hold.

NOTE: python-pptx is used here strictly READ-ONLY (never writes PPTX files).
Gates for stages that do not exist yet are reported ``not_run`` (so the overall result is ``incomplete``),
never silently passed.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE, MSO_SHAPE_TYPE
from pydantic import ValidationError

from pptv01 import textcheck
from pptv01.contracts import (
    AssetRegistry,
    AssetStatus,
    CONTRACT_FILES,
    DeckOpKind,
    DeckOps,
    ElementKind,
    GateReport,
    GateResult,
    PageDesign,
    ProductionPlan,
    SlidePlan,
    UserInputRequests,
    UserRequestStatus,
    load_contract,
)
from pptv01.d_retrieve.license import needs_attribution
from pptv01.f_layout.layouts import (
    LEGACY_LAYOUT_ID,
    MAX_PLACEHOLDER_AREA_RATIO,
    normalize_layout_id,
    slide_content_area_in2,
)
from pptv01.renderers.protocol import DeckDump

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
_DIAGRAM_BIND_RE = re.compile(r"^(?:DIAGRAM_(A\d{3,})|DIAGRAM_INPUT_(U\d{3,}))(?:__|$)")
_PLACEHOLDER_NAME_RE = re.compile(r"^PLACEHOLDER_(U\d{3,})(?:__|$)")
# Strict exact fallback set used ONLY when neither deck_ops.json nor page_design.json exists in run_dir
_FALLBACK_STRUCTURAL_EXACT = frozenset({
    "bg_header",
    "accent_bar",
    "accent_rail",
    "accent_divider",
    "card_main",
    "card_text",
    "title",
    "bullets",
    "summary",
    "footer",
})
_FALLBACK_CAPTION_RE = re.compile(r"^cap_\d+$")
_ALLOWED_STRUCTURAL_AUTOSHAPES = frozenset({
    MSO_SHAPE.RECTANGLE,
    MSO_SHAPE.ROUNDED_RECTANGLE,
})


def _load_declared_structural_names(run_dir: Path) -> dict[int, set[str]] | None:
    """Build exact per-slide structural element name whitelist from deck_ops.json or page_design.json."""
    ops_path = run_dir / "deck_ops.json"
    if ops_path.is_file():
        try:
            ops = load_contract(DeckOps, ops_path)
            page_to_idx = {pid: i for i, pid in enumerate(ops.page_ids, 1)}
            per_slide: dict[int, set[str]] = {i: set() for i in range(1, len(ops.page_ids) + 1)}
            for op in ops.ops:
                s_idx = page_to_idx.get(op.page_id)
                if s_idx is None:
                    continue
                if op.kind in (DeckOpKind.set_placeholder_text, DeckOpKind.add_textbox):
                    sname = str(op.params.get("shape_name") or op.element_id or "").strip()
                    if sname:
                        per_slide[s_idx].add(sname)
                elif op.kind is DeckOpKind.add_shape and not op.asset_id and not op.request_id:
                    sname = str(op.params.get("shape_name") or op.element_id or "").strip()
                    if sname:
                        per_slide[s_idx].add(sname)
            return per_slide
        except Exception:
            pass

    design_path = run_dir / "page_design.json"
    if design_path.is_file():
        try:
            design = load_contract(PageDesign, design_path)
            per_slide = {}
            for s_idx, page in enumerate(design.pages, 1):
                names: set[str] = set()
                for el in page.elements:
                    if el.kind in (
                        ElementKind.title,
                        ElementKind.body,
                        ElementKind.bullet,
                        ElementKind.caption,
                        ElementKind.footer,
                    ):
                        names.add(el.element_id)
                    elif el.kind is ElementKind.shape and not el.asset_id and not el.request_id and el.role != "diagram_json":
                        names.add(el.element_id)
                per_slide[s_idx] = names
            return per_slide
        except Exception:
            pass
    return None


def _connector_endpoint_ids(shape) -> tuple[int | None, int | None]:
    """Extract OOXML <a:stCxn id=.../> and <a:endCxn id=.../> target shape IDs from a cxnSp connector."""
    el = getattr(shape, "_element", None)
    if el is None:
        return None, None
    st_id: int | None = None
    end_id: int | None = None
    for node in el.iter():
        tag = str(node.tag)
        if tag.endswith("}stCxn") or tag == "stCxn":
            raw = node.get("id")
            if raw and raw.isdigit():
                st_id = int(raw)
        elif tag.endswith("}endCxn") or tag == "endCxn":
            raw = node.get("id")
            if raw and raw.isdigit():
                end_id = int(raw)
    return st_id, end_id


def _is_allowed_structural_shape_type(shape) -> tuple[bool, str]:
    """Structural elements may only be textbox, placeholder, or rectangle/rounded-rectangle autoshape."""
    st = shape.shape_type
    if st in (MSO_SHAPE_TYPE.TEXT_BOX, MSO_SHAPE_TYPE.PLACEHOLDER):
        return True, ""
    if st is MSO_SHAPE_TYPE.AUTO_SHAPE:
        try:
            ast = shape.auto_shape_type
        except Exception:
            ast = None
        if ast in _ALLOWED_STRUCTURAL_AUTOSHAPES:
            return True, ""
        return False, f"disallowed structural AutoShape type {ast}"
    return False, f"disallowed structural shape type {st.name}"


def _blob_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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
    asset_sha_by_id: dict[str, str] = {}
    sha_to_asset_ids: dict[str, list[str]] = {}
    if reg is not None:
        for a in reg.assets:
            if a.status in (AssetStatus.acquired, AssetStatus.drawn, AssetStatus.substituted):
                valid_assets.add(a.asset_id)
                sha = a.sha256
                if not sha and a.file_path and (run_dir / a.file_path).is_file():
                    sha = _blob_sha256((run_dir / a.file_path).read_bytes())
                if sha:
                    asset_sha_by_id[a.asset_id] = sha
                    sha_to_asset_ids.setdefault(sha, []).append(a.asset_id)

    valid_inputs: set[str] = set()
    input_sha_by_id: dict[str, str] = {}
    all_request_ids: set[str] = set()
    if uir is not None:
        for r in uir.requests:
            all_request_ids.add(r.request_id)
            if r.status is UserRequestStatus.provided and r.provided and (run_dir / r.provided.file).is_file():
                valid_inputs.add(r.request_id)
                input_sha_by_id[r.request_id] = r.provided.sha256
                if r.asset_id:
                    valid_assets.add(r.asset_id)
                    asset_sha_by_id.setdefault(r.asset_id, r.provided.sha256)

    declared_structural = _load_declared_structural_names(run_dir)

    unbound: list[str] = []
    for idx, slide in enumerate(prs.slides, 1):
        # Collect valid bound DIAGRAM_* node shape_ids on this slide for connector endpoint verification
        slide_diagram_node_ids: set[int] = set()
        for sh in slide.shapes:
            sname = (sh.name or "").strip()
            is_conn_sh = sh.shape_type is MSO_SHAPE_TYPE.LINE or str(getattr(getattr(sh, "_element", None), "tag", "")).endswith("cxnSp")
            if is_conn_sh:
                continue
            md = _DIAGRAM_BIND_RE.match(sname)
            if md:
                aid_d, uid_d = md.group(1), md.group(2)
                if (aid_d and aid_d in valid_assets) or (uid_d and uid_d in valid_inputs):
                    slide_diagram_node_ids.add(int(sh.shape_id))

        allowed_names_for_slide = declared_structural.get(idx, set()) if declared_structural is not None else None

        for sh in slide.shapes:
            name = (sh.name or "").strip()
            is_pic = sh.shape_type is MSO_SHAPE_TYPE.PICTURE
            is_visual = is_pic or getattr(sh, "has_chart", False) or getattr(sh, "has_table", False)
            is_conn = sh.shape_type is MSO_SHAPE_TYPE.LINE or str(getattr(getattr(sh, "_element", None), "tag", "")).endswith("cxnSp")
            m_a = _ASSET_BIND_RE.match(name)
            m_u = _INPUT_BIND_RE.match(name)
            m_ph = _PLACEHOLDER_NAME_RE.match(name)

            if is_visual:
                if m_a and m_a.group(1) in valid_assets:
                    aid = m_a.group(1)
                    if is_pic and aid in asset_sha_by_id:
                        try:
                            bsha = _blob_sha256(sh.image.blob)
                        except Exception:
                            bsha = ""
                        if bsha != asset_sha_by_id[aid]:
                            unbound.append(
                                f"slide {idx}: {name} sha256 mismatch (blob={bsha[:10]} != {aid}:{asset_sha_by_id[aid][:10]})"
                            )
                    continue
                if m_u and m_u.group(1) in valid_inputs:
                    uid = m_u.group(1)
                    if is_pic and uid in input_sha_by_id:
                        try:
                            bsha = _blob_sha256(sh.image.blob)
                        except Exception:
                            bsha = ""
                        if bsha != input_sha_by_id[uid]:
                            unbound.append(
                                f"slide {idx}: {name} sha256 mismatch (blob={bsha[:10]} != {uid}:{input_sha_by_id[uid][:10]})"
                            )
                    continue
                unbound.append(f"slide {idx}: {name or sh.shape_type.name}")
                continue

            # Connectors MUST be bound DIAGRAM_* AND connected at both ends to bound diagram nodes
            if is_conn:
                md = _DIAGRAM_BIND_RE.match(name)
                valid_diag = bool(md and ((md.group(1) and md.group(1) in valid_assets) or (md.group(2) and md.group(2) in valid_inputs)))
                if not valid_diag:
                    unbound.append(f"slide {idx}: unbound connector {name or 'LINE'} (connectors must be bound DIAGRAM_*)")
                    continue
                st_id, end_id = _connector_endpoint_ids(sh)
                if st_id is None or end_id is None or st_id not in slide_diagram_node_ids or end_id not in slide_diagram_node_ids:
                    unbound.append(
                        f"slide {idx}: connector {name} endpoints ({st_id}->{end_id}) are not both connected to bound DIAGRAM_* nodes"
                    )
                continue

            # Freeform / Group shapes are never allowed as structural elements
            if sh.shape_type in (MSO_SHAPE_TYPE.FREEFORM, MSO_SHAPE_TYPE.GROUP):
                unbound.append(f"slide {idx}: disallowed {sh.shape_type.name} shape {name or sh.shape_type.name}")
                continue

            if m_ph:
                if not all_request_ids or m_ph.group(1) in all_request_ids:
                    continue
                unbound.append(f"slide {idx}: unknown placeholder {name}")
                continue
            if m_a and m_a.group(1) in valid_assets:
                continue
            if m_u and m_u.group(1) in valid_inputs:
                continue

            # Non-visual structural shapes: exact declared whitelist + strict shape-type restriction (B9 fix)
            if allowed_names_for_slide is not None:
                is_declared_name = name in allowed_names_for_slide
            else:
                is_declared_name = (name in _FALLBACK_STRUCTURAL_EXACT) or bool(_FALLBACK_CAPTION_RE.match(name))

            if is_declared_name:
                type_ok, type_why = _is_allowed_structural_shape_type(sh)
                if type_ok:
                    continue
                unbound.append(f"slide {idx}: structural element {name} has {type_why}")
                continue

            unbound.append(f"slide {idx}: undeclared/unknown element {name or sh.shape_type.name} ({sh.shape_type.name})")

    fab_res = GateResult(
        gate="no_fabricated_visuals",
        status="fail" if unbound else "pass",
        detail=f"unbound/unknown visual element(s): {', '.join(unbound[:6])}" if unbound else "all visual elements sha256-verified and shapes whitelisted",
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
                candidate_aids: set[str] = set()
                m_a = _ASSET_BIND_RE.match(sh.name or "")
                if m_a:
                    candidate_aids.add(m_a.group(1))
                # Also identify asset by picture blob sha256 so renaming cannot bypass attribution check
                if sh.shape_type is MSO_SHAPE_TYPE.PICTURE:
                    try:
                        bsha = _blob_sha256(sh.image.blob)
                        candidate_aids.update(sha_to_asset_ids.get(bsha, ()))
                    except Exception:
                        pass
                for aid in sorted(candidate_aids):
                    a = reg_by_id.get(aid)
                    if a and needs_attribution(a.license_or_terms or ""):
                        credit_ok = bool(
                            (a.attribution and a.attribution in combined)
                            or (a.author and a.author in combined)
                        )
                        if not credit_ok and f"slide {idx}:{aid}" not in missing_attr:
                            missing_attr.append(f"slide {idx}:{aid}")
        attr_res = GateResult(
            gate="attribution_present",
            status="fail" if missing_attr else "pass",
            detail=f"missing attribution on slide/notes for: {missing_attr}" if missing_attr else "all attribution-required assets credited",
        )

    # Cross-check deck_dump.json when present (COM read-back second evidence)
    dump_path = run_dir / "deck_dump.json"
    if dump_path.is_file() and opens_res.status == "pass":
        try:
            dump = DeckDump.model_validate_json(dump_path.read_text(encoding="utf-8"))
            if len(dump.slides) != n_slides:
                opens_res = GateResult(
                    gate="pptx_opens",
                    status="fail",
                    detail=f"deck_dump.json has {len(dump.slides)} slides != deck.pptx {n_slides}",
                )
        except Exception as e:
            opens_res = GateResult(
                gate="pptx_opens",
                status="fail",
                detail=f"deck_dump.json invalid: {type(e).__name__}: {str(e)[:120]}",
            )

    return [fab_res, ph_res, opens_res, native_res, attr_res]


def _looks_like_legacy_header_bullets_box(page) -> bool:
    """Detect the legacy 2010-memo layout structurally: bg_header + left bullets + right big box."""
    has_bg_header = any(e.element_id == "bg_header" and e.kind is ElementKind.shape for e in page.elements)
    if not has_bg_header:
        return False
    has_left_bullets = any(
        e.kind is ElementKind.bullet and e.x_in <= 1.2 and e.w_in <= 6.5 for e in page.elements
    )
    has_right_box = any(
        e.kind in (ElementKind.placeholder, ElementKind.image, ElementKind.chart, ElementKind.table)
        and e.x_in >= 5.5
        and (e.w_in * e.h_in) >= 12.0
        for e in page.elements
    )
    return has_left_bullets and has_right_box


def _check_layout_diversity_gate(run_dir: Path, design: PageDesign | None) -> GateResult:
    """Hard gate enforcing layout diversity, legacy-layout cap, and compact non-hero placeholders."""
    if design is None:
        return GateResult(
            gate="layout_diversity",
            status="not_run",
            detail="page_design.json absent (stage F not run)",
        )

    pages = design.pages
    n = len(pages)
    failures: list[str] = []
    layout_ids: list[str] = []
    legacy_pages: list[str] = []

    for page in pages:
        raw_id = (page.layout_id or page.layout_name or "").strip()
        lid = normalize_layout_id(raw_id)
        if not lid:
            failures.append(f"{page.page_id}: missing layout_id")
            lid = "unspecified"
        if lid == LEGACY_LAYOUT_ID or _looks_like_legacy_header_bullets_box(page):
            lid = LEGACY_LAYOUT_ID
            legacy_pages.append(page.page_id)
        layout_ids.append(lid)

        # Placeholder compactness & non-hero visual checks (<= 15% content area; not largest content slot)
        content_area = slide_content_area_in2(page.canvas_width_in, page.canvas_height_in)
        placeholders = [e for e in page.elements if e.kind is ElementKind.placeholder]
        if placeholders:
            primary_areas = [
                e.w_in * e.h_in
                for e in page.elements
                if e.kind not in (ElementKind.placeholder, ElementKind.shape, ElementKind.caption, ElementKind.footer)
                or (e.kind is ElementKind.shape and e.role == "diagram_json")
            ]
            max_primary = max(primary_areas, default=0.0)
            for ph in placeholders:
                ph_area = ph.w_in * ph.h_in
                ratio = ph_area / max(content_area, 1e-6)
                if ratio > MAX_PLACEHOLDER_AREA_RATIO + 1e-6:
                    failures.append(
                        f"{page.page_id}.{ph.element_id}: placeholder area {ratio:.1%} > {MAX_PLACEHOLDER_AREA_RATIO:.0%} of content area"
                    )
                if lid != "needs-input-list" and max_primary > 0 and ph_area >= max_primary:
                    failures.append(
                        f"{page.page_id}.{ph.element_id}: placeholder occupies largest content slot ({ph_area:.1f} >= {max_primary:.1f} sq in)"
                    )

    # 1. Adjacent slides must NOT share the same layout_id
    for i in range(1, n):
        if layout_ids[i] == layout_ids[i - 1]:
            failures.append(
                f"adjacent slides {pages[i - 1].page_id}/{pages[i].page_id} repeat layout_id {layout_ids[i]!r}"
            )

    # 2. Deck with >= 4 slides must use at least min(5, n) distinct layouts
    distinct = sorted(set(layout_ids))
    if n >= 4:
        min_required = min(5, n)
        if len(distinct) < min_required:
            failures.append(
                f"insufficient layout diversity: {len(distinct)} distinct layout(s) across {n} slides (need >= {min_required}; got {layout_ids})"
            )

    # 3. Legacy top-bar + left-bullets + right-big-box layout may appear at most once per deck
    if len(legacy_pages) > 1:
        failures.append(
            f"legacy layout {LEGACY_LAYOUT_ID!r} used on {len(legacy_pages)} slides {legacy_pages} (max 1 allowed)"
        )

    # 4. Cross-check compiled deck_ops.json preserves layout_id on add_slide ops
    ops_path = run_dir / "deck_ops.json"
    if ops_path.is_file():
        try:
            ops = load_contract(DeckOps, ops_path)
            compiled_lids = [
                normalize_layout_id(str(o.params.get("layout_id") or o.params.get("layout_name") or ""))
                for o in ops.ops
                if o.kind is DeckOpKind.add_slide
            ]
            if len(compiled_lids) == n:
                for page, expected_lid, got_lid in zip(pages, layout_ids, compiled_lids):
                    if got_lid and expected_lid != LEGACY_LAYOUT_ID and got_lid != expected_lid:
                        failures.append(
                            f"{page.page_id}: deck_ops layout_id {got_lid!r} != page_design {expected_lid!r}"
                        )
        except Exception:
            pass

    if failures:
        return GateResult(
            gate="layout_diversity",
            status="fail",
            detail="; ".join(failures[:6]),
        )
    return GateResult(
        gate="layout_diversity",
        status="pass",
        detail=f"{len(distinct)} distinct layout(s) across {n} slide(s): {', '.join(layout_ids)}",
    )


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
    design = loaded.get("page_design")
    results.append(_check_layout_diversity_gate(run_dir, design if isinstance(design, PageDesign) else None))
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
