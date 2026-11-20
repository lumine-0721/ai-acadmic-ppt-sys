"""Research providers: turn A's retrieval_brief into a ResearchRecord.

A provider NEVER invents notes. Every note comes from a backend response; any
failure is recorded in the query log. ``DisabledResearch`` records that nothing
was retrieved.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Mapping, Protocol, Sequence

import httpx

from pptv01.contracts import (
    SCHEMA_VERSION,
    FilteredHit,
    FilterReason,
    ProductionPlan,
    QueryStatus,
    RelevanceInfo,
    ResearchNote,
    ResearchQueryRecord,
    ResearchRecord,
    ResearchStatus,
)

from .relevance import RULES_VERSION as RELEVANCE_RULES_VERSION
from .relevance import Candidate, RelevanceFilter, filter_from_env
from .backends import (
    DEFAULT_CONTACT,
    BackendError,
    CrossrefBackend,
    SearchBackend,
    SearxngBackend,
    WikipediaBackend,
    make_http,
)

ENV_RESEARCH = "PPTV01_RESEARCH"                  # auto (default) | none | comma list: wikipedia,crossref,searxng
ENV_CONTACT = "PPTV01_RESEARCH_CONTACT"
ENV_MAX_HITS = "PPTV01_RESEARCH_MAX_HITS"
ENV_MAX_QUERIES = "PPTV01_RESEARCH_MAX_QUERIES"
ENV_TIMEOUT = "PPTV01_RESEARCH_TIMEOUT_S"
ENV_SEARXNG = "PPTV01_RESEARCH_SEARXNG_URL"
DEFAULT_BACKENDS = ("wikipedia", "crossref")


class ResearchProvider(Protocol):
    def research(self, plan: ProductionPlan) -> ResearchRecord: ...


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


class DisabledResearch:
    """Research explicitly switched off. Records that fact; retrieves nothing."""

    def __init__(self, reason: str = "research disabled"):
        self.reason = reason

    def research(self, plan: ProductionPlan) -> ResearchRecord:
        return ResearchRecord(
            run_id=plan.run_id, provider="disabled", status=ResearchStatus.disabled,
            summary=f"no research performed: {self.reason}",
        )


class LiveResearch:
    def __init__(self, backends: Sequence[SearchBackend], *, max_hits: int = 3, max_queries: int = 12,
                 provider_name: str | None = None, relevance: RelevanceFilter | None = None):
        if not backends:
            raise ValueError("LiveResearch needs at least one backend")
        self.backends = list(backends)
        self.max_hits = max_hits
        self.max_queries = max_queries
        self.provider_name = provider_name or "+".join(b.name for b in self.backends)
        # None = explicit "no filter" (unit tests of the raw backends); env path always passes a filter
        self.relevance = relevance if relevance is not None else RelevanceFilter(None, None, mode="off")

    def research(self, plan: ProductionPlan) -> ResearchRecord:
        queries: list[ResearchQueryRecord] = []
        cands: list[Candidate] = []
        seen_urls: set[str] = set()
        n_run = 0
        for topic in plan.retrieval_brief:
            for q in topic.queries:
                if n_run >= self.max_queries:
                    break
                n_run += 1
                for be in self.backends:
                    if not be.supports(q.lang):
                        continue
                    t0 = _now()
                    try:
                        hits = be.search(q.text, q.lang, self.max_hits)
                    except BackendError as e:
                        queries.append(ResearchQueryRecord(
                            backend=be.name, topic=topic.topic, query=q.text, lang=q.lang,
                            status=QueryStatus.error, hits=0, error=str(e)[:200], requested_at=t0))
                        continue
                    fresh = [h for h in hits if h.url not in seen_urls]
                    queries.append(ResearchQueryRecord(
                        backend=be.name, topic=topic.topic, query=q.text, lang=q.lang,
                        status=QueryStatus.ok if hits else QueryStatus.no_results,
                        hits=len(hits), requested_at=t0))
                    for h in fresh:
                        seen_urls.add(h.url)
                        cands.append(Candidate(title=h.title, text=h.text, kind=h.kind, backend=be.name,
                                               topic=topic.topic, query=q.text, doc_type=h.doc_type, payload=h))
        notes, filtered, info = self._filter(plan, cands)
        n_err = sum(q.status is QueryStatus.error for q in queries)
        if not queries:
            status, summary = ResearchStatus.no_results, "no query could be issued (no backend supports the brief's languages)"
        elif n_err == len(queries):
            status = ResearchStatus.failed
            summary = f"all {n_err} queries failed (network unavailable or backend error); nothing retrieved"
        elif not notes:
            status = ResearchStatus.no_results
            summary = f"{len(queries)} queries answered, no usable results" + (
                f" ({len(filtered)} hits removed by the relevance filter)" if filtered else "")
        else:
            extra = f"; {len(filtered)} hits filtered as irrelevant" if filtered else ""
            if n_err:
                status, summary = ResearchStatus.partial, f"{len(notes)} notes; {n_err}/{len(queries)} queries failed{extra}"
            else:
                status, summary = ResearchStatus.ok, f"{len(notes)} notes from {len(queries)} queries{extra}"
        return ResearchRecord(run_id=plan.run_id, provider=self.provider_name, status=status,
                              queries=queries, notes=notes, filtered=filtered, relevance_filter=info, summary=summary)

    def _filter(self, plan: ProductionPlan, cands: list[Candidate]):
        outcome = self.relevance.apply(cands, plan.topic)
        notes: list[ResearchNote] = []
        for c, score in outcome.kept:
            h = c.payload
            notes.append(ResearchNote(
                note_id=f"R{len(notes) + 1:03d}", kind=h.kind, title=h.title, text=h.text,
                source_url=h.url, source_name=h.source_name, license_or_terms=h.license_or_terms,
                backend=c.backend, query=c.query, retrieved_at=h.retrieved_at, doc_type=c.doc_type,
                relevance=None if score is None else round(score, 4)))
        filtered = [FilteredHit(
            title=c.title, source_url=c.payload.url, kind=c.kind, backend=c.backend, topic=c.topic,
            query=c.query, doc_type=c.doc_type, reason=FilterReason(v.reason), detail=v.detail,
            score=None if v.score is None else round(v.score, 4)) for c, v in outcome.dropped]
        info = RelevanceInfo(
            mode=outcome.mode, rules_version=RELEVANCE_RULES_VERSION, scorer=outcome.scorer, model=outcome.model,
            threshold=outcome.threshold, n_candidates=len(cands), n_kept=len(notes), n_filtered=len(filtered),
            note=outcome.note)
        return notes, filtered, info


def research_from_env(env: Mapping[str, str] | None = None,
                      transport: httpx.BaseTransport | None = None) -> ResearchProvider:
    """PPTV01_RESEARCH: auto (default: wikipedia,crossref) | none | comma list of backends."""
    from pptv01.llm import LLMConfigError  # local import: avoid cycle at module load

    env = os.environ if env is None else env
    mode = (env.get(ENV_RESEARCH, "auto").strip().lower() or "auto")
    if mode == "none":
        return DisabledResearch(f"{ENV_RESEARCH}=none")
    names = list(DEFAULT_BACKENDS) if mode == "auto" else [n.strip() for n in mode.split(",") if n.strip()]
    try:
        timeout = float(env.get(ENV_TIMEOUT, "20"))
        max_hits = int(env.get(ENV_MAX_HITS, "3"))
        max_queries = int(env.get(ENV_MAX_QUERIES, "12"))
    except ValueError as e:
        raise LLMConfigError(f"invalid numeric research setting: {e}") from None
    contact = env.get(ENV_CONTACT, "").strip() or DEFAULT_CONTACT
    http = make_http(timeout, contact, transport)
    backends: list[SearchBackend] = []
    for n in names:
        if n == "wikipedia":
            backends.append(WikipediaBackend(http))
        elif n == "crossref":
            backends.append(CrossrefBackend(http, contact))
        elif n == "searxng":
            url = env.get(ENV_SEARXNG, "").strip()
            if not url:
                raise LLMConfigError(f"{ENV_RESEARCH} includes searxng but {ENV_SEARXNG} is not set")
            backends.append(SearxngBackend(http, url))
        else:
            raise LLMConfigError(f"unknown research backend {n!r}; use wikipedia, crossref, searxng, auto or none")
    return LiveResearch(backends, max_hits=max_hits, max_queries=max_queries, relevance=filter_from_env(env))
