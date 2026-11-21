"""Mature public APIs used by stage D (no new scrapers):

  Wikidata   entity search + P18 (representative image on Commons)   https://www.wikidata.org/w/api.php
  Commons    file search + imageinfo/extmetadata (licence, author)   https://commons.wikimedia.org/w/api.php
  OpenAlex   works search through pyalex (MIT)                       https://docs.openalex.org
  Crossref   DOI verification                                        https://api.crossref.org

Every failure raises BackendError (never swallowed into fake data). HTTP is injectable (httpx transport) and the
OpenAlex fetch is injectable, so unit tests replay recorded/canned responses instead of touching the network.
"""

from __future__ import annotations

import hashlib
import io
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Callable

import httpx

from pptv01.retrieval.backends import BackendError, _clamp_timeout, _get_json, make_http, sanitize_url

from .license import plain

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
OPENALEX_WORKS = "https://api.openalex.org/works"
CROSSREF_WORKS = "https://api.crossref.org/works"
MAX_DOWNLOAD_BYTES = 12 * 1024 * 1024
IMAGE_MIMES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}   # raster only in V0.1 (Pillow-verifiable)
MIN_RASTER_WIDTH = 600
MAX_ASPECT = 3.0              # long side / short side; panoramas and banners are not usable as slide pictures


def now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


# ------------------------------------------------------------------------------------------ Wikidata
@dataclass
class WdEntity:
    qid: str
    label: str
    description: str
    label_en: str = ""
    label_zh: str = ""
    p18: list[str] = field(default_factory=list)       # Commons file names (no "File:" prefix)
    match_text: str = ""                               # text of the label/alias that matched the search (wbsearchentities)
    aliases: list[str] = field(default_factory=list)   # en/zh aliases from Wikidata
    country_qids: list[str] = field(default_factory=list)  # P17 (country) QIDs

    @property
    def url(self) -> str:
        return f"https://www.wikidata.org/wiki/{self.qid}"


class WikidataClient:
    def __init__(self, http: httpx.Client):
        self.http = http

    def search(self, name: str, lang: str, limit: int = 5) -> list[WdEntity]:
        d = _get_json(self.http, WIKIDATA_API, {"action": "wbsearchentities", "search": name, "language": lang,
                                                "uselang": lang, "type": "item", "limit": limit, "format": "json"},
                      provider="wikidata")
        if "error" in d:
            raise BackendError(f"wikidata error: {str(d['error'])[:120]}", provider="wikidata", url=WIKIDATA_API)
        return [WdEntity(x["id"], x.get("label") or x["id"], x.get("description") or "",
                         match_text=(x.get("match") or {}).get("text", ""))
                for x in d.get("search", []) if x.get("id")]

    def instance_of(self, qids: list[str]) -> dict[str, set[str]]:
        """P31 (instance of) per entity, one batched call. Entities missing from the answer are simply absent."""
        d = _get_json(self.http, WIKIDATA_API, {"action": "wbgetentities", "ids": "|".join(qids), "props": "claims",
                                                "format": "json"}, provider="wikidata")
        out: dict[str, set[str]] = {}
        for qid, e in (d.get("entities") or {}).items():
            if not isinstance(e, dict) or "missing" in e:
                continue
            out[qid] = {(c.get("mainsnak", {}).get("datavalue") or {}).get("value", {}).get("id", "")
                        for c in (e.get("claims") or {}).get("P31", [])
                        if isinstance(((c.get("mainsnak") or {}).get("datavalue") or {}).get("value"), dict)} - {""}
        return out

    def entity(self, qid: str) -> WdEntity:
        d = _get_json(self.http, WIKIDATA_API, {"action": "wbgetentities", "ids": qid, "props": "claims|labels|descriptions|aliases",
                                                "languages": "zh|en", "format": "json"}, provider="wikidata")
        e = (d.get("entities") or {}).get(qid)
        if not e or "missing" in e:
            raise BackendError(f"wikidata entity {qid} not found", provider="wikidata", url=WIKIDATA_API)
        labels, desc = e.get("labels", {}), e.get("descriptions", {})
        en, zh = labels.get("en", {}).get("value", ""), labels.get("zh", {}).get("value", "")
        aliases: list[str] = []
        for lang_list in (e.get("aliases") or {}).values():
            for item in lang_list or []:
                val = (item or {}).get("value", "").strip()
                if val and val not in aliases:
                    aliases.append(val)
        files = []
        for c in e.get("claims", {}).get("P18", []):
            v = (c.get("mainsnak", {}).get("datavalue") or {}).get("value")
            if isinstance(v, str) and c.get("rank") != "deprecated":
                files.append(v)
        country_qids: list[str] = []
        for c in e.get("claims", {}).get("P17", []):
            v = (c.get("mainsnak", {}).get("datavalue") or {}).get("value")
            if isinstance(v, dict) and v.get("id") and c.get("rank") != "deprecated":
                if v["id"] not in country_qids:
                    country_qids.append(v["id"])
        return WdEntity(qid, en or zh or qid, desc.get("en", {}).get("value") or desc.get("zh", {}).get("value", ""),
                        en, zh, files, aliases=aliases, country_qids=country_qids)


