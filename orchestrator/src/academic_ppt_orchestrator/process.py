"""Subprocess runner for existing module CLIs. Arguments are a list, never a shell string."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Sequence


class CommandResult:
    def __init__(self, argv: Sequence[str], returncode: int, stdout: str, stderr: str, duration_ms: int, timed_out: bool):
        self.argv = [str(part) for part in argv]
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.duration_ms = duration_ms
        self.timed_out = timed_out

    def as_dict(self) -> dict:
        return {
            "argv": self.argv,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
            "timed_out": self.timed_out,
        }


def run_command(argv: Sequence[str], timeout_seconds: float, cwd: Path | None = None) -> CommandResult:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            [str(part) for part in argv],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", "replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", "replace")
        return CommandResult(argv, 124, stdout, stderr + "\nTIMEOUT", duration_ms, True)
    duration_ms = int((time.perf_counter() - started) * 1000)
    return CommandResult(argv, completed.returncode, completed.stdout, completed.stderr, duration_ms, False)
