"""Opt-in veto gate using Articul8 Bivia /boolean endpoint.

This module provides an opt-in boolean judgment capability.  It is disabled by
default (ENV_FLAG must be set and ENV_KEY must be non-empty).  Any failure is
fail-open: the pipeline never aborts because of this judge.

IMPORTANT – image judgment is a veto only, never a proof:
    A "yes" result from the model can never alone prove that an image depicts a
    particular entity.  (Lesson B15: a New York photo was given p=0.97 and
    judged "Beijing".)  The `judge_image_veto` method therefore maps "pass"
    outcomes to "not_vetoed" so callers cannot treat it as positive evidence.
"""

from __future__ import annotations

import base64
import io
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import httpx

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://api.articul8.ai/bivia"

ENV_FLAG = "PPTV01_BIVIA_JUDGE"
ENV_KEY = "A8_API_KEY"
ENV_THRESHOLD = "PPTV01_BIVIA_THRESHOLD"
ENV_TIMEOUT = "PPTV01_BIVIA_TIMEOUT_S"

DEFAULT_THRESHOLD = 0.90
DEFAULT_TIMEOUT_S = 30.0
RETRIES = 2

# Module-level sleep function; may be overridden in tests.
_sleep = time.sleep


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class JudgeVerdict:
    """Result of a boolean judgment call.

    Attributes
    ----------
    outcome : str
        One of: pass | veto | uncertain | not_run | not_vetoed
    value : str | None
        Raw value from the API ("yes" / "no"), if available.
    probability : float | None
        Raw probability from the API, if available.
    status : str | None
        Raw status string from the API, if available.
    reason : str
        Human-readable explanation, defaults to empty string.
    """

    outcome: str
    value: str | None = None
    probability: float | None = None
    status: str | None = None
    reason: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def bivia_enabled(env: Mapping[str, str]) -> bool:
    """Return True when the gate is explicitly enabled and an API key is present.

    The flag must be one of "1" or "on" (case-insensitive) and the key must be
    non-empty after stripping whitespace.
    """
    flag = env.get(ENV_FLAG, "").strip().lower()
    key = env.get(ENV_KEY, "").strip()
    return flag in ("1", "on") and bool(key)


_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".webp": "image/webp",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}
_STAT_KEYS = ("pass", "not_vetoed", "veto", "uncertain", "not_run")


def encode_image_for_bivia(source: Path | str | bytes, *, suffix: str = ".jpg", max_px: int = 800) -> tuple[str, str]:
    """Encode an image path or blob as bounded base64 JPEG (falls back to raw bytes for synthetic stubs)."""
    if isinstance(source, (str, Path)):
        p = Path(source)
        raw = p.read_bytes()
        eff_suffix = p.suffix.lower() or suffix.lower()
    else:
        raw = bytes(source)
        eff_suffix = suffix.lower()

    try:
        from PIL import Image

        with Image.open(io.BytesIO(raw)) as im:
            rgb = im.convert("RGB")
            rgb.thumbnail((max_px, max_px))
            buf = io.BytesIO()
            rgb.save(buf, format="JPEG", quality=82)
            return base64.b64encode(buf.getvalue()).decode("ascii"), "image/jpeg"
    except Exception:  # noqa: BLE001 - mock/synthetic test bytes fall through cleanly
        mime = _MIME_BY_SUFFIX.get(eff_suffix, "image/jpeg")
        return base64.b64encode(raw).decode("ascii"), mime


def new_bivia_stats() -> dict[str, int]:
    return {k: 0 for k in _STAT_KEYS}


def record_bivia_outcome(stats: dict[str, int] | None, outcome: str) -> None:
    if stats is None:
        return
    key = outcome if outcome in _STAT_KEYS else "not_run"
    stats[key] = int(stats.get(key, 0)) + 1


def bivia_stage_substep_status(stats: dict[str, int]) -> str:
    total = sum(stats.values())
    if total > 0 and stats.get("not_run", 0) == total:
        return "not_run"
    return "done"


