"""Stage D: turn C's asset requirements into real files + a registry (asset_registry.json, assets/).

Per requirement type (V0.1 wires two classes first, as the technical plan 6.5 asks):
  image      Wikidata (disambiguate named target, P18 = representative Commons file) + Commons search
             -> licence allowlist -> format/size -> relevance filter -> download -> Pillow-verify -> assets/<id>.<ext>
  reference  OpenAlex (pyalex) -> relevance filter -> Crossref DOI verification -> assets/<id>.json (citation record)
             fallback to Crossref search when OpenAlex fails (the failure is recorded)
  others     recorded as ``missing`` with the reason (diagram/icon are drawn later; statistic/chart_data/table need a
             cited dataset and no source is wired yet). Nothing is ever fabricated.

D never calls an LLM and cannot look at pixels: ``checks.relevance`` says exactly what was (not) verified.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping

import httpx

from pptv01.contracts import (
    AssetChecks, AssetRecord, AssetRegistry, AssetRequirement, AssetRequirements, AssetStatus, AssetType,
    CandidateRecord, EntityRef, ProductionPlan, RelevanceInfo, SCHEMA_VERSION, SlidePlan, SourceCall, write_contract,
)
from pptv01.user_requests import USER_INPUT_REQUESTS_FILE, build_user_input_requests
from pptv01.llm import LLMConfigError
from pptv01.retrieval.backends import BackendError, CrossrefBackend, make_http
from pptv01.retrieval.relevance import RULES_VERSION, Candidate, RelevanceFilter, filter_from_env

from .license import check_license, needs_attribution, plain
from .sources import (
    IMAGE_MIMES, MAX_ASPECT, MIN_RASTER_WIDTH, NON_SUBJECT_CLASSES, CommonsClient, CommonsFile, CrossrefVerifier, OpenAlexClient, WdEntity,
    WikidataClient, Work, bare_doi, download_image, now, similar, verify_image,
)

REGISTRY_FILE = "asset_registry.json"
ENV_ASSETS = "PPTV01_ASSETS"                       # auto (default) | none
ENV_OA_KEY = "PPTV01_OPENALEX_API_KEY"
ENV_OA_EMAIL = "PPTV01_OPENALEX_EMAIL"
ENV_THUMB = "PPTV01_COMMONS_THUMB_WIDTH"
ENV_TIMEOUT = "PPTV01_ASSETS_TIMEOUT_S"
NO_TARGET_ENTITY_TYPES = {"dataset", "publication"}
MAX_DOWNLOAD_TRIES = 3
_CJK = re.compile(r"[\u3400-\u9fff]")


@dataclass
class DServices:
    http: httpx.Client | None
    wikidata: WikidataClient | None
    commons: CommonsClient | None
    openalex: OpenAlexClient | None
    crossref: CrossrefVerifier | None
    relevance: RelevanceFilter
    contact: str = ""
    disabled_reason: str = ""

    @property
    def enabled(self) -> bool:
        return not self.disabled_reason

    def close(self) -> None:
        if self.http is not None:
            self.http.close()


def services_from_env(env: Mapping[str, str] | None = None, transport: httpx.BaseTransport | None = None,
                      openalex_fetch=None) -> DServices:
    env = os.environ if env is None else env
    rel = filter_from_env(env)
    if (env.get(ENV_ASSETS, "auto").strip().lower() or "auto") == "none":
        return DServices(None, None, None, None, None, rel, disabled_reason=f"{ENV_ASSETS}=none")
    if (env.get(ENV_ASSETS, "auto").strip().lower() or "auto") != "auto":
        raise LLMConfigError(f"{ENV_ASSETS} must be auto or none")
    try:
        timeout = float(env.get(ENV_TIMEOUT, "30"))
        thumb = int(env.get(ENV_THUMB, "1280"))
    except ValueError as e:
        raise LLMConfigError(f"invalid numeric asset setting: {e}") from None
    contact = env.get("PPTV01_RESEARCH_CONTACT", "").strip() or "https://github.com/lumine-0721/ai-acadmic-ppt-sys"
    http = make_http(timeout, contact, transport)
    oa_email = env.get(ENV_OA_EMAIL, "").strip() or (contact if "@" in contact else "")
    return DServices(http, WikidataClient(http), CommonsClient(http, thumb),
                     OpenAlexClient(openalex_fetch, api_key=env.get(ENV_OA_KEY, "").strip(), email=oa_email),
                     CrossrefVerifier(http, contact), rel, contact)


# ------------------------------------------------------------------------------------------ helpers
class AssetRetrievalFailed(RuntimeError):
    """Every source call failed (network/quota): nothing was retrieved. The registry file is still written."""

    def __init__(self, message: str, registry: AssetRegistry):
        super().__init__(message)
        self.registry = registry


class _Ctx:
    def __init__(self, plan: ProductionPlan, sv: DServices, run_dir: Path):
        self.plan, self.sv, self.run_dir = plan, sv, run_dir
        self.calls: list[SourceCall] = []
        self.n_cand = self.n_kept = self.n_filtered = 0
        self.scorer_info: tuple[str | None, str | None, float | None] = (None, None, None)
        self.note = ""
        self.wd_rejected: dict[str, list[str]] = {}      # target name -> Wikidata candidates dropped by class (P31)
        self.used_by_id: dict[str, AssetRecord] = {}     # Commons title -> first AssetRecord that acquired it
        self.used_by_sha: dict[str, AssetRecord] = {}    # sha256 -> first AssetRecord that acquired it

    def call(self, backend: str, purpose: str, query: str, fn, *, count=len):
        """Run one API call, log it (ok / no_results / error). Returns result or None on error."""
        try:
            res = fn()
        except BackendError as e:
            self.calls.append(SourceCall(backend=backend, purpose=purpose, query=query[:200], status="error", error=str(e)[:240]))
            return None
        n = count(res)
        self.calls.append(SourceCall(backend=backend, purpose=purpose, query=query[:200],
                                     status="ok" if n else "no_results", hits=n))
        return res


def _is_cjk(s: str) -> bool:
    return bool(_CJK.search(s))


def _ascii_ratio(s: str) -> float:
    return sum(ord(c) < 128 for c in s) / max(len(s), 1)


def _terms(*texts: str) -> set[str]:
    out: set[str] = set()
    for t in texts:
        out |= {w.lower() for w in re.findall(r"[A-Za-z]{4,}", t)}
        out |= {m for m in re.findall(r"[\u3400-\u9fff]{2,}", t)}
    return out


def _score(ctx: _Ctx, cands: list[Candidate], *, count: bool = True):
    """Relevance filter (structural + scorer) over D candidates. Returns {id(cand): (keep, score, reason)}.
    ``count=False`` for Wikidata entity picking (not an asset candidate, kept out of the filter statistics)."""
    out = ctx.sv.relevance.apply(cands, ctx.plan.topic)
    if count:
        ctx.n_cand += len(cands)
        ctx.n_kept += len(out.kept)
        ctx.n_filtered += len(out.dropped)
    if out.scorer:
        ctx.scorer_info = (out.scorer, out.model, out.threshold)
    elif out.note:
        ctx.note = out.note
    res: dict[int, tuple[bool, float | None, str]] = {}
    for c, s in out.kept:
        res[id(c)] = (True, s, "")
    for c, v in out.dropped:
        res[id(c)] = (False, v.score, f"{v.reason}: {v.detail}")
    return res, out.scorer is not None


def _missing(rq: AssetRequirement, notes: str, **kw) -> AssetRecord:
    return AssetRecord(asset_id=rq.asset_id, status=AssetStatus.missing, type=rq.type, notes=notes, **kw)


# ------------------------------------------------------------------------------------------ entity
def _resolve_entity(ctx: _Ctx, rq: AssetRequirement, name: str, etype: str) -> tuple[EntityRef, WdEntity] | None:
    wd = ctx.sv.wikidata
    langs = ["zh", "en"] if _is_cjk(name) else ["en", "zh"]
    found: list[WdEntity] = []
    for lang in langs:
        res = ctx.call("wikidata", "disambiguate target", f"{name} [{lang}]", lambda lang=lang: wd.search(name, lang, 5))
        if res:
            found = res
            break
    if not found:
        return None
    # drop items that are papers/lists/disambiguation pages (P31): a scholarly article *about* a temple is not the temple
    rejected: list[str] = []
    types = ctx.call("wikidata", "entity classes (P31)", ",".join(e.qid for e in found),
                     lambda: wd.instance_of([e.qid for e in found]), count=len) or {}
    kept: list[WdEntity] = []
    for e in found:
        bad = sorted(NON_SUBJECT_CLASSES[t] for t in types.get(e.qid, set()) if t in NON_SUBJECT_CLASSES)
        if bad:
            rejected.append(f"{e.qid} {e.label[:60]} - rejected: instance of {', '.join(bad)}")
        else:
            kept.append(e)
    ctx.wd_rejected[name] = rejected
    if not kept:
        return None
    found = kept
    exact = [e for e in found if e.match_text and e.match_text.casefold() == name.casefold()]
    if len(found) == 1:
        pick, how = found[0], "only_candidate"
    elif exact:
        # the search matched the requested name exactly on a label/alias: keep Wikidata's own ranking among those
        pick, how = exact[0], "exact_label_match"
    else:
        cands = [Candidate(f"{e.label}", f"{e.label}。{e.description}", "web", "wikidata", f"{name} {rq.purpose}", name,
                           payload=e) for e in found]
        verdict, scored = _score(ctx, cands, count=False)
        if scored:
            ranked = sorted((c for c in cands), key=lambda c: -(verdict[id(c)][1] if verdict[id(c)][1] is not None else -1))
            pick, how = ranked[0].payload, "relevance_score"
        else:
            pick, how = found[0], "search_rank"
    ent = ctx.call("wikidata", "entity details (labels, P18)", pick.qid, lambda: wd.entity(pick.qid), count=lambda e: 1)
    if ent is None:
        return None
    ref = EntityRef(name=name, qid=pick.qid, label=ent.label, description=ent.description or pick.description,
                    url=ent.url, chosen_by=how,
                    alternatives=[f"{e.qid} {e.label} - {e.description}"[:160] for e in found if e.qid != pick.qid] + rejected)
    return ref, ent


# ------------------------------------------------------------------------------------------ image
def _acquire_image(ctx: _Ctx, rq: AssetRequirement) -> AssetRecord:
    sv = ctx.sv
    primary = rq.targets[0]
    uncovered = [t.name for t in rq.targets[1:]]
    cand_log: list[CandidateRecord] = []
    entity: tuple[EntityRef, WdEntity] | None = None
    if primary.entity_type.lower() not in NO_TARGET_ENTITY_TYPES:
        entity = _resolve_entity(ctx, rq, primary.name, primary.entity_type)
    pool: list[CommonsFile] = []
    seen: set[str] = set()

    def add(files: list[CommonsFile] | None):
        for f in files or []:
            if f.title not in seen:
                seen.add(f.title)
                pool.append(f)
    if entity and entity[1].p18:
        add(ctx.call("commons", "Wikidata P18 file info", ",".join(entity[1].p18[:3]),
                     lambda: sv.commons.files(entity[1].p18[:3])))
    queries: list[str] = []
    if entity and entity[1].label_en:
        queries.append(entity[1].label_en)
    queries += [h for h in sorted(rq.query_hints, key=lambda h: -_ascii_ratio(h))]
    n_search = 0
    for q in queries:
        if n_search >= 3 or len([f for f in pool if f.origin == "commons_search"]) >= 12:
            break
        n_search += 1
        add(ctx.call("commons", "search images", q, lambda q=q: sv.commons.search(q, 8)))
    if not pool:
        return _missing(rq, f"no Commons file found for target {primary.name!r} (queries: {queries[:3]})",
                        entity=entity[0] if entity else None, uncovered_targets=uncovered)
    # 1) licence + format gates
    ok: list[CommonsFile] = []
    for f in pool:
        accepted, why = check_license(f.license_short, f.restrictions)
        if not accepted:
            cand_log.append(CandidateRecord(title=f.title, url=f.page_url, license=f.license_short, origin=f.origin,
                                            decision="rejected", reason=why))
        elif needs_attribution(f.license_short) and not (f.artist or f.credit):
            cand_log.append(CandidateRecord(title=f.title, url=f.page_url, license=f.license_short, origin=f.origin,
                                            decision="rejected",
                                            reason="licence requires attribution but Commons gives no author/credit"))
        elif f.mime not in IMAGE_MIMES:
            cand_log.append(CandidateRecord(title=f.title, url=f.page_url, license=f.license_short, origin=f.origin,
                                            decision="rejected", reason=f"format {f.mime or 'unknown'} not supported in V0.1"))
        elif f.width < MIN_RASTER_WIDTH:
            cand_log.append(CandidateRecord(title=f.title, url=f.page_url, license=f.license_short, origin=f.origin,
                                            decision="rejected", reason=f"too small ({f.width}px wide < {MIN_RASTER_WIDTH})"))
        elif f.width and f.height and max(f.width, f.height) / min(f.width, f.height) > MAX_ASPECT:
            cand_log.append(CandidateRecord(title=f.title, url=f.page_url, license=f.license_short, origin=f.origin,
                                            decision="rejected",
                                            reason=f"extreme aspect ratio {f.width}x{f.height} (> {MAX_ASPECT:g}:1, not usable as a slide picture)"))
        else:
            ok.append(f)
    # 2) relevance: P18 is Wikidata's own representative image of the (disambiguated) entity -> trusted;
    #    search results go through the relevance filter (structural + scorer, else lexical overlap).
    p18 = [f for f in ok if f.origin == "wikidata_p18"]
    search = [f for f in ok if f.origin != "wikidata_p18"]
    cands = [Candidate(f.name, f.description or f.name, "web", "commons", f"{primary.name} {rq.purpose}", f.title, payload=f)
             for f in search]
    ordered: list[tuple[CommonsFile, float | None, str]] = [
        (f, None, f"Wikidata P18 of {entity[0].qid} {entity[0].label} (representative image chosen on Wikidata)") for f in p18]
    if cands:
        verdict, scored = _score(ctx, cands)
        want = _terms(primary.name, *([entity[0].label, entity[1].label_en] if entity else []))
        for c in cands:
            f = c.payload
            keep, score, why = verdict[id(c)]
            if keep and not scored:                # no embedding scorer: demand lexical overlap with the target
                text = f"{f.name} {f.description}".lower()
                if want and not any(w.lower() in text for w in want):
                    keep, why = False, "no lexical overlap with the target (no embedding scorer available)"
            if not keep:
                cand_log.append(CandidateRecord(title=f.title, url=f.page_url, license=f.license_short, origin=f.origin,
                                                decision="rejected", reason=why, score=score))
            else:
                how = (f"embedding score {score:.2f} (metadata text vs target/purpose)" if scored
                       else "lexical overlap with target in file metadata (no embedding scorer)")
                ordered.append((f, score, how))
        # best score first among search results (P18 stays first)
        head, tail = ordered[:len(p18)], sorted(ordered[len(p18):], key=lambda t: -(t[1] if t[1] is not None else -1))
        ordered = head + tail
    # 3) cross-requirement deduplication: demote candidates already selected by an earlier requirement
    fresh: list[tuple[CommonsFile, float | None, str]] = []
    demoted: list[tuple[CommonsFile, float | None, str]] = []
    demoted_reasons: dict[str, str] = {}
    for item in ordered:
        f = item[0]
        if f.title in ctx.used_by_id:
            owner = ctx.used_by_id[f.title].asset_id
            demoted_reasons[f.title] = f"demoted: already used by {owner}"
            demoted.append(item)
        else:
            fresh.append(item)
    # 4) download best unused candidate, verify, check sha256 dedupe, fall through on failure
    chosen = None
    sha_fallback = None
    for f, score, how in fresh[:MAX_DOWNLOAD_TRIES]:
        got = ctx.call("commons", "download image", f.file_url, lambda f=f: download_image(sv.http, f.file_url),
                       count=lambda r: 1)
        if got is None:
            cand_log.append(CandidateRecord(title=f.title, url=f.page_url, license=f.license_short, origin=f.origin,
                                            decision="rejected", reason="download failed", score=score))
            continue
        data, ctype = got
        try:
            w, h, sha = verify_image(data, f.mime)
        except BackendError as e:
            cand_log.append(CandidateRecord(title=f.title, url=f.page_url, license=f.license_short, origin=f.origin,
                                            decision="rejected", reason=str(e), score=score))
            continue
        if sha in ctx.used_by_sha:
            owner_rec = ctx.used_by_sha[sha]
            demoted_reasons[f.title] = f"demoted: same sha256 as {owner_rec.asset_id}"
            if sha_fallback is None:
                sha_fallback = (f, score, how, data, w, h, sha, owner_rec)
            continue
        chosen = (f, score, how, data, w, h, sha, None)
        break
    if chosen is None and demoted:
        f, score, how = demoted[0]
        owner_rec = ctx.used_by_id[f.title]
        chosen = (f, score, how, None, owner_rec.width, owner_rec.height, owner_rec.sha256, owner_rec)
    if chosen is None and sha_fallback is not None:
        chosen = sha_fallback
    if chosen is None:
        for f, score, how in fresh[MAX_DOWNLOAD_TRIES:]:
            cand_log.append(CandidateRecord(title=f.title, url=f.page_url, license=f.license_short, origin=f.origin,
                                            decision="rejected", reason="not tried (download attempts exhausted)", score=score))
        return _missing(rq, f"candidates found for {primary.name!r} but none passed licence/format/relevance/download "
                            f"(see candidates)", entity=entity[0] if entity else None, candidates=cand_log,
                        uncovered_targets=uncovered)
    f, score, how, data, w, h, sha, shared_rec = chosen
    tried = {c.title for c in cand_log}
    for g, gscore, _ in ordered:
        if g.title != f.title and g.title not in tried:
            reason = demoted_reasons.get(g.title, "passed the filters but ranked below the selected file")
            cand_log.append(CandidateRecord(title=g.title, url=g.page_url, license=g.license_short, origin=g.origin,
                                            decision="rejected", reason=reason, score=gscore))
    if shared_rec is None:
        ext = IMAGE_MIMES[f.mime]
        rel = f"assets/{rq.asset_id}.{ext}"
        (ctx.run_dir / "assets").mkdir(exist_ok=True)
        (ctx.run_dir / rel).write_bytes(data)
        size_bytes = len(data)
        shared_with = None
    else:
        rel = shared_rec.file_path
        size_bytes = (ctx.run_dir / rel).stat().st_size
        shared_with = shared_rec.asset_id
        how = f"{how}; reused from {shared_with} (no unused candidate available)"
    artist = (f.artist or f.credit)[:200] or None
    credit = f"{artist or 'unknown author'}, {f.license_short}, via Wikimedia Commons ({f.page_url})"
    cand_log.append(CandidateRecord(title=f.title, url=f.page_url, license=f.license_short, origin=f.origin,
                                    decision="selected", reason=how, score=score))
    notes = [f"target {primary.name!r} ({primary.verification.value})"]
    notes += [f"Wikidata candidate {r}" for r in ctx.wd_rejected.get(primary.name, [])[:3]]
    if primary.verification.value == "to_verify":
        notes.append("target is still to_verify: the file matches the target only by Commons/Wikidata metadata, not by content")
    if uncovered:
        notes.append(f"multi-target requirement: only the first target was acquired; not covered: {uncovered} (split in C / revise)")
    if shared_with:
        notes.append(f"shared with {shared_with} (no unused candidate available)")
    rec = AssetRecord(
        asset_id=rq.asset_id, status=AssetStatus.acquired, type=AssetType.image, file_path=rel, source_url=f.page_url,
        source_name="Wikimedia Commons", license_or_terms=f"{f.license_short}" + (f" ({f.license_url})" if f.license_url else ""),
        author=artist, attribution=credit if (needs_attribution(f.license_short) or artist) else None,
        identifier=f.title, entity=entity[0] if entity else None, sha256=sha, mime=f.mime, width=w, height=h,
        retrieved_at=now(), notes="; ".join(notes), shared_with=shared_with, uncovered_targets=uncovered, candidates=cand_log,
        checks=AssetChecks(
            relevance=f"{how}; image content NOT visually inspected",
            usability=f"licence {f.license_short}" + ("; attribution required" if needs_attribution(f.license_short) else ""),
            format=f"{f.mime} {w}x{h}, {size_bytes} bytes, opened with Pillow, sha256 recorded",
            quality="not assessed in D"))
    if shared_rec is None:
        ctx.used_by_id[f.title] = rec
        ctx.used_by_sha[sha] = rec
    return rec


# ------------------------------------------------------------------------------------------ reference
def _acquire_reference(ctx: _Ctx, rq: AssetRequirement) -> AssetRecord:
    sv = ctx.sv
    primary = rq.targets[0] if rq.targets else None
    exact = bool(primary and primary.entity_type.lower() == "publication" and primary.verification.value == "verified"
                 and not _is_cjk(primary.name))
    queries = ([primary.name] if exact else []) + sorted(rq.query_hints, key=lambda h: -_ascii_ratio(h))
    queries = [q for i, q in enumerate(queries) if q not in queries[:i]][:3]
    cand_log: list[CandidateRecord] = []
    oa_failed: list[str] = []
    works: list[Work] = []
    for q in queries:
        res = ctx.call("openalex", "search works", q, lambda q=q: sv.openalex.search(q, 6))
        if res is None:
            oa_failed.append(q)
            break                                  # quota / outage: do not hammer; fall back below
        works += [w for w in res if w.doi and w.openalex_id not in {x.openalex_id for x in works}]
        if exact and any(similar(w.title, primary.name) >= 0.85 for w in works):
            break
        if len(works) >= 3 and not exact:
            break
    origin_note = ""
    if oa_failed and not works:
        cr = CrossrefBackend(sv.http, sv.contact)
        for q in queries[:2]:
            hits = ctx.call("crossref", "fallback search (OpenAlex failed)", q, lambda q=q: cr.search(q, "en", 6))
            for h in hits or []:
                m = re.search(r"DOI (\S+)$", h.text)
                year = re.search(r"\((\d{4})\)", h.text)
                if m:
                    works.append(Work("", bare_doi(m.group(1)), h.title, int(year.group(1)) if year else None, [], "",
                                      h.doc_type, None, None, None, origin="crossref"))
            if works:
                break
        origin_note = "OpenAlex unavailable (see sources); fell back to Crossref search. "
    if not works:
        return _missing(rq, origin_note + f"no work with a DOI found for queries {queries}", candidates=cand_log)
    ctop = primary.name if primary else rq.purpose
    cands = [Candidate(w.title, f"{w.title} — {w.venue} ({w.year})" if w.venue else w.title, "reference", w.origin,
                       f"{ctop} {rq.purpose}", w.title, doc_type=w.type, payload=w) for w in works]
    verdict, scored = _score(ctx, cands)
    ranked = []
    for c in cands:
        w = c.payload
        keep, score, why = verdict[id(c)]
        if keep and not scored:
            want = _terms(ctop, *rq.query_hints)
            if want and not any(t in w.title.lower() for t in want):
                keep, why = False, "no lexical overlap with target/hints (no embedding scorer available)"
        if exact and similar(w.title, primary.name) >= 0.85:
            keep, why, score = True, "", 1.0       # the exact title B/C cited
        if not keep:
            cand_log.append(CandidateRecord(title=w.title, url=f"https://doi.org/{w.doi}", origin=w.origin,
                                            decision="rejected", reason=why, score=score))
        else:
            ranked.append((w, score))
    ranked.sort(key=lambda t: -(t[1] if t[1] is not None else -1))
    chosen = None
    for w, score in ranked:
        try:
            ok, detail = sv.crossref.verify(w.doi, w.title)
            ctx.calls.append(SourceCall(backend="crossref", purpose="verify DOI", query=w.doi, status="ok", hits=1))
        except BackendError as e:
            msg = str(e)
            if "HTTP 404" in msg:
                ctx.calls.append(SourceCall(backend="crossref", purpose="verify DOI", query=w.doi, status="no_results"))
                cand_log.append(CandidateRecord(title=w.title, url=f"https://doi.org/{w.doi}", origin=w.origin,
                                                decision="rejected", reason="DOI not found in Crossref", score=score))
                continue
            ctx.calls.append(SourceCall(backend="crossref", purpose="verify DOI", query=w.doi, status="error", error=msg[:240]))
            ok, detail = True, f"DOI NOT verified: Crossref unreachable ({msg[:80]})"
        if not ok:
            cand_log.append(CandidateRecord(title=w.title, url=f"https://doi.org/{w.doi}", origin=w.origin,
                                            decision="rejected", reason=detail, score=score))
            continue
        chosen = (w, score, detail)
        break
    if chosen is None:
        return _missing(rq, origin_note + "candidates found but none was relevant and DOI-verified", candidates=cand_log)
    w, score, detail = chosen
    rec = {"title": w.title, "authors": w.authors, "year": w.year, "venue": w.venue, "type": w.type, "doi": w.doi,
           "url": f"https://doi.org/{w.doi}", "openalex_id": w.openalex_id or None, "open_access": w.is_oa,
           "oa_url": w.oa_url, "source": "OpenAlex" if w.origin == "openalex" else "Crossref", "retrieved_at": now().isoformat(),
           "note": "citation record only; no abstract or full text retrieved"}
    rel = f"assets/{rq.asset_id}.json"
    (ctx.run_dir / "assets").mkdir(exist_ok=True)
    (ctx.run_dir / rel).write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cand_log.append(CandidateRecord(title=w.title, url=rec["url"], origin=w.origin, decision="selected",
                                    reason=("exact title of the cited publication" if exact and score == 1.0 else
                                            f"embedding score {score:.2f}" if score is not None else "structural rules only"),
                                    score=score))
    is_oa = w.origin == "openalex"
    return AssetRecord(
        asset_id=rq.asset_id, status=AssetStatus.acquired, type=AssetType.reference, file_path=rel, source_url=rec["url"],
        source_name="OpenAlex (pyalex)" if is_oa else "Crossref",
        license_or_terms=("OpenAlex metadata, CC0; bibliographic record only" if is_oa
                          else "Crossref metadata (bibliographic record only)"),
        author=", ".join(a for a in w.authors[:3] if a) or None, identifier=w.doi, retrieved_at=now(),
        notes=(origin_note + (f"target {primary.name!r} ({primary.verification.value})" if primary else "no named target")).strip(),
        candidates=cand_log,
        checks=AssetChecks(relevance=("exact title match" if exact and score == 1.0 else
                                      f"embedding score {score:.2f} on title/venue; abstract not read" if score is not None
                                      else "structural rules only; abstract not read"),
                           usability="citation metadata only; full text not retrieved", format=detail,
                           quality="not assessed in D"))


# ------------------------------------------------------------------------------------------ orchestrator
_MISSING_WHY = {
    AssetType.diagram: "D 未找到可标来源的现成图；不允许凭空绘制，请用户提供结构化数据或图",
    AssetType.icon: "D 未找到可标来源的现成图标；不允许凭空绘制，请用户提供开源或有授权的图标文件",
    AssetType.statistic: "statistic: no structured data source is wired in D V0.1; needs a cited dataset (not invented)",
    AssetType.chart_data: "chart_data: no structured data source is wired in D V0.1; needs a cited dataset (not invented)",
    AssetType.table: "table: content must be authored or sourced from a cited dataset; not retrievable in D V0.1",
}


def run_stage_d(plan: ProductionPlan, slide_plan: SlidePlan, reqs: AssetRequirements, run_dir: Path,
                services: DServices, *, on_missing: Literal["placeholder", "wait"] = "placeholder") -> AssetRegistry:
    run_dir = Path(run_dir)
    ctx = _Ctx(plan, services, run_dir)
    records: list[AssetRecord] = []
    for rq in reqs.requirements:
        if not services.enabled:
            records.append(_missing(rq, f"asset retrieval disabled ({services.disabled_reason}); nothing was retrieved"))
            continue
        if rq.type is AssetType.image:
            records.append(_acquire_image(ctx, rq))
        elif rq.type is AssetType.reference:
            records.append(_acquire_reference(ctx, rq))
        else:
            records.append(_missing(rq, _MISSING_WHY[rq.type]))
    sc, model, thr = ctx.scorer_info
    info = RelevanceInfo(mode=services.relevance.mode, rules_version=RULES_VERSION, scorer=sc, model=model, threshold=thr,
                         n_candidates=ctx.n_cand, n_kept=ctx.n_kept, n_filtered=ctx.n_filtered,
                         note=ctx.note or services.relevance.unavailable)
    n_ok = sum(r.status is AssetStatus.acquired for r in records)
    reg = AssetRegistry(
        run_id=plan.run_id, assets=records, sources=ctx.calls or [SourceCall(backend="none", purpose="asset retrieval",
                                                                             status="disabled")],
        relevance_filter=info,
        summary=f"{n_ok}/{len(records)} requirements acquired; {sum(r.status is AssetStatus.missing for r in records)} missing")
    from .validate import validate_registry
    validate_registry(reg, reqs, run_dir)
    write_contract(reg, run_dir / REGISTRY_FILE)
    uir = build_user_input_requests(reqs, reg, on_missing=on_missing)
    write_contract(uir, run_dir / USER_INPUT_REQUESTS_FILE)
    real = [c for c in ctx.calls if c.status != "disabled"]
    if services.enabled and real and all(c.status == "error" for c in real):
        raise AssetRetrievalFailed(f"all {len(real)} asset source calls failed (network/quota); nothing retrieved: "
                                   f"{real[0].error}", reg)
    return reg
