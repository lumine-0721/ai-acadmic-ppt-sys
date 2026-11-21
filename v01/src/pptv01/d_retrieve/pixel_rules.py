"""Deterministic pixel-level image quality & signage filter using fastembed CLIP + RapidOCR (R6-4).

Blocks text signs, screenshots, logos, document pages, and text-heavy posters
before Stage D accepts a Wikimedia Commons file as a slide photograph:
  1. CLIP zero-shot prompt check (``Qdrant/clip-ViT-B-32-vision`` + ``-text``,
     via ``fastembed``, MIT): rejects when any negative prompt score meets or
     exceeds ``positive_score + CLIP_NEG_REJECT_MARGIN``.
  2. RapidOCR text-box coverage check (``rapidocr-onnxruntime``, Apache-2.0):
     computes union area of detected text polygons relative to image pixel area
     and rejects photos exceeding ``OCR_TEXT_AREA_RATIO_MAX``. Exempt for
     ``diagram``, ``chart``, ``chart_data``, and ``table`` visual types.

Threshold calibration on ``v01/tests/fixtures/r6_pixel_images`` (run in repo with
``rapidocr-onnxruntime==1.2.3`` and ``fastembed==0.9.1`` CLIP ViT-B-32):
  * ``Sponge_City.png`` (roadside Chinese text sign):
    ``clip_pos=0.1827``, ``max_neg=0.2979 ('a sign with text')``,
    ``neg-pos=+0.1152``, ``ocr_ratio=0.1422`` -> REJECTED by both CLIP & OCR.
  * ``cafe_de_flore.jpg`` (real cafe facade with storefront awning sign):
    ``clip_pos=0.3035``, ``max_neg=0.2321``, ``neg-pos=-0.0714``,
    ``ocr_ratio=0.0415`` -> ACCEPTED.
  * ``coffee_pourover.jpg`` (brewing photo):
    ``clip_pos=0.2850``, ``max_neg=0.2101``, ``neg-pos=-0.0749``,
    ``ocr_ratio=0.0000`` -> ACCEPTED.
  * ``parthenon.jpg`` (architecture photo):
    ``clip_pos=0.3040``, ``max_neg=0.2356``, ``neg-pos=-0.0684``,
    ``ocr_ratio=0.0000`` -> ACCEPTED.

Setting ``OCR_TEXT_AREA_RATIO_MAX = 0.12`` catches text signs like ``Sponge_City.png``
(``0.1422``) independently even if CLIP is unavailable, while staying nearly 3x above
natural storefront text (``cafe_de_flore.jpg = 0.0415``). Setting
``CLIP_NEG_REJECT_MARGIN = 0.02`` leaves a ``0.18+`` separation margin between
signage (``+0.1152``) and photographs (``<= -0.0684``).
"""

from __future__ import annotations

import io
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Protocol

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from pptv01.contracts import AssetRequirement
from pptv01.llm import LLMConfigError
from pptv01.retrieval.backends import sanitize_url
from pptv01.retrieval.relevance import (
    ENV_FASTEMBED_CACHE,
    ENV_FASTEMBED_CACHE_ALT,
    ENV_LOAD_TIMEOUT,
)

ENV_PIXEL_RULES = "PPTV01_PIXEL_RULES"               # auto (default) | off
ENV_PIXEL_LOAD_TIMEOUT = "PPTV01_PIXEL_LOAD_TIMEOUT_S"

CLIP_VISION_MODEL = "Qdrant/clip-ViT-B-32-vision"
CLIP_TEXT_MODEL = "Qdrant/clip-ViT-B-32-text"

CLIP_NEGATIVE_PROMPTS: tuple[str, ...] = (
    "a sign with text",
    "a logo",
    "a screenshot of text",
    "a document page",
    "a poster with text",
    "a map legend",
)

CLIP_NEG_REJECT_MARGIN = 0.02
OCR_TEXT_AREA_RATIO_MAX = 0.12
OCR_MIN_CONFIDENCE = 0.30
OCR_EXEMPT_TYPES: frozenset[str] = frozenset({"diagram", "chart", "chart_data", "table"})

