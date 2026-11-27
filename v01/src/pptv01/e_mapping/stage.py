"""Stage E: Slide Asset Mapping (slide_asset_mapping.json).

Maps ONLY genuinely available assets (acquired/drawn/substituted or user-provided) to slide pages.
Missing or open user requests are NEVER mapped as available assets.
If PPTV01_E_VISION_MODEL is configured, runs visual relevance checks on mapped images;
otherwise honestly records relevance='not_assessed'.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pptv01.contracts import (
    AssetId,
    AssetRegistry,
    AssetRequirements,
    MappingEntry,
    NonEmptyStr,
    PageId,
    ProductionPlan,
    RequestId,
    SCHEMA_VERSION,
    SlideAssetMapping,
    SlidePlan,
    UnusedAsset,
    UserInputRequests,
    write_contract,
)
from pptv01.bivia_judge import (
    BiviaJudge,
    bivia_session,
    encode_image_for_bivia,
    format_bivia_probability,
    guarded_verdict,
    record_bivia_outcome,
)
from pptv01.llm import ChatClient, LLMConfigError, OpenAICompatClient, complete_json

from .prompt import SYSTEM_PROMPT, USER_TEMPLATE, VISION_PROMPT
from .validate import UsableAsset, build_usable_pool, validate_mapping

ARTIFACT = "slide_asset_mapping.json"
ENV_E_VISION_MODEL = "PPTV01_E_VISION_MODEL"
ENV_E_VISION_BASE_URL = "PPTV01_E_VISION_BASE_URL"
ENV_E_VISION_API_KEY = "PPTV01_E_VISION_API_KEY"
ENV_E_VISION_TIMEOUT = "PPTV01_E_VISION_TIMEOUT_S"

_RASTER_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


class MappingRuleError(ValueError):
    pass


class DraftMappingEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_id: PageId
    asset_id: AssetId | None = None
    request_id: RequestId | None = None
    usage: NonEmptyStr
    accepted: bool

    @model_validator(mode="after")
    def _has_ref(self) -> "DraftMappingEntry":
        if not self.asset_id and not self.request_id:
            raise ValueError("mapping entry must specify asset_id or request_id")
        return self


class DraftUnusedAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: AssetId | None = None
    request_id: RequestId | None = None
    reason: NonEmptyStr

    @model_validator(mode="after")
    def _has_ref(self) -> "DraftUnusedAsset":
        if not self.asset_id and not self.request_id:
            raise ValueError("unused_assets entry must specify asset_id or request_id")
        return self


class DraftSlideAssetMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mappings: list[DraftMappingEntry] = Field(default_factory=list)
    unused_assets: list[DraftUnusedAsset] = Field(default_factory=list)


class VisionVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    relevance: Literal["relevant", "weak", "irrelevant"]
    reason: NonEmptyStr


def vision_client_from_env(env: Mapping[str, str] | None = None) -> OpenAICompatClient | None:
    """Return a vision ChatClient iff PPTV01_E_VISION_MODEL is set; None otherwise (no fallback)."""
    env = os.environ if env is None else env
    model = env.get(ENV_E_VISION_MODEL, "").strip()
    if not model:
        return None
    base_url = (env.get(ENV_E_VISION_BASE_URL, "").strip() or env.get("PPTV01_LLM_BASE_URL", "").strip())
    api_key = (env.get(ENV_E_VISION_API_KEY, "").strip() or env.get("PPTV01_LLM_API_KEY", "").strip())
    if not base_url or not api_key:
        raise LLMConfigError(
            f"{ENV_E_VISION_MODEL} is set ({model}) but base URL / API key are missing "
            f"(set {ENV_E_VISION_BASE_URL}/{ENV_E_VISION_API_KEY} or PPTV01_LLM_BASE_URL/PPTV01_LLM_API_KEY)"
        )
    try:
        timeout = float(env.get(ENV_E_VISION_TIMEOUT, "60"))
    except ValueError as e:
        raise LLMConfigError(f"invalid {ENV_E_VISION_TIMEOUT}: {e}") from None
    return OpenAICompatClient(base_url=base_url, api_key=api_key, model=model, timeout_s=timeout)


def _normalize_ref(
    asset_id: str | None, request_id: str | None, pool: dict[str, UsableAsset]
) -> tuple[str | None, str | None]:
    if asset_id and asset_id in pool:
        u = pool[asset_id]
        return u.asset_id, request_id or u.request_id
    if request_id:
        if request_id in pool:
            u = pool[request_id]
            return u.asset_id, u.request_id
        for u in pool.values():
            if u.request_id == request_id:
                return u.asset_id, u.request_id
    return asset_id, request_id


def build_mapping(
    raw: Any,
    production: ProductionPlan,
    slide_plan: SlidePlan,
    reg: AssetRegistry,
    uir: UserInputRequests | None,
    run_dir: Path,
) -> SlideAssetMapping:
    draft = DraftSlideAssetMapping.model_validate(raw)
    pool = build_usable_pool(reg, uir, run_dir)
    mappings: list[MappingEntry] = []
    for m in draft.mappings:
        aid, rid = _normalize_ref(m.asset_id, m.request_id, pool)
        mappings.append(
            MappingEntry(
                page_id=m.page_id,
                asset_id=aid,
                request_id=rid,
                usage=m.usage,
                accepted=m.accepted,
                relevance="not_assessed",
                relevance_note="",
            )
        )
    unused: list[UnusedAsset] = []
    for u in draft.unused_assets:
        aid, rid = _normalize_ref(u.asset_id, u.request_id, pool)
        unused.append(UnusedAsset(asset_id=aid, request_id=rid, reason=u.reason))

    mapping = SlideAssetMapping(
        schema_version=SCHEMA_VERSION,
        run_id=production.run_id,
        mappings=mappings,
        unused_assets=unused,
    )
    problems = validate_mapping(mapping, slide_plan, reg, uir, run_dir)
    if problems:
        raise MappingRuleError("slide asset mapping rejected:\n- " + "\n- ".join(problems))
    return mapping


def _move_rejected_to_unused(
    entries: list[MappingEntry],
    existing_unused: list[UnusedAsset],
    rejected: dict[str, list[str]],
    pool: dict[str, UsableAsset],
    *,
    reason_prefix: str,
) -> list[UnusedAsset]:
    still_accepted = {(m.asset_id or m.request_id) for m in entries if m.accepted}
    unused = list(existing_unused)
    already_unused = {(u.asset_id or u.request_id) for u in unused}
    for key, reasons in rejected.items():
        if key not in still_accepted and key not in already_unused:
            u = pool[key]
            unused.append(
                UnusedAsset(
                    asset_id=u.asset_id,
                    request_id=u.request_id,
                    reason=f"{reason_prefix}: {'; '.join(reasons)}",
                )
            )
    return unused


def _merge_note(existing: str, extra: str) -> str:
    base = (existing or "").strip()
    return f"{base}; {extra}" if base else extra


def _apply_bivia_e_gate(
    mapping: SlideAssetMapping,
    production: ProductionPlan,
    slide_plan: SlidePlan,
    pool: dict[str, UsableAsset],
    run_dir: Path,
    bivia: BiviaJudge,
    *,
    stats: dict[str, int] | None = None,
) -> SlideAssetMapping:
    page_by_id = {p.page_id: p for p in slide_plan.pages}
    updated: list[MappingEntry] = []
    rejected: dict[str, list[str]] = {}

    for m in mapping.mappings:
        if not m.accepted:
            updated.append(m)
            continue
        key = m.asset_id or m.request_id or ""
        asset = pool.get(key)
        if asset is None:
            updated.append(m)
            continue
        page = page_by_id.get(m.page_id)
        slide_ctx = (
            f"topic: {production.topic}\n"
            f"page_id: {m.page_id}\n"
            f"title: {page.title if page else ''}\n"
            f"key_points: {'; '.join(page.key_points) if page else ''}\n"
            f"planned_usage: {m.usage}\n"
            f"asset_note: {asset.title_or_note}\n"
            f"source: {asset.source}"
        )
        is_raster = Path(asset.file_path).suffix.lower() in _RASTER_SUFFIXES
        if is_raster:
            try:
                b64, mime = encode_image_for_bivia(run_dir / asset.file_path)
            except OSError as exc:
                record_bivia_outcome(stats, "not_run")
                updated.append(m.model_copy(update={
                    "relevance_note": _merge_note(m.relevance_note, f"bivia: not_run (read_error: {type(exc).__name__})"),
                }))
                continue
            verdict = guarded_verdict(
                bivia.judge_image_veto,
                "Does this image genuinely match the required entity/scene for this slide and planned usage "
                "(not a wrong place, wrong object, or unrelated decorative graphic)?",
                slide_ctx,
                b64,
                mime=mime,
                mode="deep",
            )
            record_bivia_outcome(stats, verdict.outcome)
            if verdict.outcome == "veto":
                rejected.setdefault(key, []).append(
                    f"{m.page_id}: bivia image veto ({format_bivia_probability(verdict.probability)})"
                )
                updated.append(m.model_copy(update={
                    "accepted": False,
                    "relevance": "irrelevant",
                    "relevance_note": _merge_note(
                        m.relevance_note,
                        f"bivia veto ({format_bivia_probability(verdict.probability)}): image vetoed — 待补充替换素材",
                    ),
                }))
            elif verdict.outcome == "uncertain":
                reason_tag = verdict.reason or "low_confidence"
                rejected.setdefault(key, []).append(f"{m.page_id}: bivia uncertain ({reason_tag}) 待补充人工复核")
                updated.append(m.model_copy(update={
                    "accepted": False,
                    "relevance": "not_assessed",
                    "relevance_note": _merge_note(
                        m.relevance_note,
                        f"bivia uncertain ({reason_tag}): 待补充 / needs_review (not passed)",
                    ),
                }))
            elif verdict.outcome == "not_vetoed":
                # Image yes is veto-only, NEVER upgrades relevance to 'relevant'.
                updated.append(m.model_copy(update={
                    "relevance_note": _merge_note(m.relevance_note, "bivia: not_vetoed (image yes is not proof)"),
                }))
            else:
                updated.append(m.model_copy(update={
                    "relevance_note": _merge_note(m.relevance_note, f"bivia: not_run ({verdict.reason or 'error'})"),
                }))
        elif asset.kind == "reference":
            ref_path = run_dir / asset.file_path
            ref_snippet = ""
            if ref_path.is_file():
                try:
                    ref_snippet = ref_path.read_text(encoding="utf-8", errors="ignore")[:1500]
                except OSError:
                    ref_snippet = ""
            verdict = guarded_verdict(
                bivia.judge_boolean,
                "Are this slide's claims and citation usage supported by and pertinent to this reference record?",
                f"{slide_ctx}\nreference_record:\n{ref_snippet}",
                mode="deep",
            )
            record_bivia_outcome(stats, verdict.outcome)
            if verdict.outcome == "pass":
                updated.append(m.model_copy(update={
                    "relevance": "relevant",
                    "relevance_note": _merge_note(
                        m.relevance_note,
                        f"bivia: pass ({format_bivia_probability(verdict.probability)})",
                    ),
                }))
            elif verdict.outcome == "veto":
                rejected.setdefault(key, []).append(
                    f"{m.page_id}: bivia reference veto ({format_bivia_probability(verdict.probability)})"
                )
                updated.append(m.model_copy(update={
                    "accepted": False,
                    "relevance": "irrelevant",
                    "relevance_note": _merge_note(
                        m.relevance_note,
                        f"bivia veto ({format_bivia_probability(verdict.probability)}): reference unsupported — 待补充",
                    ),
                }))
            elif verdict.outcome == "uncertain":
                reason_tag = verdict.reason or "low_confidence"
                rejected.setdefault(key, []).append(f"{m.page_id}: bivia uncertain ({reason_tag}) 待补充人工复核")
                updated.append(m.model_copy(update={
                    "accepted": False,
                    "relevance": "not_assessed",
                    "relevance_note": _merge_note(
                        m.relevance_note,
                        f"bivia uncertain ({reason_tag}): 待补充 / needs_review (not passed)",
                    ),
                }))
            else:
                updated.append(m.model_copy(update={
                    "relevance_note": _merge_note(m.relevance_note, f"bivia: not_run ({verdict.reason or 'error'})"),
                }))
        else:
            updated.append(m)

    unused = _move_rejected_to_unused(
        updated,
        mapping.unused_assets,
        rejected,
        pool,
        reason_prefix="bivia gate rejected (待补充)",
    )
    return SlideAssetMapping(
        schema_version=mapping.schema_version,
        run_id=mapping.run_id,
        mappings=updated,
        unused_assets=unused,
    )


def _assess_visual_relevance(
    mapping: SlideAssetMapping,
    slide_plan: SlidePlan,
    pool: dict[str, UsableAsset],
    run_dir: Path,
    vision_client: ChatClient | None,
) -> SlideAssetMapping:
    page_by_id = {p.page_id: p for p in slide_plan.pages}
    updated_entries: list[MappingEntry] = []
    rejected_by_vlm: dict[str, list[str]] = {}

    for m in mapping.mappings:
        key = m.asset_id or m.request_id or ""
        asset = pool.get(key)
        is_raster = bool(asset and Path(asset.file_path).suffix.lower() in _RASTER_SUFFIXES)
        if not m.accepted:
            updated_entries.append(m.model_copy(update={
                "relevance": "not_assessed",
                "relevance_note": "not selected (accepted=false)",
            }))
            continue
        if not is_raster:
            updated_entries.append(m.model_copy(update={
                "relevance": "not_assessed",
                "relevance_note": "non-raster asset (citation/structured data/vector)",
            }))
            continue
        if vision_client is None:
            updated_entries.append(m.model_copy(update={
                "relevance": "not_assessed",
                "relevance_note": f"no vision model configured ({ENV_E_VISION_MODEL} unset); image content not visually inspected",
            }))
            continue

        page = page_by_id[m.page_id]
        img_path = Path(run_dir) / asset.file_path
        prompt = VISION_PROMPT.format(
            page_id=page.page_id,
            title=page.title,
            goal=page.goal,
            key_points="; ".join(page.key_points),
            usage=m.usage,
        )
        try:
            res = complete_json(
                vision_client,
                [{"role": "user", "content": prompt, "images": [str(img_path)]}],
                VisionVerdict.model_validate,
            )
            verdict: VisionVerdict = res.value
            if verdict.relevance == "irrelevant":
                rejected_by_vlm.setdefault(key, []).append(f"{m.page_id}: {verdict.reason}")
                updated_entries.append(m.model_copy(update={
                    "accepted": False,
                    "relevance": "irrelevant",
                    "relevance_note": f"VLM ({vision_client.model_id}): {verdict.reason}",
                }))
            else:
                updated_entries.append(m.model_copy(update={
                    "relevance": verdict.relevance,
                    "relevance_note": f"VLM ({vision_client.model_id}): {verdict.reason}",
                }))
        except Exception as e:  # noqa: BLE001 - record failure honestly rather than faking assessment
            updated_entries.append(m.model_copy(update={
                "relevance": "not_assessed",
                "relevance_note": f"vision check failed ({type(e).__name__}): {str(e)[:120]}",
            }))

    # If VLM rejected all accepted mappings for an asset, move it into unused_assets
    unused = _move_rejected_to_unused(
        updated_entries,
        mapping.unused_assets,
        rejected_by_vlm,
        pool,
        reason_prefix="visual relevance check marked irrelevant",
    )

    return SlideAssetMapping(
        schema_version=mapping.schema_version,
        run_id=mapping.run_id,
        mappings=updated_entries,
        unused_assets=unused,
    )


def run_stage_e(
    production: ProductionPlan,
    slide_plan: SlidePlan,
    reqs: AssetRequirements | None,
    reg: AssetRegistry,
    uir: UserInputRequests | None,
    client: ChatClient,
    run_dir: Path | None = None,
    *,
    vision_client: ChatClient | None = None,
    bivia: BiviaJudge | None = None,
    bivia_stats_out: dict[str, int] | None = None,
) -> tuple[SlideAssetMapping, list[dict[str, Any]]]:
    run_dir_p = Path(run_dir) if run_dir is not None else Path(".")
    planned_by_asset: dict[str, list[str]] = (
        {r.asset_id: list(r.page_ids) for r in reqs.requirements} if reqs else {}
    )
    pool = build_usable_pool(reg, uir, run_dir_p, planned_by_asset)

    if not pool:
        mapping = SlideAssetMapping(
            schema_version=SCHEMA_VERSION,
            run_id=production.run_id,
            mappings=[],
            unused_assets=[],
        )
        if run_dir is not None:
            write_contract(mapping, run_dir_p / ARTIFACT)
        return mapping, []

    pages_view = [
        {
            "page_id": p.page_id,
            "title": p.title,
            "goal": p.goal,
            "key_points": p.key_points,
            "visual_intent": p.visual_intent,
        }
        for p in slide_plan.pages
    ]
    assets_view = [
        {
            "asset_id": u.asset_id,
            "request_id": u.request_id,
            "kind": u.kind,
            "file_path": u.file_path,
            "title_or_note": u.title_or_note,
            "source": u.source,
            "license": u.license,
            "planned_pages": u.planned_pages,
        }
        for u in pool.values()
    ]
    missing_view = [
        {"asset_id": a.asset_id, "type": a.type.value if a.type else "unknown", "notes": a.notes}
        for a in reg.assets
        if a.asset_id not in pool
    ]

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": USER_TEMPLATE.format(
                topic=production.topic,
                language=production.language,
                pages_json=json.dumps(pages_view, ensure_ascii=False, indent=2),
                assets_json=json.dumps(assets_view, ensure_ascii=False, indent=2),
                missing_json=json.dumps(missing_view, ensure_ascii=False, indent=2) if missing_view else "[]",
            ),
        },
    ]

    res = complete_json(
        client,
        messages,
        lambda raw: build_mapping(raw, production, slide_plan, reg, uir, run_dir_p),
    )
    mapping: SlideAssetMapping = res.value

    own_vision = None
    try:
        v_cli = vision_client if vision_client is not None else vision_client_from_env()
        if vision_client is None and v_cli is not None:
            own_vision = v_cli
        mapping = _assess_visual_relevance(mapping, slide_plan, pool, run_dir_p, v_cli)
    finally:
        if own_vision is not None:
            own_vision.close()

    with bivia_session(bivia, bivia_stats_out) as eff_bivia:
        if eff_bivia is not None:
            gate_stats = dict(bivia_stats_out or {})
            try:
                mapping = _apply_bivia_e_gate(
                    mapping, production, slide_plan, pool, run_dir_p, eff_bivia, stats=gate_stats,
                )
            except Exception:  # Optional gate preparation; keep the deterministic mapping.
                record_bivia_outcome(bivia_stats_out, "not_run")
            else:
                if bivia_stats_out is not None:
                    bivia_stats_out.update(gate_stats)

    if run_dir is not None:
        write_contract(mapping, run_dir_p / ARTIFACT)
    return mapping, res.attempts
