"""Append-only execution journal. One process-wide lock keeps parallel nodes from tearing lines."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()


def append_event(run_dir: str, event: dict[str, Any]) -> None:
    path = Path(run_dir) / "exec_log.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, ensure_ascii=False) + "\n"
    with _LOCK:
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)


def read_events(run_dir: str) -> list[dict]:
    path = Path(run_dir) / "exec_log.jsonl"
    if not path.is_file():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events