# Wikidata classes that are never the *subject* of an image (papers about it, list/disambiguation pages, ...).
NON_SUBJECT_CLASSES = {
    "Q13442814": "scholarly article", "Q18918145": "academic journal article", "Q191067": "article",
    "Q7318358": "review article", "Q1266946": "thesis", "Q187685": "doctoral thesis", "Q1348305": "doctoral dissertation",
    "Q3331189": "version, edition or translation", "Q4167410": "Wikimedia disambiguation page",
    "Q13406463": "Wikimedia list article", "Q4167836": "Wikimedia category", "Q11266439": "Wikimedia template",
}


# ------------------------------------------------------------------------------------------ Commons
@dataclass
class CommonsFile:
    title: str                  # "File:Foo.jpg"
    page_url: str
    file_url: str               # URL we download (thumbnail <= width, or original for svg)
    mime: str
    width: int
    height: int
    size: int
    license_short: str
    license_url: str
    usage_terms: str
    attribution_required: bool
    artist: str
    credit: str
    description: str
    restrictions: str
    origin: str = "commons_search"
    categories: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return re.sub(r"\.\w{2,5}$", "", self.title.removeprefix("File:")).replace("_", " ")


class CommonsClient:
    def __init__(self, http: httpx.Client, thumb_width: int = 1280):
        self.http, self.thumb_width = http, thumb_width

    def _parse(self, pages: list[dict], origin: str) -> list[CommonsFile]:
        out = []
        for p in sorted(pages, key=lambda x: x.get("index", 0)):
            ii = (p.get("imageinfo") or [None])[0]
            if not ii or p.get("missing"):
                continue
            em = ii.get("extmetadata") or {}

            def ev(k):
                return (em.get(k) or {}).get("value", "")
            mime = ii.get("mime", "")
            url = ii.get("url", "") if mime == "image/svg+xml" else (ii.get("thumburl") or ii.get("url", ""))
            cats = [c.strip() for c in plain(ev("Categories")).split("|") if c.strip()]
            out.append(CommonsFile(
                title=p["title"], page_url=ii.get("descriptionurl", ""), file_url=url, mime=mime,
                width=int(ii.get("width") or 0), height=int(ii.get("height") or 0),      # original size (size gate)
                size=int(ii.get("size") or 0),
                license_short=plain(ev("LicenseShortName")), license_url=plain(ev("LicenseUrl")),
                usage_terms=plain(ev("UsageTerms")), attribution_required=str(ev("AttributionRequired")).lower() == "true",
                artist=plain(ev("Artist")), credit=plain(ev("Credit")), description=plain(ev("ImageDescription")),
                restrictions=plain(ev("Restrictions")), origin=origin, categories=cats))
        return out

    _INFO = {"prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "format": "json", "formatversion": 2}

    def search(self, query: str, limit: int = 8) -> list[CommonsFile]:
        d = _get_json(self.http, COMMONS_API, {**self._INFO, "action": "query", "generator": "search",
                                               "gsrsearch": query, "gsrnamespace": 6, "gsrlimit": limit,
                                               "iiurlwidth": self.thumb_width}, provider="commons")
        if "error" in d:
            raise BackendError(f"commons error: {str(d['error'])[:120]}", provider="commons", url=COMMONS_API)
        return self._parse((d.get("query") or {}).get("pages", []), "commons_search")

    def files(self, names: list[str], origin: str = "wikidata_p18") -> list[CommonsFile]:
        if not names:
            return []
        d = _get_json(self.http, COMMONS_API, {**self._INFO, "action": "query", "iiurlwidth": self.thumb_width,
                                               "titles": "|".join("File:" + n for n in names)}, provider="commons")
        if "error" in d:
            raise BackendError(f"commons error: {str(d['error'])[:120]}", provider="commons", url=COMMONS_API)
        return self._parse((d.get("query") or {}).get("pages", []), origin)


def download_image(
    http: httpx.Client,
    url: str,
    *,
    deadline: float | None = None,
    max_backoff_total_s: float = 12.0,
) -> tuple[bytes, str]:
    """GET with bounded timeout, retries, and budget check; returns (bytes, content-type)."""
    eff_deadline = deadline if deadline is not None else getattr(http, "_pptv01_deadline", None)
    clean_url = sanitize_url(url)
    t0 = time.monotonic()
    last = ""
    attempts = 0
    backoff_used = 0.0
    timed_out = False

    for attempt in range(3):
        if eff_deadline is not None and time.monotonic() >= eff_deadline:
            elapsed = round(time.monotonic() - t0, 3)
            raise BackendError(
                f"download budget exhausted (provider=commons, url={clean_url})",
                provider="commons",
                url=clean_url,
                elapsed_s=elapsed,
                attempts=attempts,
                timed_out=True,
            )
        attempts += 1
        call_timeout = _clamp_timeout(http, eff_deadline)
        try:
            if call_timeout is not None:
                r = http.get(url, timeout=call_timeout)
            else:
                r = http.get(url)
        except httpx.TimeoutException as e:
            timed_out = True
            last = f"{type(e).__name__}"
        except httpx.HTTPError as e:
            last = f"{type(e).__name__}"
        else:
            if r.status_code == 200:
                if len(r.content) > MAX_DOWNLOAD_BYTES:
                    raise BackendError(
                        f"image larger than {MAX_DOWNLOAD_BYTES} bytes",
                        provider="commons",
                        url=clean_url,
                        elapsed_s=round(time.monotonic() - t0, 3),
                        attempts=attempts,
                    )
                return r.content, r.headers.get("content-type", "").split(";")[0].strip()
            last = f"HTTP {r.status_code}"
            if r.status_code not in (429, 500, 502, 503, 504):
                break
        if attempt < 2:
            wait = min(1.0 * (2 ** attempt), max(0.0, max_backoff_total_s - backoff_used))
            if wait <= 0:
                break
            if eff_deadline is not None and eff_deadline - time.monotonic() <= wait + 0.05:
                timed_out = True
                break
            backoff_used += wait
            import pptv01.retrieval.backends as b
            b._sleep(wait)

    elapsed = round(time.monotonic() - t0, 3)
    raise BackendError(
        f"download failed: {last} (provider=commons, url={clean_url}, elapsed={elapsed:.2f}s, attempts={attempts})",
        provider="commons",
        url=clean_url,
        elapsed_s=elapsed,
        attempts=attempts,
        timed_out=timed_out,
    )


def verify_image(data: bytes, mime: str) -> tuple[int, int, str]:
    """Open with Pillow (raster) or sniff <svg (vector). Returns (w, h, sha256)."""
    sha = hashlib.sha256(data).hexdigest()
    if mime == "image/svg+xml":
        if b"<svg" not in data[:4096].lower():
            raise BackendError("downloaded file is not an SVG")
        return 0, 0, sha
    from PIL import Image
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
        with Image.open(io.BytesIO(data)) as im:
            return im.size[0], im.size[1], sha
    except Exception as e:  # noqa: BLE001
        raise BackendError(f"downloaded file is not a readable image: {type(e).__name__}") from None


# ------------------------------------------------------------------------------------------ OpenAlex
@dataclass
class Work:
    openalex_id: str
    doi: str                    # bare DOI, lowercase, no URL prefix
    title: str
    year: int | None
    authors: list[str]
    venue: str
    type: str
    is_oa: bool | None
    oa_url: str | None
    cited_by: int | None
    origin: str = "openalex"


def bare_doi(d: str | None) -> str:
    return re.sub(r"^https?://(?:dx\.)?doi\.org/", "", (d or "").strip(), flags=re.I).lower()


SELECT = ["id", "doi", "title", "publication_year", "type", "primary_location", "authorships", "open_access",
          "cited_by_count"]


def openalex_http_fetch(
    http: httpx.Client,
    query: str,
    limit: int,
    *,
    api_key: str = "",
    email: str = "",
    deadline: float | None = None,
) -> list[dict]:
    """Fetch OpenAlex works via shared httpx client (explicit connect/read timeouts, proxy & budget aware)."""
    params: dict[str, str | int] = {
        "search": query,
        "per-page": limit,
        "select": ",".join(SELECT),
    }
    if email:
        params["mailto"] = email
    if api_key:
        params["api_key"] = api_key
    try:
        d = _get_json(http, OPENALEX_WORKS, params, provider="openalex", deadline=deadline)
    except BackendError as e:
        msg = str(e)
        if re.search(r"\b(?:402|403|429)\b|budget|quota|rate.?limit", msg, re.I) and "budget exhausted" not in msg.lower():
            raise BackendError(
                f"OpenAlex quota/rate limit reached: set PPTV01_OPENALEX_API_KEY (free key) or retry tomorrow: {msg[:160]}",
                provider="openalex",
                url=e.url or OPENALEX_WORKS,
                elapsed_s=e.elapsed_s,
                attempts=e.attempts,
                timed_out=e.timed_out,
            ) from None
        raise
    if not isinstance(d, dict):
        raise BackendError("OpenAlex error: unexpected non-object response", provider="openalex", url=OPENALEX_WORKS)
    return list(d.get("results") or [])


def pyalex_fetch(
    query: str,
    limit: int,
    *,
    api_key: str = "",
    email: str = "",
    connect_timeout_s: float = 10.0,
    read_timeout_s: float = 20.0,
) -> list[dict]:
    """Default OpenAlex fetch over timeout-enforced httpx (replaces untimeouted pyalex/requests)."""
    contact = email or "https://github.com/lumine-0721/ai-acadmic-ppt-sys"
    with make_http(
        max(connect_timeout_s, read_timeout_s),
        contact,
        connect_timeout_s=connect_timeout_s,
        read_timeout_s=read_timeout_s,
    ) as http:
        return openalex_http_fetch(http, query, limit, api_key=api_key, email=email)


class OpenAlexClient:
    def __init__(
        self,
        fetch: Callable[[str, int], list[dict]] | None = None,
        *,
        api_key: str = "",
        email: str = "",
        http: httpx.Client | None = None,
    ):
        if fetch is not None:
            self._fetch = fetch
        elif http is not None:
            self._fetch = lambda q, n: openalex_http_fetch(http, q, n, api_key=api_key, email=email)
        else:
            self._fetch = lambda q, n: pyalex_fetch(q, n, api_key=api_key, email=email)
        self.keyed = bool(api_key)

    def search(self, query: str, limit: int = 5) -> list[Work]:
        out = []
        for w in self._fetch(query, limit):
            title = w.get("title") or w.get("display_name")
            doi = bare_doi(w.get("doi"))
            if not title or not w.get("id"):
                continue
            loc = (w.get("primary_location") or {})
            oa = w.get("open_access") or {}
            out.append(Work(
                openalex_id=w["id"], doi=doi, title=title, year=w.get("publication_year"),
                authors=[(a.get("author") or {}).get("display_name", "") for a in (w.get("authorships") or [])][:6],
                venue=((loc.get("source") or {}).get("display_name") or ""), type=w.get("type") or "",
                is_oa=oa.get("is_oa"), oa_url=oa.get("oa_url"), cited_by=w.get("cited_by_count")))
        return out


# ------------------------------------------------------------------------------------------ Crossref
def similar(a: str, b: str) -> float:
    n = lambda s: re.sub(r"[^\w]+", " ", s.lower()).strip()  # noqa: E731
    return SequenceMatcher(None, n(a), n(b)).ratio()


class CrossrefVerifier:
    def __init__(self, http: httpx.Client, contact: str = ""):
        self.http, self.contact = http, contact

    def work(self, doi: str) -> dict:
        params = {"mailto": self.contact} if "@" in self.contact else {}
        d = _get_json(self.http, f"{CROSSREF_WORKS}/{doi}", params, provider="crossref")
        return d.get("message") or {}

    def verify(self, doi: str, title: str) -> tuple[bool, str]:
        """(ok, detail). ok only if the DOI resolves in Crossref and its title matches ours (>= 0.8)."""
        m = self.work(doi)
        titles = m.get("title") or []
        if not titles:
            return False, "Crossref record has no title"
        sim = similar(titles[0], title)
        return sim >= 0.8, f"Crossref title similarity {sim:.2f}: {titles[0][:80]!r}"