_DEFAULT_LOAD_TIMEOUT_S = 45.0
_LOAD_ENV_LOCK = threading.Lock()


@dataclass(frozen=True)
class ClipScoreResult:
    """Zero-shot CLIP cosine similarity scores for one candidate image."""

    positive_score: float
    negative_scores: dict[str, float]
    max_negative_label: str
    max_negative_score: float


class ClipScorer(Protocol):
    """Protocol for injectable CLIP zero-shot image-text scorers."""

    def score_image(
        self,
        image_bytes: bytes,
        positive_prompt: str,
        negative_prompts: tuple[str, ...],
    ) -> ClipScoreResult: ...


class OcrRunner(Protocol):
    """Protocol for injectable OCR text-area ratio estimators returning ratio in [0, 1]."""

    def __call__(self, image_bytes: bytes) -> float: ...


def _log(msg: str, progress_fn: Callable[[str], None] | None = None) -> None:
    line = f"[pixel-rules] {msg}"
    if progress_fn is not None:
        progress_fn(line)
    else:
        print(line, flush=True)


def default_fastembed_cache_dir(env: Mapping[str, str] | None = None) -> Path:
    """Resolve the fastembed cache directory matching ``pptv01.retrieval.relevance``."""
    e = os.environ if env is None else env
    configured = (
        e.get(ENV_FASTEMBED_CACHE)
        or e.get(ENV_FASTEMBED_CACHE_ALT)
        or os.environ.get(ENV_FASTEMBED_CACHE)
        or os.environ.get(ENV_FASTEMBED_CACHE_ALT)
    )
    if configured:
        return Path(configured)
    return Path(tempfile.gettempdir()) / "fastembed_cache"


def _has_cached_repo(cache_root: Path, repo_id: str) -> bool:
    slug = f"models--{repo_id.replace('/', '--')}"
    repo_dir = cache_root / slug
    if not repo_dir.is_dir():
        return False
    return any(repo_dir.rglob("*.onnx"))


def is_clip_cached(cache_dir: str | Path | None = None, env: Mapping[str, str] | None = None) -> bool:
    """Return True iff both CLIP vision and text ONNX repositories exist locally in cache."""
    root = Path(cache_dir) if cache_dir else default_fastembed_cache_dir(env)
    return _has_cached_repo(root, CLIP_VISION_MODEL) and _has_cached_repo(root, CLIP_TEXT_MODEL)


def _ascii_ratio(text: str) -> float:
    return sum(ord(c) < 128 for c in text) / max(len(text), 1)


def is_ocr_exempt(req_type: Any) -> bool:
    """Return True for visual types (diagrams/charts/tables) whose text content is expected."""
    if req_type is None:
        return False
    val = getattr(req_type, "value", req_type)
    return str(val).strip().lower() in OCR_EXEMPT_TYPES


def build_positive_prompt(rq: AssetRequirement | str, entity: tuple[Any, Any] | None = None) -> str:
    """Build a concise English-leaning CLIP prompt describing the target real-world photo."""
    if isinstance(rq, str):
        subject = rq.strip() or "subject scene"
        return f"a photograph of {subject}"[:200]

    candidates: list[str] = []
    if entity and len(entity) >= 2 and getattr(entity[1], "label_en", None):
        candidates.append(str(entity[1].label_en).strip())
    if rq.targets:
        primary_name = rq.targets[0].name.strip()
        if _ascii_ratio(primary_name) >= 0.7:
            candidates.append(primary_name)
    for hint in sorted(rq.query_hints, key=lambda h: -_ascii_ratio(h)):
        cleaned = hint.strip()
        if cleaned and _ascii_ratio(cleaned) >= 0.7:
            candidates.append(cleaned)
            break
    if not candidates:
        if rq.targets:
            candidates.append(rq.targets[0].name.strip())
        elif rq.purpose:
            candidates.append(rq.purpose.strip())

    subject = " ".join(candidates[0].split()) if candidates else "real world scene"
    return f"a photograph of {subject}"[:200]


