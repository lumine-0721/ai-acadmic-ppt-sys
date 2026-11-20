"""Post-validation of the asset registry against C's requirements and the files on disk."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pptv01.contracts import AssetRegistry, AssetRequirements, AssetStatus


def validate_registry(reg: AssetRegistry, reqs: AssetRequirements, run_dir: Path) -> None:
    want = [r.asset_id for r in reqs.requirements]
    got = [a.asset_id for a in reg.assets]
    if got != want:
        raise ValueError(f"registry must have exactly one record per requirement, in order: want {want}, got {got}")
    by_req = {r.asset_id: r for r in reqs.requirements}
    seen_acquired: set[str] = set()
    for a in reg.assets:
        if a.type is not None and a.type is not by_req[a.asset_id].type:
            raise ValueError(f"{a.asset_id}: record type {a.type.value} != requirement type {by_req[a.asset_id].type.value}")
        if a.shared_with is not None and a.shared_with not in seen_acquired:
            raise ValueError(f"{a.asset_id}: shared_with={a.shared_with} must reference an earlier acquired asset")
        if a.status is AssetStatus.acquired:
            p = Path(run_dir) / a.file_path
            if not p.is_file() or p.stat().st_size == 0:
                raise ValueError(f"{a.asset_id}: acquired but {a.file_path} is missing/empty on disk")
            if a.sha256 and hashlib.sha256(p.read_bytes()).hexdigest() != a.sha256:
                raise ValueError(f"{a.asset_id}: sha256 does not match the file on disk")
            if not a.candidates:
                raise ValueError(f"{a.asset_id}: acquired without an audit trail (candidates)")
            seen_acquired.add(a.asset_id)
