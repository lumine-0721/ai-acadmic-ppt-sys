"""Deterministic validation and usable-pool construction for stage E (Slide Asset Mapping)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pptv01.contracts import (
    AssetRegistry,
    AssetStatus,
    SlideAssetMapping,
    SlidePlan,
    UserInputRequests,
    UserRequestStatus,
)

GENERIC_USAGE = re.compile(
    r"^(?:配图|装饰|美化|点缀|占位|插图|相关图片|相关素材|图片|素材|image|picture|photo|decoration|placeholder)[。！!.]?$",
    re.I,
)
USABLE_ASSET_STATUSES = {AssetStatus.acquired, AssetStatus.drawn, AssetStatus.substituted}


@dataclass(frozen=True)
class UsableAsset:
    key: str                  # canonical key: asset_id if present, else request_id
    asset_id: str | None
    request_id: str | None
    kind: str
    file_path: str
    title_or_note: str
    source: str
    license: str
    planned_pages: list[str]


def build_usable_pool(
    reg: AssetRegistry,
    uir: UserInputRequests | None,
    run_dir: Path,
    planned_pages_by_asset: dict[str, list[str]] | None = None,
) -> dict[str, UsableAsset]:
    """Return ONLY real assets on disk (acquired/drawn/substituted or provided U###)."""
    run_dir = Path(run_dir)
    planned_pages_by_asset = planned_pages_by_asset or {}
    pool: dict[str, UsableAsset] = {}

    # 1) registry assets that actually exist on disk
    for a in reg.assets:
        if a.status in USABLE_ASSET_STATUSES and a.file_path and (run_dir / a.file_path).is_file():
            pool[a.asset_id] = UsableAsset(
                key=a.asset_id,
                asset_id=a.asset_id,
                request_id=None,
                kind=a.type.value if a.type else "other",
                file_path=a.file_path,
                title_or_note=a.identifier or a.notes or a.asset_id,
                source=a.source_url or a.source_name or "asset_registry",
                license=a.license_or_terms or "",
                planned_pages=planned_pages_by_asset.get(a.asset_id, []),
            )

    # 2) user-provided inputs (provided status + file exists on disk)
    if uir is not None:
        for r in uir.requests:
            if r.status is UserRequestStatus.provided and r.provided and (run_dir / r.provided.file).is_file():
                key = r.asset_id or r.request_id
                # Only add if not already acquired in registry; provided inputs answer missing assets
                if key not in pool:
                    pool[key] = UsableAsset(
                        key=key,
                        asset_id=r.asset_id,
                        request_id=r.request_id,
                        kind=r.kind.value,
                        file_path=r.provided.file,
                        title_or_note=r.need,
                        source=r.provided.source,
                        license=r.provided.license,
                        planned_pages=list(r.page_ids),
                    )
    return pool


def _ref_key(asset_id: str | None, request_id: str | None, pool: dict[str, UsableAsset]) -> str | None:
    if asset_id and asset_id in pool:
        return asset_id
    if request_id and request_id in pool:
        return request_id
    if request_id:
        for k, u in pool.items():
            if u.request_id == request_id:
                return k
    return None


def validate_mapping(
    mapping: SlideAssetMapping,
    slide_plan: SlidePlan,
    reg: AssetRegistry,
    uir: UserInputRequests | None,
    run_dir: Path,
) -> list[str]:
    problems: list[str] = []
    pages = {p.page_id for p in slide_plan.pages}
    pool = build_usable_pool(reg, uir, run_dir)
    missing_ids = {a.asset_id for a in reg.assets if a.asset_id not in pool}
    open_reqs = {
        r.request_id
        for r in (uir.requests if uir else [])
        if r.status is not UserRequestStatus.provided
    }

    accepted_keys: set[str] = set()
    usages_by_key: dict[str, list[str]] = {}

    for m in mapping.mappings:
        label = f"({m.page_id}, {m.asset_id or m.request_id})"
        if m.page_id not in pages:
            problems.append(f"{label}: page_id {m.page_id!r} does not exist in slide_plan")
        if m.asset_id in missing_ids:
            problems.append(f"{label}: asset {m.asset_id} is missing/unprovided and MUST NOT be mapped")
            continue
        if m.request_id in open_reqs:
            problems.append(f"{label}: request {m.request_id} is not provided and MUST NOT be mapped")
            continue
        k = _ref_key(m.asset_id, m.request_id, pool)
        if k is None:
            problems.append(f"{label}: asset/request is not in the usable asset pool")
            continue
        usage = m.usage.strip()
        if len(usage) < 6 or GENERIC_USAGE.match(usage):
            problems.append(f"{label}: usage {m.usage!r} is too generic; describe what it does on {m.page_id}")
        if m.accepted:
            accepted_keys.add(k)
            usages_by_key.setdefault(k, []).append(usage)

    # Multi-page reuse must give distinct, page-specific usage
    for k, usages in usages_by_key.items():
        if len(usages) > 1 and len({u.casefold() for u in usages}) < len(usages):
            problems.append(f"{k}: reused across {len(usages)} pages with identical usage; write distinct usage per page")

    unused_keys: set[str] = set()
    for u in mapping.unused_assets:
        label = f"unused({u.asset_id or u.request_id})"
        if u.asset_id in missing_ids:
            problems.append(f"{label}: missing asset should not be in unused_assets (it was never acquired)")
            continue
        k = _ref_key(u.asset_id, u.request_id, pool)
        if k is None:
            problems.append(f"{label}: not in the usable asset pool")
            continue
        if len(u.reason.strip()) < 6:
            problems.append(f"{label}: reason {u.reason!r} is too short; explain why the asset was not used")
        if k in unused_keys:
            problems.append(f"{label}: duplicate unused_assets entry")
        unused_keys.add(k)

    # Every usable asset must be either accepted on >=1 page OR explained in unused_assets
    unaccounted = sorted(set(pool) - accepted_keys - unused_keys)
    if unaccounted:
        problems.append(f"usable assets neither accepted on any page nor listed in unused_assets: {unaccounted}")

    return problems