def _run_with_load_timeout(fn: Callable[[], Any], timeout_s: float, label: str) -> Any:
    holder: dict[str, Any] = {}
    done = threading.Event()

    def worker() -> None:
        try:
            holder["result"] = fn()
        except BaseException as exc:  # pragma: no cover - mapped below
            holder["error"] = exc
        finally:
            done.set()

    t = threading.Thread(target=worker, daemon=True, name=f"pptv01-{label}")
    t.start()
    if not done.wait(max(0.05, float(timeout_s))):
        raise TimeoutError(f"{label} load timed out after {timeout_s:g}s")
    if "error" in holder:
        raise holder["error"]
    return holder["result"]


def _load_rgb_image(image_bytes: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(image_bytes))
    im = ImageOps.exif_transpose(im)
    if im.mode != "RGB":
        im = im.convert("RGB")
    return im


def _polygon_area_ratio(raw_boxes: list[tuple[Any, float | None]], width: int, height: int) -> float:
    if width <= 0 or height <= 0 or not raw_boxes:
        return 0.0
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    drawn = False
    for box, conf in raw_boxes:
        if conf is not None and conf < OCR_MIN_CONFIDENCE:
            continue
        try:
            pts = [(float(p[0]), float(p[1])) for p in box]
        except Exception:
            continue
        if len(pts) < 3:
            continue
        draw.polygon(pts, fill=255)
        drawn = True
    if not drawn:
        return 0.0
    text_pixels = int(mask.histogram()[255])
    return text_pixels / float(width * height)


def _extract_ocr_boxes(raw_result: Any) -> list[tuple[Any, float | None]]:
    """Normalize outputs from both ``rapidocr_onnxruntime`` (tuple) and ``rapidocr`` (.boxes)."""
    if raw_result is None:
        return []
    # rapidocr-onnxruntime returns (list_of_[box, text, conf], elapsed)
    if isinstance(raw_result, tuple):
        items = raw_result[0]
        if not items:
            return []
        out: list[tuple[Any, float | None]] = []
        for entry in items:
            if isinstance(entry, (list, tuple)) and len(entry) >= 3:
                try:
                    conf = float(entry[2])
                except (TypeError, ValueError):
                    conf = None
                out.append((entry[0], conf))
            elif isinstance(entry, (list, tuple)) and entry:
                out.append((entry[0], None))
        return out
    # rapidocr >= 2.x RapidOCROutput object with .boxes / .scores
    boxes = getattr(raw_result, "boxes", None)
    if boxes is not None:
        scores = getattr(raw_result, "scores", None)
        out = []
        for i, box in enumerate(boxes):
            conf = float(scores[i]) if scores is not None and i < len(scores) else None
            out.append((box, conf))
        return out
    if isinstance(raw_result, list):
        return [(entry[0], float(entry[2]) if len(entry) >= 3 else None) for entry in raw_result if entry]
    return []


