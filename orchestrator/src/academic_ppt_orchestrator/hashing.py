"""Content hashes. Cache identity is never a filename or mtime."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def hash_tree(path: Path) -> str:
    path = path.resolve()
    digest = hashlib.sha256()
    if path.is_file():
        digest.update(b"F")
        digest.update(sha256_file(path).encode("ascii"))
        return digest.hexdigest()
    files = [item for item in path.rglob("*") if item.is_file()]
    for item in sorted(files, key=lambda candidate: candidate.relative_to(path).as_posix()):
        rel = item.relative_to(path).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256_file(item).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()
