"""Firecrawl MCP backend — keyless, no paid key needed.

定位: 无 API key 访问 https://mcp.firecrawl.dev/v2/mcp (每日每 IP 限速，
fail-open / graceful degradation 由调用方保证)。不记录 key，错误透明上抛。

MCP JSON-RPC over httpx, 支持两种响应 Content-Type:
  - application/json         → 直接解析
  - text/event-stream (SSE) → 收集所有 data: 行后拼成完整 JSON 再解析
"""

from __future__ import annotations

from datetime import datetime, timezone

import json
import os
from typing import Any

import httpx

from pptv01.retrieval.backends import BackendError, SearchHit, SearchBackend

# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------
_MCP_ENDPOINT = "https://mcp.firecrawl.dev/v2/mcp"
_PROTOCOL_VERSION = "2025-11-25"          # 协商后回落至 2025-03-26
_SUPPORTED_VERSIONS = {"2025-11-25", "2025-06-18", "2025-03-26"}
_TIMEOUT_SECONDS = 30.0


# ----------------------------------------------------------------------
# FirecrawlMcpClient
# ----------------------------------------------------------------------
class FirecrawlMcpClient:
    """MCP JSON-RPC client with SSE fallback, injects httpx.Client for tests."""

    def __init__(
        self,
        transport: httpx.Client | None = None,
        timeout: float = _TIMEOUT_SECONDS,
    ) -> None:
        if transport is not None:
            self._client = transport
        else:
            self._client = httpx.Client(
                timeout=timeout,
                follow_redirects=False,          # spec: 不跟随 3xx
                headers={"MCP-Protocol-Version": _PROTOCOL_VERSION},
            )
        self._session_id: str | None = None
        self._initialized = False
        self._rpc_id = 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _next_id(self) -> int:
        self._rpc_id += 1
        return self._rpc_id

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        key = os.environ.get("FIRECRAWL_API_KEY", "")
        if key:
            h["Authorization"] = f"Bearer {key}"
        return h

    def _request(self, payload: dict[str, Any]) -> httpx.Response:
        try:
            resp = self._client.post(
                _MCP_ENDPOINT,
                headers=self._headers(),
                json=payload,
            )
        except httpx.RequestError as exc:
            raise BackendError(
                f"Firecrawl MCP request failed: {exc}",
                provider="firecrawl",
                url=_MCP_ENDPOINT,
            ) from exc

        # Capture session ID from response headers
        if session_id := resp.headers.get("mcp-session-id"):
            self._session_id = session_id

        # HTTP-level failure
        if resp.status_code == 429:
            raise BackendError(
                "Firecrawl quota exceeded",
                provider="firecrawl",
                url=_MCP_ENDPOINT,
                status_code=429,
            )
        if resp.status_code >= 400:
            raise BackendError(
                f"Firecrawl MCP HTTP {resp.status_code}",
                provider="firecrawl",
                url=_MCP_ENDPOINT,
                status_code=resp.status_code,
            )
        return resp

    def _parse_response(self, resp: httpx.Response) -> dict[str, Any]:
        """Parse JSON or SSE response into a parsed JSON-RPC dict."""
        content_type = resp.headers.get("Content-Type", "")
        raw: str

        if "application/json" in content_type:
            raw = resp.text
        elif "text/event-stream" in content_type or "text/plain" in content_type:
            # SSE: collect all "data:" lines and concatenate
            raw = ""
            for line in resp.text.splitlines():
                line = line.strip()
                if line.startswith("data:"):
                    raw += line[5:].strip()
        else:
            raw = resp.text  # fallback

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BackendError(
                f"Firecrawl MCP response JSON parse error: {exc}",
                provider="firecrawl",
                url=_MCP_ENDPOINT,
            ) from exc

        # JSON-RPC error envelope
        if isinstance(parsed, dict) and "error" in parsed:
            err = parsed["error"]
            raise BackendError(
                f"Firecrawl MCP JSON-RPC error: {err.get('message', err)}",
                provider="firecrawl",
                url=_MCP_ENDPOINT,
            )

        return parsed  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # MCP handshake
    # ------------------------------------------------------------------
    def initialize(self) -> None:
        if self._initialized:
            return

        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "ai-ppt-firecrawl-wt",
                    "version": "0.1.0",
                },
            },
        }
        resp = self._request(payload)
        parsed = self._parse_response(resp)

        # Validate negotiated version
        negotiated = parsed.get("result", {}).get("protocolVersion", "")
        if negotiated not in _SUPPORTED_VERSIONS:
            # Fall back gracefully (server may accept 2025-03-26)
            if negotiated and negotiated not in _SUPPORTED_VERSIONS:
                pass  # server-accepted it; proceed

        # Send notifications/initialized (no id, fire-and-forget)
        notify_payload = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
        self._request(notify_payload)  # errors already raised above

        self._initialized = True

    # ------------------------------------------------------------------
    # Tool calls
    # ------------------------------------------------------------------
    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call an MCP tool and return the parsed result dict."""
        if not self._initialized:
            self.initialize()

        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments,
            },
        }
        resp = self._request(payload)
        parsed = self._parse_response(resp)

        result: dict[str, Any] = parsed.get("result", {})
        if result.get("isError"):
            raise BackendError(
                f"Firecrawl tool '{name}' returned error",
                provider="firecrawl",
                url=_MCP_ENDPOINT,
            )
        return result

    def close(self) -> None:
        self._client.close()


# ----------------------------------------------------------------------
# FirecrawlBackend
# ----------------------------------------------------------------------
def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


class FirecrawlBackend:
    name: str = "firecrawl"

    def __init__(self, client: FirecrawlMcpClient | None = None, max_calls: int | None = None) -> None:
        self._client = client
        self._max_calls = max_calls
        self._calls = 0
        self._quota_exhausted = False

    def _spend(self, url: str = "") -> None:
        """Quota gate: per-instance call cap + 429 circuit breaker (fail-open upstream)."""
        if self._quota_exhausted:
            raise BackendError("Firecrawl quota exhausted (circuit open)", provider="firecrawl", url=url, status_code=429)
        if self._max_calls is not None and self._calls >= self._max_calls:
            self._quota_exhausted = True
            raise BackendError("Firecrawl quota exhausted (per-run call cap)", provider="firecrawl", url=url, status_code=429)
        self._calls += 1

    def supports(self, lang: str) -> bool:
        return True

    def search(self, query: str, lang: str, limit: int) -> list[SearchHit]:
        if not query.strip():
            return []

        self._spend()
        client = self._client or FirecrawlMcpClient()
        try:
            # Build a short English objective sentence derived from the query
            objective = f"Find up to {limit} web resources relevant to: {query}"

            result = client.call_tool(
                "firecrawl_search",
                arguments={
                    "query": query,
                    "objective": objective,
                    "limit": limit,
                },
            )
        except BackendError as e:
            if e.status_code == 429:
                self._quota_exhausted = True
            raise
        except Exception as exc:
            raise BackendError(
                str(exc),
                provider="firecrawl",
                url=_MCP_ENDPOINT,
            ) from exc
        finally:
            if self._client is None:
                client.close()

        # Parse result.content[0].text (JSON string containing {data: {web: [...]}})
        hits: list[SearchHit] = []
        try:
            content = result.get("content", [])
            if not content:
                return hits
            text_blob = content[0].get("text", "{}")
            parsed = json.loads(text_blob)
            web_results: list[dict[str, Any]] = (
                parsed.get("data", {}).get("web", [])
                if isinstance(parsed, dict)
                else []
            )
            for item in web_results:
                hits.append(
                    SearchHit(
                        title=item.get("title", ""),
                        text=item.get("description", ""),
                        url=item.get("url", ""),
                        kind="web",
                        source_name="firecrawl",
                        license_or_terms="",
                        retrieved_at=_now(),
                    )
                )
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise BackendError(
                f"Failed to parse Firecrawl search response: {exc}",
                provider="firecrawl",
                url=_MCP_ENDPOINT,
            ) from exc

        return hits

    def scrape(self, url: str, objective: str) -> str:
        """Scrape a URL and return its markdown content as a plain string."""
        self._spend(url)
        client = self._client or FirecrawlMcpClient()
        try:
            result = client.call_tool(
                "firecrawl_scrape",
                arguments={
                    "url": url,
                    "objective": objective,
                },
            )
        except BackendError as e:
            if e.status_code == 429:
                self._quota_exhausted = True
            raise
        except Exception as exc:
            raise BackendError(
                str(exc),
                provider="firecrawl",
                url=_MCP_ENDPOINT,
            ) from exc
        finally:
            if self._client is None:
                client.close()

        # Parse markdown from result.content[0].text (JSON string with .markdown)
        try:
            content = result.get("content", [])
            if not content:
                return ""
            text_blob = content[0].get("text", "{}")
            parsed = json.loads(text_blob)
            markdown: str = (
                parsed.get("markdown", "")
                if isinstance(parsed, dict)
                else ""
            )
            return markdown
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise BackendError(
                f"Failed to parse Firecrawl scrape response: {exc}",
                provider="firecrawl",
                url=url,
            ) from exc