class FastembedClipScorer:
    """CLIP ViT-B-32 scorer backed by ``fastembed`` ``ImageEmbedding`` and ``TextEmbedding``."""

    def __init__(
        self,
        *,
        load_timeout_s: float = _DEFAULT_LOAD_TIMEOUT_S,
        cache_dir: str | None = None,
        hf_endpoint: str | None = None,
        progress_fn: Callable[[str], None] | None = None,
    ) -> None:
        t0 = time.monotonic()
        with _LOAD_ENV_LOCK:
            prev_hf = os.environ.get("HF_ENDPOINT")
            if hf_endpoint:
                os.environ["HF_ENDPOINT"] = hf_endpoint
            try:
                _log(
                    f"loading CLIP ({CLIP_VISION_MODEL}, {CLIP_TEXT_MODEL}, "
                    f"timeout={load_timeout_s:g}s, cache={cache_dir or 'default'}, "
                    f"hf_endpoint={sanitize_url(hf_endpoint or prev_hf or '') or 'default'})",
                    progress_fn,
                )

                def _load() -> tuple[Any, Any]:
                    from fastembed import ImageEmbedding, TextEmbedding

                    v_kwargs: dict[str, Any] = {"model_name": CLIP_VISION_MODEL}
                    t_kwargs: dict[str, Any] = {"model_name": CLIP_TEXT_MODEL}
                    if cache_dir:
                        v_kwargs["cache_dir"] = cache_dir
                        t_kwargs["cache_dir"] = cache_dir
                    return ImageEmbedding(**v_kwargs), TextEmbedding(**t_kwargs)

                self._vision, self._text = _run_with_load_timeout(_load, load_timeout_s, "CLIP")
            finally:
                if hf_endpoint:
                    if prev_hf is None:
                        os.environ.pop("HF_ENDPOINT", None)
                    else:
                        os.environ["HF_ENDPOINT"] = prev_hf
        self._text_cache: dict[str, np.ndarray] = {}
        self._lock = threading.Lock()
        _log(f"loaded CLIP models in {time.monotonic() - t0:.2f}s", progress_fn)

    def _encode_texts(self, prompts: list[str]) -> dict[str, np.ndarray]:
        missing = [p for p in prompts if p not in self._text_cache]
        if missing:
            vecs = list(self._text.embed(missing))
            for prompt, vec in zip(missing, vecs):
                arr = np.asarray(vec, dtype=np.float32)
                norm = float(np.linalg.norm(arr))
                if norm > 0:
                    arr = arr / norm
                self._text_cache[prompt] = arr
        return {p: self._text_cache[p] for p in prompts}

    def score_image(
        self,
        image_bytes: bytes,
        positive_prompt: str,
        negative_prompts: tuple[str, ...] = CLIP_NEGATIVE_PROMPTS,
    ) -> ClipScoreResult:
        im = _load_rgb_image(image_bytes)
        with self._lock:
            img_vec = np.asarray(next(iter(self._vision.embed([im]))), dtype=np.float32)
            norm = float(np.linalg.norm(img_vec))
            if norm > 0:
                img_vec = img_vec / norm
            all_prompts = [positive_prompt, *negative_prompts]
            text_vecs = self._encode_texts(all_prompts)

        pos_score = float(np.dot(img_vec, text_vecs[positive_prompt]))
        neg_scores = {p: float(np.dot(img_vec, text_vecs[p])) for p in negative_prompts}
        if neg_scores:
            top_label, top_score = max(neg_scores.items(), key=lambda kv: kv[1])
        else:
            top_label, top_score = "", -1.0
        return ClipScoreResult(
            positive_score=pos_score,
            negative_scores=neg_scores,
            max_negative_label=top_label,
            max_negative_score=top_score,
        )


class RapidOcrAreaRunner:
    """RapidOCR text-box polygon area ratio estimator."""

    def __init__(
        self,
        *,
        load_timeout_s: float = _DEFAULT_LOAD_TIMEOUT_S,
        progress_fn: Callable[[str], None] | None = None,
    ) -> None:
        t0 = time.monotonic()
        _log(f"loading RapidOCR (timeout={load_timeout_s:g}s)", progress_fn)

        def _load() -> Any:
            try:
                from rapidocr_onnxruntime import RapidOCR

                return RapidOCR()
            except ImportError:
                from rapidocr import RapidOCR  # type: ignore[import-not-found]

                return RapidOCR()

        self._engine = _run_with_load_timeout(_load, load_timeout_s, "RapidOCR")
        self._lock = threading.Lock()
        _log(f"loaded RapidOCR in {time.monotonic() - t0:.2f}s", progress_fn)

    def __call__(self, image_bytes: bytes) -> float:
        im = _load_rgb_image(image_bytes)
        arr = np.asarray(im)
        with self._lock:
            raw = self._engine(arr)
        boxes = _extract_ocr_boxes(raw)
        return _polygon_area_ratio(boxes, im.width, im.height)


@dataclass(frozen=True)
class PixelVerdict:
    """Result of evaluating one image against CLIP and RapidOCR pixel rules."""

    accepted: bool
    reason: str
    positive_prompt: str
    clip_positive: float | None = None
    clip_max_negative: float | None = None
    clip_max_negative_label: str | None = None
    ocr_area_ratio: float | None = None
    exempt_ocr: bool = False
    errors: tuple[str, ...] = ()

    def summary_tag(self) -> str:
        """Compact metrics summary suitable for CandidateRecord or AssetChecks."""
        parts: list[str] = []
        if self.clip_positive is not None:
            parts.append(f"clip_pos={self.clip_positive:.3f}")
        if self.clip_max_negative is not None and self.clip_max_negative_label:
            parts.append(f"neg={self.clip_max_negative_label!r}:{self.clip_max_negative:.3f}")
        if self.ocr_area_ratio is not None:
            parts.append(f"ocr_ratio={self.ocr_area_ratio:.3f}")
        if self.exempt_ocr:
            parts.append("ocr_exempt")
        if self.errors:
            parts.append(f"errors={'; '.join(self.errors)}")
        return ", ".join(parts) or self.reason


