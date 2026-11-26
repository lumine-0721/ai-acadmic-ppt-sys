"""Real search backends for stage B research (no paid key needed for the first two).

  wikipedia  MediaWiki Action API, intro extracts  (zh / en)   CC BY-SA
  crossref   Crossref REST works search            (en only)   bibliographic metadata, no abstracts
  searxng    any SearXNG instance with the JSON API (optional; URL from env)
  keenable   Keenable AI  POST /v1/search + /v1/fetch  (API key + env gate required)

Each backend raises BackendError on network/HTTP/parse failure; nothing is ever
fabricated. Backends take an optional httpx transport ONLY so unit tests can
replay canned HTTP responses.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

DEFAULT_CONTACT = "https://github.com/lumine-0721/ai-acadmic-ppt-sys"
_SECRET_QUERY_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "key",
        "token",
        "access_token",
        "secret",
        "auth",
        "password",
        "passwd",
        "sig",
        "signature",
    }
)


def sanitize_url(url: str) -> str:
    """Return ``url`` stripped of userinfo credentials and secret query parameters."""
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        sp = urlsplit(raw)
        netloc = sp.hostname or ""
        if sp.port is not None:
            netloc = f"{netloc}:{sp.port}"
        if not netloc and sp.netloc:
            # Strip any user:pass@ prefix if hostname parsing fell back
            netloc = sp.netloc.rsplit("@", 1)[-1]
        if sp.query:
            kept = [
                (k, v)
                for k, v in parse_qsl(sp.query, keep_blank_values=True)
                if k.lower() not in _SECRET_QUERY_KEYS
            ]
            query = urlencode(kept, doseq=True)
        else:
            query = ""
        return urlunsplit((sp.scheme, netloc, sp.path, query, sp.fragment))
    except Exception:
        return raw.split("?", 1)[0]


class BackendError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        url: str = "",
        elapsed_s: float | None = None,
        attempts: int | None = None,
        timed_out: bool = False,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.url = sanitize_url(url) if url else ""
        self.elapsed_s = elapsed_s
        self.attempts = attempts
        self.timed_out = timed_out
        self.status_code = status_code


@dataclass
class SearchHit:
    title: str
    text: str
    url: str
    kind: str                # encyclopedia | reference | web
    source_name: str
    license_or_terms: str
    retrieved_at: datetime
    doc_type: str = ""


class SearchBackend(Protocol):
    name: str

    def supports(self, lang: str) -> bool: ...

    def search(self, query: str, lang: str, limit: int) -> list[SearchHit]: ...


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def make_http(
    timeout_s: float,
    contact: str,
    transport: httpx.BaseTransport | None = None,
    *,
    connect_timeout_s: float | None = None,
    read_timeout_s: float | None = None,
) -> httpx.Client:
    """Create an httpx client with explicit connect/read/write/pool timeouts and env proxy support."""
    ua = f"pptv01/0.1 (research; contact: {contact}) httpx"
    total = max(0.05, float(timeout_s))
    c_to = max(0.05, float(connect_timeout_s)) if connect_timeout_s is not None else min(10.0, total)
    r_to = max(0.05, float(read_timeout_s)) if read_timeout_s is not None else min(20.0, total)
    timeout = httpx.Timeout(connect=c_to, read=r_to, write=min(r_to, 15.0), pool=min(c_to, 10.0))
    return httpx.Client(
        timeout=timeout,
        transport=transport,
        headers={"User-Agent": ua},
        follow_redirects=True,
        trust_env=True,
    )


_sleep = time.sleep          # indirection so unit tests can skip the waits
RETRIES = 2                  # extra attempts after the first (429 / 5xx / timeouts are transient)


def _clamp_timeout(http: httpx.Client, deadline: float | None) -> httpx.Timeout | None:
    if deadline is None:
        return None
    rem = max(0.05, deadline - time.monotonic())
    base = http.timeout
    c = base.connect if base.connect is not None else 10.0
    r = base.read if base.read is not None else 20.0
    w = base.write if base.write is not None else 15.0
    p = base.pool if base.pool is not None else 10.0
    return httpx.Timeout(connect=min(c, rem), read=min(r, rem), write=min(w, rem), pool=min(p, rem))


def _get_json(
    http: httpx.Client,
    url: str,
    params: dict,
    *,
    provider: str = "",
    deadline: float | None = None,
    max_backoff_total_s: float = 12.0,
    retry_on_429: bool = True,
) -> dict:
    eff_deadline = deadline if deadline is not None else getattr(http, "_pptv01_deadline", None)
    host = httpx.URL(url).host
    prov = provider or host or "http"
    try:
        clean_url = sanitize_url(str(httpx.URL(url, params=params)))
    except Exception:
        clean_url = sanitize_url(url)

    t0 = time.monotonic()
    last = "unknown error"
    last_status: int | None = None
    attempts = 0
    backoff_used = 0.0
    timed_out = False

    for attempt in range(RETRIES + 1):
        if eff_deadline is not None and time.monotonic() >= eff_deadline:
            elapsed = round(time.monotonic() - t0, 3)
            raise BackendError(
                f"budget exhausted before request (provider={prov}, url={clean_url})",
                provider=prov,
                url=clean_url,
                elapsed_s=elapsed,
                attempts=attempts,
                timed_out=True,
                status_code=last_status,
            )
        attempts += 1
        wait = 1.0 * (2 ** attempt)
        call_timeout = _clamp_timeout(http, eff_deadline)
        try:
            if call_timeout is not None:
                r = http.get(url, params=params, timeout=call_timeout)
            else:
                r = http.get(url, params=params)
        except httpx.TimeoutException as e:
            timed_out = True
            last = f"{type(e).__name__}: {str(e)[:120]}"
        except httpx.HTTPError as e:
            last = f"{type(e).__name__}: {str(e)[:120]}"
        else:
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError:
                    elapsed = round(time.monotonic() - t0, 3)
                    raise BackendError(
                        f"non-JSON response from {host} (provider={prov}, url={clean_url})",
                        provider=prov,
                        url=clean_url,
                        elapsed_s=elapsed,
                        attempts=attempts,
                        status_code=r.status_code,
                    ) from None
            last_status = r.status_code
            last = f"HTTP {r.status_code} from {host}"
            if (r.status_code == 429 and retry_on_429) or r.status_code >= 500:
                try:
                    wait = min(float(r.headers.get("Retry-After", wait)), 10.0)
                except ValueError:
                    pass
            else:
                elapsed = round(time.monotonic() - t0, 3)
                raise BackendError(
                    f"{last} (provider={prov}, url={clean_url}, elapsed={elapsed:.2f}s, attempts={attempts})",
                    provider=prov,
                    url=clean_url,
                    elapsed_s=elapsed,
                    attempts=attempts,
                    status_code=r.status_code,
                )  # 4xx (or non-retrying 429): fail fast without backoff

        if attempt < RETRIES:
            rem_backoff = max_backoff_total_s - backoff_used
            if rem_backoff <= 0:
                break
            wait = min(wait, rem_backoff)
            if eff_deadline is not None:
                rem_budget = eff_deadline - time.monotonic()
                if rem_budget <= wait + 0.05:
                    timed_out = True
                    break
            backoff_used += wait
            _sleep(wait)

    elapsed = round(time.monotonic() - t0, 3)
    raise BackendError(
        f"{last} (after {attempts} attempts, provider={prov}, url={clean_url}, elapsed={elapsed:.2f}s)",
        provider=prov,
        url=clean_url,
        elapsed_s=elapsed,
        attempts=attempts,
        timed_out=timed_out,
        status_code=last_status,
    )


def _post_json(
    http: httpx.Client,
    url: str,
    body: dict,
    headers: dict | None = None,
    *,
    provider: str = "",
) -> dict:
    try:
        clean_url = sanitize_url(url)
    except Exception:
        clean_url = url

    t0 = time.monotonic()
    last = "unknown error"
    last_status: int | None = None
    attempts = 0
    backoff_used = 0.0
    timed_out = False
    max_backoff_total_s = 12.0

    for attempt in range(RETRIES + 1):
        attempts += 1
        wait = 1.0 * (2 ** attempt)
        try:
            r = http.post(url, json=body, headers=headers)
        except httpx.TimeoutException as e:
            timed_out = True
            last = f"{type(e).__name__}: {str(e)[:120]}"
        except httpx.HTTPError as e:
            last = f"{type(e).__name__}: {str(e)[:120]}"
        else:
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError:
                    elapsed = round(time.monotonic() - t0, 3)
                    host = httpx.URL(url).host
                    prov = provider or host or "http"
                    raise BackendError(
                        f"non-JSON response from {host} (provider={prov}, url={clean_url})",
                        provider=prov,
                        url=clean_url,
                        elapsed_s=elapsed,
                        attempts=attempts,
                        status_code=r.status_code,
                    ) from None
            last_status = r.status_code
            last = f"HTTP {r.status_code}"
            if r.status_code == 429 or r.status_code >= 500:
                try:
                    wait = min(float(r.headers.get("Retry-After", wait)), 10.0)
                except ValueError:
                    pass
            else:
                elapsed = round(time.monotonic() - t0, 3)
                prov = provider or httpx.URL(url).host or "http"
                raise BackendError(
                    f"{last} (provider={prov}, url={clean_url}, elapsed={elapsed:.2f}s, attempts={attempts})",
                    provider=prov,
                    url=clean_url,
                    elapsed_s=elapsed,
                    attempts=attempts,
                    status_code=r.status_code,
                )

        if attempt < RETRIES:
            rem_backoff = max_backoff_total_s - backoff_used
            if rem_backoff <= 0:
                break
            wait = min(wait, rem_backoff)
            backoff_used += wait
            _sleep(wait)

    elapsed = round(time.monotonic() - t0, 3)
    prov = provider or httpx.URL(url).host or "http"
    raise BackendError(
        f"{last} (after {attempts} attempts, provider={prov}, url={clean_url}, elapsed={elapsed:.2f}s)",
        provider=prov,
        url=clean_url,
        elapsed_s=elapsed,
        attempts=attempts,
        timed_out=timed_out,
        status_code=last_status,
    )


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
        }, provider="wikipedia")
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
        data = _get_json(self.http, "https://api.crossref.org/works", params, provider="crossref")
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
                retrieved_at=_now(), doc_type=str(it.get("type") or ""),
            ))
        return out


class KeenableBackend:
    name = "keenable"

    def __init__(self, http: httpx.Client, api_key: str):
        self.http = http
        self._api_key = api_key

    def supports(self, lang: str) -> bool:
        return True

    def search(self, query: str, lang: str, limit: int) -> list[SearchHit]:
        data = _post_json(
            self.http,
            "https://api.keenable.ai/v1/search",
            {"query": query, "max_results": limit},
            headers={"X-API-Key": self._api_key},
            provider="keenable",
        )
        results = data.get("results")
        if not isinstance(results, list):
            raise BackendError("keenable: malformed response")
        out = []
        for it in results:
            title = it.get("title")
            url = it.get("url")
            if not title or not url:
                continue
            snippet = it.get("snippet") or it.get("description") or ""
            text = re.sub(r"\s+", " ", snippet).strip()[:600]
            out.append(SearchHit(
                title=title,
                text=text,
                url=url,
                kind="web",
                source_name=httpx.URL(url).host or "Keenable",
                license_or_terms="see source page",
                retrieved_at=_now(),
            ))
        return out

    def fetch(self, urls: list[str], prompt: str = "") -> list[dict]:
        body: dict = {"urls": list(urls)}
        if prompt:
            body["content"] = {"prompt": prompt}
        data = _post_json(
            self.http,
            "https://api.keenable.ai/v1/fetch",
            body,
            headers={"X-API-Key": self._api_key},
            provider="keenable",
        )
        results = data.get("results")
        if not isinstance(results, list):
            raise BackendError("keenable: malformed response")
        return results


class SearxngBackend:
    name = "searxng"

    def __init__(self, http: httpx.Client, base_url: str):
        self.http = http
        self.base = base_url.rstrip("/")

    def supports(self, lang: str) -> bool:
        return True

    def search(self, query: str, lang: str, limit: int) -> list[SearchHit]:
        data = _get_json(self.http, f"{self.base}/search", {"q": query, "format": "json", "language": lang}, provider="searxng")
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
