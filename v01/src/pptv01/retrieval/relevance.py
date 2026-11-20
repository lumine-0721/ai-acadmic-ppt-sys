"""Relevance filter for retrieved hits (stage B).

Three layers, cheapest first; every removed hit is KEPT in the record with its reason:

  1. structural rules   junk titles (INTRODUCTION:, Chapter Sixteen, Inhalt, [Illustration], Figure 3 ...),
                        near-empty bodies, disambiguation / list pages, Crossref non-article types.
  2. coarse score       multilingual sentence embedding (fastembed, ONNX, CPU) vs "topic + sub-topic".
  3. optional reranker  cross-encoder (BAAI/bge-reranker-v2-m3) - opt-in, see docs/relevance-benchmark.md.

Nothing here invents content; it only decides keep / drop and says why. The scorer is lazy and optional:
if its dependency is missing the filter runs structural-only and the record says so.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Mapping, Protocol, Sequence

RULES_VERSION = "1"

ENV_MODE = "PPTV01_RELEVANCE"                 # auto (default) | structural | off
ENV_SCORER = "PPTV01_RELEVANCE_SCORER"        # fastembed (default) | bge-reranker
ENV_THRESHOLD = "PPTV01_RELEVANCE_THRESHOLD"  # float override for the chosen scorer
ENV_EMBED_MODEL = "PPTV01_RELEVANCE_EMBED_MODEL"
ENV_RERANK_MODEL = "PPTV01_RELEVANCE_RERANK_MODEL"

DEFAULT_EMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_RERANK_MODEL = "BAAI/bge-reranker-v2-m3"
# Conservative, benchmark-chosen cut-offs (see docs/relevance-benchmark.md): drop only the clearly off-topic.
DEFAULT_THRESHOLDS = {"fastembed": 0.30, "bge-reranker": 0.005}

# --- structural rules ------------------------------------------------------------------------------
_NUM_WORDS = r"(?:\d+|[ivxlc]+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty\w*|[一二三四五六七八九十百]+)"
_JUNK_TITLE = re.compile(
    r"^\s*(?:\d+[.)]\s*)?(?:"
    r"introduction|preface|foreword|prologue|epilogue|afterword|contents|table of contents|index|abstract|summary|"
    r"inhalt|inhaltsverzeichnis|vorwort|einleitung|register|sommaire|table des mati[eè]res|"
    r"acknowledg(?:e)?ments?|references|bibliography|notes|appendix(?:\s+\w+)?|front matter|back matter|"
    r"title page|half[- ]title|copyright(?: page)?|cover|dedication|in memoriam|list of (?:figures|tables|illustrations)|"
    r"chapter\s+" + _NUM_WORDS + r"|part\s+" + _NUM_WORDS + r"|"
    r"(?:figure|fig\.?|table|plate|scheme|box)\s*[\w.-]*\d[\w.-]*(?::.*|\.\s.*)?|"
    r"\[(?:illustration|figure|table|image|blank)[^\]]*\]|"
    r"目录|目次|前言|序言?|引言|摘要|致谢|参考文献|附录|封面|版权页|第[一二三四五六七八九十百\d]+[章节篇]"
    r")\s*[:：.。]*\s*$", re.I)
_FIGURE_PREFIX = re.compile(r"^\s*(?:figure|fig\.?|table|plate|图|表)\s*\d+[\s:：.]", re.I)
_DISAMBIG = re.compile(r"(?:may (?:also )?refer to|can refer to|most commonly refers to|消歧义|可以指|可能指|指的是以下)", re.I)
_LIST_TITLE = re.compile(r"(?:^list of |^lists of |列表$|一覽表$|一览表$|^index of )", re.I)
MIN_BODY_CHARS = 60           # an encyclopedia extract shorter than this carries no usable fact
BAD_CROSSREF_TYPES = {"component", "peer-review", "report-component", "grant", "other", "journal-issue", "journal-volume"}


@dataclass
class Candidate:
    """A retrieved hit plus the context the filter needs. Backends fill ``doc_type`` when they know it."""

    title: str
    text: str
    kind: str
    backend: str
    topic: str                # the retrieval-brief topic that issued the query
    query: str
    doc_type: str = ""
    payload: object = None    # opaque: the caller's own hit object, returned untouched


@dataclass
class Verdict:
    keep: bool
    reason: str = ""          # junk_title | too_short | disambiguation | list_page | bad_type | low_relevance
    detail: str = ""
    score: float | None = None


def structural_check(c: Candidate) -> Verdict:
    t = c.title.strip()
    if _JUNK_TITLE.match(t) or _FIGURE_PREFIX.match(t):
        return Verdict(False, "junk_title", f"title {t!r} is a structural fragment, not a topic")
    if c.doc_type and c.doc_type.lower() in BAD_CROSSREF_TYPES:
        return Verdict(False, "bad_type", f"Crossref type {c.doc_type!r} is not a citable work")
    if c.kind == "encyclopedia":
        body = c.text.strip()
        if _DISAMBIG.search(body[:200]):
            return Verdict(False, "disambiguation", "disambiguation page")
        if _LIST_TITLE.search(t):
            return Verdict(False, "list_page", f"list/index page {t!r}")
        if len(body) < MIN_BODY_CHARS:
            return Verdict(False, "too_short", f"extract is {len(body)} chars (< {MIN_BODY_CHARS})")
    return Verdict(True)


# --- scorers ---------------------------------------------------------------------------------------
class ScorerUnavailable(RuntimeError):
    pass


class Scorer(Protocol):
    name: str          # "fastembed" | "bge-reranker" (keys of DEFAULT_THRESHOLDS) or a test fake's name
    model: str

    def score(self, query: str, docs: Sequence[str]) -> list[float]: ...


def doc_text(c: Candidate) -> str:
    body = c.text.strip()
    return c.title if not body or body == c.title else f"{c.title}。{body[:300]}"


class FastembedScorer:
    name = "fastembed"

    def __init__(self, model: str = DEFAULT_EMBED_MODEL):
        try:
            from fastembed import TextEmbedding
        except ImportError as e:
            raise ScorerUnavailable("fastembed is not installed (pip install 'pptv01[rank]')") from e
        self.model = model
        try:
            self._m = TextEmbedding(model)
        except Exception as e:  # download / onnx problems
            raise ScorerUnavailable(f"cannot load embedding model {model}: {type(e).__name__}: {str(e)[:120]}") from e

    def score(self, query: str, docs: Sequence[str]) -> list[float]:
        import numpy as np
        d = np.array(list(self._m.embed(list(docs))))
        q = np.array(list(self._m.embed([query])))[0]
        return list((d @ q) / (np.linalg.norm(d, axis=1) * np.linalg.norm(q) + 1e-9))


class CrossEncoderScorer:
    name = "bge-reranker"

    def __init__(self, model: str = DEFAULT_RERANK_MODEL):
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as e:
            raise ScorerUnavailable("sentence-transformers/torch not installed (pip install 'pptv01[rerank]')") from e
        self.model = model
        try:
            self._m = CrossEncoder(model, device="cpu", max_length=512)
        except Exception as e:
            raise ScorerUnavailable(f"cannot load reranker {model}: {type(e).__name__}: {str(e)[:120]}") from e

    def score(self, query: str, docs: Sequence[str]) -> list[float]:
        return [float(x) for x in self._m.predict([(query, d) for d in docs], batch_size=8)]


# --- the filter ------------------------------------------------------------------------------------
@dataclass
class FilterOutcome:
    kept: list[tuple[Candidate, float | None]] = field(default_factory=list)
    dropped: list[tuple[Candidate, Verdict]] = field(default_factory=list)
    scorer: str | None = None
    model: str | None = None
    threshold: float | None = None
    mode: str = "structural"
    note: str = ""


class RelevanceFilter:
    def __init__(self, scorer: Scorer | None = None, threshold: float | None = None, *,
                 mode: str = "auto", unavailable: str = ""):
        self.scorer = scorer
        self.mode = mode
        self.unavailable = unavailable
        if scorer is not None and threshold is None:
            threshold = DEFAULT_THRESHOLDS.get(scorer.name)
        self.threshold = threshold

    def apply(self, cands: Sequence[Candidate], plan_topic: str) -> FilterOutcome:
        out = FilterOutcome(mode=self.mode, note=self.unavailable)
        if self.mode == "off":
            out.kept = [(c, None) for c in cands]
            out.note = "relevance filtering disabled; every hit kept"
            return out
        survivors: list[Candidate] = []
        for c in cands:
            v = structural_check(c)
            (survivors.append(c) if v.keep else out.dropped.append((c, v)))
        if self.scorer is None or self.threshold is None or not survivors:
            out.kept = [(c, None) for c in survivors]
            return out
        out.scorer, out.model, out.threshold = self.scorer.name, self.scorer.model, self.threshold
        # one batched call per retrieval topic: query = "<plan topic> <sub-topic>"
        by_topic: dict[str, list[Candidate]] = {}
        for c in survivors:
            by_topic.setdefault(c.topic, []).append(c)
        scores: dict[int, float] = {}
        for topic, group in by_topic.items():
            q = plan_topic if topic == plan_topic else f"{plan_topic} {topic}"
            for c, s in zip(group, self.scorer.score(q, [doc_text(c) for c in group])):
                scores[id(c)] = float(s)
        for c in survivors:
            s = scores[id(c)]
            if s < self.threshold:
                out.dropped.append((c, Verdict(False, "low_relevance",
                                               f"{self.scorer.name} score {s:.3f} < {self.threshold}", s)))
            else:
                out.kept.append((c, s))
        return out


def filter_from_env(env: Mapping[str, str] | None = None) -> RelevanceFilter:
    """PPTV01_RELEVANCE: auto (structural + scorer if installed) | structural | off(= keep everything, recorded)."""
    from pptv01.llm import LLMConfigError

    env = os.environ if env is None else env
    mode = (env.get(ENV_MODE, "auto").strip().lower() or "auto")
    if mode not in ("auto", "structural", "off"):
        raise LLMConfigError(f"{ENV_MODE} must be auto, structural or off")
    if mode in ("structural", "off"):
        return RelevanceFilter(None, None, mode=mode)
    which = (env.get(ENV_SCORER, "fastembed").strip().lower() or "fastembed")
    try:
        thr = float(env[ENV_THRESHOLD]) if env.get(ENV_THRESHOLD) else None
    except ValueError:
        raise LLMConfigError(f"{ENV_THRESHOLD} must be a number") from None
    try:
        if which == "fastembed":
            sc: Scorer = FastembedScorer(env.get(ENV_EMBED_MODEL) or DEFAULT_EMBED_MODEL)
        elif which in ("bge-reranker", "reranker"):
            sc = CrossEncoderScorer(env.get(ENV_RERANK_MODEL) or DEFAULT_RERANK_MODEL)
        else:
            raise LLMConfigError(f"{ENV_SCORER} must be fastembed or bge-reranker")
    except ScorerUnavailable as e:
        return RelevanceFilter(None, None, mode="auto", unavailable=f"scorer unavailable, structural rules only: {e}")
    return RelevanceFilter(sc, thr, mode="auto")
