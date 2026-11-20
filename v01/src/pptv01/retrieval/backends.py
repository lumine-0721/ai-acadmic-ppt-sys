"""Real search backends for stage B research (no paid key needed for the first two).

  wikipedia  MediaWiki Action API, intro extracts  (zh / en)   CC BY-SA
  crossref   Crossref REST works search            (en only)   bibliographic metadata, no abstracts
  searxng    any SearXNG instance with the JSON API (optional; URL from env)

Each backend raises BackendError on network/HTTP/parse failure; nothing is ever
fabricated. Backends take an optional httpx transport ONLY so unit tests can
replay canned HTTP responses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

import httpx

DEFAULT_CONTACT = "https://github.com/lumine-0721/ai-acadmic-ppt-sys"


class BackendError(RuntimeError):
    pass


@dataclass
class SearchHit:
    title: str
    text: str
    url: str
    kind: str                # encyclopedia | reference | web
    source_name: str
    license_or_terms: str
    retrieved_at: datetime


class SearchBackend(Protocol):
    name: str

    def supports(self, lang: str) -> bool: ...

    def search(self, query: str, lang: str, limit: int) -> list[SearchHit]: ...


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def make_http(timeout_s: float, contact: str, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    ua = f"pptv01/0.1 (research; contact: {contact}) httpx"
    return httpx.Client(timeout=timeout_s, transport=transport, headers={"User-Agent": ua}, follow_redirects=True)


def _get_json(http: httpx.Client, url: str, params: dict) -> dict:
    try:
        r = http.get(url, params=params)
    except httpx.HTTPError as e:
        raise BackendError(f"{type(e).__name__}: {str(e)[:120]}") from None
    if r.status_code != 200:
        raise BackendError(f"HTTP {r.status_code} from {httpx.URL(url).host}")
    try:
        return r.json()
    except ValueError:
        raise BackendError(f"non-JSON response from {httpx.URL(url).host}") from None


class WikipediaBackend:
    name = "wikipedia"

    def __init__(self, http: httpx.Client, extract_chars: int = 600):
        self.http = http
        self.extract_chars = extract_chars

    def supports(self, lang: str) -> bool:
        return lang in ("zh", "en")

    def search(self, query: str, lang: str, limit: int) -> list[SearchHit]:
        data = _get_json(self.http, f"https://{lang}.wikipedia.org/w/api.php", {
            "action": "query", "format": "json", "formatversion": 2,
            "generator": "search", "gsrsearch": query, "gsrlimit": limit,
            "prop": "extracts|info", "exintro": 1, "explaintext": 1, "exchars": self.extract_chars,
            "inprop": "url", "redirects": 1,
            **({"variant": "zh-cn"} if lang == "zh" else {}),
        })
        if "error" in data:
            raise BackendError(f"wikipedia API error: {str(data['error'])[:120]}")
        pages = data.get("query", {}).get("pages", [])
        pages = sorted(pages, key=lambda p: p.get("index", 0))
        out = []
        for p in pages:
            url = p.get("fullurl")
            title = p.get("title")
            if not url or not title:
                continue
            out.append(SearchHit(
                title=title,
                text=re.sub(r"\s+", " ", p.get("extract") or "").strip(),
                url=url, kind="encyclopedia", source_name=f"Wikipedia ({lang})",
                license_or_terms="CC BY-SA 4.0 (Wikipedia text)", retrieved_at=_now(),
            ))
        return out


class CrossrefBackend:
    name = "crossref"

    def __init__(self, http: httpx.Client, contact: str = DEFAULT_CONTACT):
        self.http = http
        self.contact = contact

    def supports(self, lang: str) -> bool:
        return lang == "en"

    def search(self, query: str, lang: str, limit: int) -> list[SearchHit]:
        params = {"query.bibliographic": query, "rows": limit,
                  "select": "DOI,title,container-title,issued,URL,type"}
        if "@" in self.contact:
            params["mailto"] = self.contact
        data = _get_json(self.http, "https://api.crossref.org/works", params)
        items = data.get("message", {}).get("items", [])
        out = []
        for it in items:
            titles = it.get("title") or []
            doi = it.get("DOI")
            if not titles or not doi:
                continue
            container = (it.get("container-title") or [""])[0]
            year = ""
            parts = (it.get("issued") or {}).get("date-parts") or [[None]]
            if parts and parts[0] and parts[0][0]:
                year = str(parts[0][0])
            text = f"{titles[0]}" + (f" — {container}" if container else "") + (f" ({year})" if year else "") + f"; DOI {doi}"
            out.append(SearchHit(
                title=titles[0], text=text, url=it.get("URL") or f"https://doi.org/{doi}",
                kind="reference", source_name="Crossref",
                license_or_terms="Crossref metadata (bibliographic record only; no abstract/full text retrieved)",
                retrieved_at=_now(),
            ))
        return out


class SearxngBackend:
    name = "searxng"

    def __init__(self, http: httpx.Client, base_url: str):
        self.http = http
        self.base = base_url.rstrip("/")

    def supports(self, lang: str) -> bool:
        return True

    def search(self, query: str, lang: str, limit: int) -> list[SearchHit]:
        data = _get_json(self.http, f"{self.base}/search", {"q": query, "format": "json", "language": lang})
        out = []
        for it in (data.get("results") or [])[:limit]:
            url, title = it.get("url"), it.get("title")
            if not url or not title:
                continue
            out.append(SearchHit(
                title=title, text=re.sub(r"\s+", " ", it.get("content") or "").strip()[:600], url=url,
                kind="web", source_name=httpx.URL(url).host or "web", license_or_terms="see source page",
                retrieved_at=_now(),
            ))
        return out