@dataclass
class PixelFilter:
    """Two-signal image filter combining CLIP zero-shot prompt checks and RapidOCR box coverage."""

    mode: Literal["auto", "off"]
    clip_scorer: ClipScorer | None = None
    ocr_runner: OcrRunner | None = None
    clip_unavailable: str = ""
    ocr_unavailable: str = ""
    clip_neg_margin: float = CLIP_NEG_REJECT_MARGIN
    ocr_area_max: float = OCR_TEXT_AREA_RATIO_MAX
    negative_prompts: tuple[str, ...] = CLIP_NEGATIVE_PROMPTS
    runtime_errors: list[str] = field(default_factory=list)

    @property
    def degraded_note(self) -> str:
        """Non-empty warning string whenever ``auto`` mode lost CLIP and/or RapidOCR."""
        if self.mode == "off":
            return ""
        issues: list[str] = []
        if self.clip_unavailable:
            issues.append(f"CLIP unavailable: {self.clip_unavailable}")
        if self.ocr_unavailable:
            issues.append(f"RapidOCR unavailable: {self.ocr_unavailable}")
        for err in self.runtime_errors:
            if err not in issues:
                issues.append(err)
        if not issues:
            return ""
        return f"pixel_rules: degraded ({'; '.join(issues[:4])})"

    @property
    def status_summary(self) -> str:
        if self.mode == "off":
            return "pixel_rules: disabled (PPTV01_PIXEL_RULES=off)"
        if self.degraded_note:
            return self.degraded_note
        active = []
        if self.clip_scorer is not None:
            active.append("CLIP")
        if self.ocr_runner is not None:
            active.append("RapidOCR")
        return f"pixel_rules: active ({' + '.join(active) or 'none'})"

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        progress_fn: Callable[[str], None] | None = None,
    ) -> "PixelFilter":
        """Build a PixelFilter from environment flags, degrading cleanly when models cannot load."""
        e = os.environ if env is None else env
        mode_raw = (e.get(ENV_PIXEL_RULES, "auto").strip().lower() or "auto")
        if mode_raw not in ("auto", "off"):
            raise LLMConfigError(f"{ENV_PIXEL_RULES} must be auto or off, got {mode_raw!r}")
        if mode_raw == "off":
            return cls(mode="off")

        timeout_raw = (
            e.get(ENV_PIXEL_LOAD_TIMEOUT, "").strip()
            or e.get(ENV_LOAD_TIMEOUT, "").strip()
            or str(_DEFAULT_LOAD_TIMEOUT_S)
        )
        try:
            load_timeout_s = max(0.05, float(timeout_raw))
        except ValueError:
            raise LLMConfigError(f"invalid pixel load timeout: {timeout_raw!r}") from None

        cache_dir = (
            e.get(ENV_FASTEMBED_CACHE)
            or e.get(ENV_FASTEMBED_CACHE_ALT)
            or os.environ.get(ENV_FASTEMBED_CACHE)
            or os.environ.get(ENV_FASTEMBED_CACHE_ALT)
            or None
        )
        hf_endpoint = e.get("HF_ENDPOINT") or os.environ.get("HF_ENDPOINT") or None

        clip_scorer: ClipScorer | None = None
        clip_err = ""
        try:
            clip_scorer = FastembedClipScorer(
                load_timeout_s=load_timeout_s,
                cache_dir=cache_dir,
                hf_endpoint=hf_endpoint,
                progress_fn=progress_fn,
            )
        except BaseException as exc:
            clip_err = f"{type(exc).__name__}: {str(exc)[:160]}"
            _log(f"CLIP unavailable, continuing without CLIP: {clip_err}", progress_fn)

        ocr_runner: OcrRunner | None = None
        ocr_err = ""
        try:
            ocr_runner = RapidOcrAreaRunner(
                load_timeout_s=load_timeout_s,
                progress_fn=progress_fn,
            )
        except BaseException as exc:
            ocr_err = f"{type(exc).__name__}: {str(exc)[:160]}"
            _log(f"RapidOCR unavailable, continuing without OCR: {ocr_err}", progress_fn)

        return cls(
            mode="auto",
            clip_scorer=clip_scorer,
            ocr_runner=ocr_runner,
            clip_unavailable=clip_err,
            ocr_unavailable=ocr_err,
        )

    def assess_image(
        self,
        image_bytes: bytes,
        rq: AssetRequirement | str = "image",
        *,
        entity: tuple[Any, Any] | None = None,
    ) -> PixelVerdict:
        """Evaluate decoded image bytes against CLIP negative prompts and RapidOCR density."""
        pos_prompt = build_positive_prompt(rq, entity)
        if self.mode == "off":
            return PixelVerdict(
                accepted=True,
                reason="pixel rules disabled (PPTV01_PIXEL_RULES=off)",
                positive_prompt=pos_prompt,
            )

        req_type = getattr(rq, "type", rq)
        if is_ocr_exempt(req_type):
            return PixelVerdict(
                accepted=True,
                reason=f"pixel OCR exempt for visual type {getattr(req_type, 'value', req_type)!r}",
                positive_prompt=pos_prompt,
                exempt_ocr=True,
            )

        errors: list[str] = []
        reject_reasons: list[str] = []
        clip_pos: float | None = None
        clip_max_neg: float | None = None
        clip_max_label: str | None = None
        ocr_ratio: float | None = None

        if self.clip_scorer is not None:
            try:
                cres = self.clip_scorer.score_image(image_bytes, pos_prompt, self.negative_prompts)
                clip_pos = cres.positive_score
                clip_max_neg = cres.max_negative_score
                clip_max_label = cres.max_negative_label
                if clip_max_neg >= clip_pos + self.clip_neg_margin:
                    reject_reasons.append(
                        f"CLIP rejected: negative prompt {clip_max_label!r} ({clip_max_neg:.3f}) "
                        f">= positive ({clip_pos:.3f}) + {self.clip_neg_margin:.2f}"
                    )
            except BaseException as exc:
                msg = f"CLIP runtime error: {type(exc).__name__}: {str(exc)[:120]}"
                errors.append(msg)
                self.runtime_errors.append(msg)

        if self.ocr_runner is not None:
            try:
                ocr_ratio = float(self.ocr_runner(image_bytes))
                if ocr_ratio > self.ocr_area_max:
                    reject_reasons.append(
                        f"OCR rejected: text area ratio {ocr_ratio:.3f} > {self.ocr_area_max:.2f}"
                    )
            except BaseException as exc:
                msg = f"RapidOCR runtime error: {type(exc).__name__}: {str(exc)[:120]}"
                errors.append(msg)
                self.runtime_errors.append(msg)

        if reject_reasons:
            return PixelVerdict(
                accepted=False,
                reason="; ".join(reject_reasons),
                positive_prompt=pos_prompt,
                clip_positive=clip_pos,
                clip_max_negative=clip_max_neg,
                clip_max_negative_label=clip_max_label,
                ocr_area_ratio=ocr_ratio,
                errors=tuple(errors),
            )

        return PixelVerdict(
            accepted=True,
            reason="passed pixel rules",
            positive_prompt=pos_prompt,
            clip_positive=clip_pos,
            clip_max_negative=clip_max_neg,
            clip_max_negative_label=clip_max_label,
            ocr_area_ratio=ocr_ratio,
            errors=tuple(errors),
        )

    def assess_file(
        self,
        path: Path,
        rq: AssetRequirement | str = "image",
        *,
        entity: tuple[Any, Any] | None = None,
    ) -> PixelVerdict:
        """Read an image file from disk and run :meth:`assess_image`."""
        return self.assess_image(Path(path).read_bytes(), rq, entity=entity)