def format_bivia_probability(probability: float | None) -> str:
    return f"p={probability:.2f}" if isinstance(probability, (int, float)) else "p=?"


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class BiviaJudge:
    """Thin client for the Articul8 Bivia boolean judgment endpoint.

    Parameters
    ----------
    api_key : str
        Bearer token for the API.  Never included in repr / log output.
    threshold : float, default 0.90
        Minimum probability required for a definite pass or veto.
    timeout_s : float, default 30.0
        HTTP request timeout in seconds.
    transport : httpx.BaseTransport | None
        Optional transport for the underlying client (useful in tests).
    """

    def __init__(
        self,
        api_key: str,
        *,
        threshold: float = DEFAULT_THRESHOLD,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self.threshold = threshold
        self.timeout_s = timeout_s
        self._init_error: str | None = None

        try:
            self._client = httpx.Client(
                timeout=timeout_s,
                transport=transport,
            )
        except Exception as exc:  # pragma: no cover – defensive
            self._init_error = str(exc)
            self._client = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        # Intentionally omits api_key to satisfy the "key never in logs/repr" rule.
        return (
            f"BiviaJudge(threshold={self.threshold!r}, timeout_s={self.timeout_s!r})"
        )

    def close(self) -> None:
        client = getattr(self, "_client", None)
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> "BiviaJudge | None":
        """Factory: return None when the gate is not enabled.

        Threshold and timeout are read from the environment; invalid values
        silently fall back to defaults without raising.
        """
        env = os.environ if env is None else env

        if not bivia_enabled(env):
            return None

        key = env.get(ENV_KEY, "").strip()

        # Parse threshold, falling back on error.
        threshold = DEFAULT_THRESHOLD
        raw_threshold = env.get(ENV_THRESHOLD, "").strip()
        if raw_threshold:
            try:
                threshold = float(raw_threshold)
            except ValueError:
                pass  # keep default

        # Parse timeout, falling back on error.
        timeout_s = DEFAULT_TIMEOUT_S
        raw_timeout = env.get(ENV_TIMEOUT, "").strip()
        if raw_timeout:
            try:
                timeout_s = float(raw_timeout)
            except ValueError:
                pass  # keep default

        return cls(key, threshold=threshold, timeout_s=timeout_s, transport=transport)

    # ------------------------------------------------------------------
    # Core judgment methods
    # ------------------------------------------------------------------

    def judge_boolean(
        self,
        question: str,
        state: str = "",
        *,
        mode: str = "fast",
        think: bool = False,
        images: list[dict[str, Any]] | None = None,
    ) -> JudgeVerdict:
        """Call POST <BASE_URL>/boolean and return a JudgeVerdict.

        The method is fully fail-open: any error (HTTP failure, malformed
        response, missing fields, or a failed client init) results in
        ``outcome='not_run'`` and never raises.
        """
        if self._init_error is not None:
            return JudgeVerdict(
                outcome="not_run",
                reason=f"init_error:{self._init_error[:40]}",
            )

        payload: dict[str, Any] = {
            "question": question,
            "state": state,
            "mode": mode,
            "think": think,
        }
        if images:
            payload["images"] = images

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        # Retry loop with exponential back-off for 429 and 5xx.
        for attempt in range(RETRIES + 1):
            try:
                response = self._client.post(
                    f"{BASE_URL}/boolean",
                    json=payload,
                    headers=headers,
                )
                # Exponential back-off on retryable status codes.
                if attempt < RETRIES and response.status_code in (429, 500, 502, 503, 504):
                    wait = 1.0 * (2 ** attempt)
                    _sleep(wait)
                    continue
                response.raise_for_status()
                break
            except httpx.HTTPStatusError as exc:
                return JudgeVerdict(
                    outcome="not_run",
                    reason=f"http_error:{exc.response.status_code}",
                )
            except httpx.RequestError as exc:
                return JudgeVerdict(
                    outcome="not_run",
                    reason=f"request_error:{str(exc)[:40]}",
                )
            except Exception as exc:
                return JudgeVerdict(
                    outcome="not_run",
                    reason=f"unexpected:{str(exc)[:40]}",
                )

        # Parse response body.
        try:
            data = response.json()
        except Exception as exc:
            return JudgeVerdict(
                outcome="not_run",
                reason=f"json_error:{str(exc)[:40]}",
            )

        # Extract fields; missing fields are tolerated as fail-open.
        value: str | None = data.get("value")
        probability: float | None = data.get("probability")
        status: str | None = data.get("status")

        # Abstain / uncertain path.
        abstain = data.get("abstain", False)
        if status == "abstained" or abstain:
            return JudgeVerdict(
                outcome="uncertain",
                value=value,
                probability=probability,
                status=status,
                reason="abstained",
            )

        # Type validation.
        if not isinstance(value, str) or isinstance(probability, bool) or not isinstance(probability, (int, float)):
            return JudgeVerdict(
                outcome="not_run",
                reason="bad_response",
            )
        probability = float(probability)

        # Low confidence → uncertain.
        if probability is None or probability < self.threshold:
            return JudgeVerdict(
                outcome="uncertain",
                value=value,
                probability=probability,
                status=status,
                reason="low_confidence",
            )

        # Above threshold.
        if value == "yes":
            return JudgeVerdict(
                outcome="pass",
                value=value,
                probability=probability,
                status=status,
            )
        if value == "no":
            return JudgeVerdict(
                outcome="veto",
                value=value,
                probability=probability,
                status=status,
            )

        # Fallback for unexpected value strings.
        return JudgeVerdict(
            outcome="uncertain",
            value=value,
            probability=probability,
            status=status,
            reason="unexpected_value",
        )

    def judge_image_veto(
        self,
        question: str,
        state: str,
        image_b64: str,
        mime: str = "image/jpeg",
        *,
        mode: str = "fast",
    ) -> JudgeVerdict:
        """Judge whether an image should be vetoed based on the question.

        This is a *veto only* — a "pass" result cannot serve as proof that the
        image depicts the target entity.  The outcome is therefore mapped:
            pass → not_vetoed
        """
        verdict = self.judge_boolean(
            question,
            state,
            mode=mode,
            think=False,
            images=[{"b64": image_b64, "mime": mime}],
        )

        if verdict.outcome == "pass":
            return JudgeVerdict(
                outcome="not_vetoed",
                value=verdict.value,
                probability=verdict.probability,
                status=verdict.status,
                reason="image yes is not proof",
            )

        return verdict
