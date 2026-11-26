import json
import httpx
import pytest
from pptv01.retrieval.backends import BackendError
from pptv01.retrieval.firecrawl_backend import FirecrawlBackend, FirecrawlMcpClient
from pptv01.d_retrieve.stage import services_from_env
from pptv01.retrieval.provider import research_from_env

SEARCH_TEXT = json.dumps({"success": True, "data": {"web": [
    {"url": "https://example.com/a", "title": "Alpha", "description": "First hit"},
    {"url": "https://example.com/b", "title": "Beta", "description": "Second hit"}]}})
SCRAPE_TEXT = json.dumps({"markdown": "# Hello\n\nBody text."})

def _rpc(result, rid=2):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def make_backend(mode="json", tool_status=200, tool_body=None):
    def handler(request):
        body = json.loads(request.content)
        method = body.get("method", "")
        rid = body.get("id", 2)

        if method == "initialize":
            return httpx.Response(200, json=_rpc({"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}}, 1))

        if method == "notifications/initialized":
            return httpx.Response(200, json={})

        if method == "tools/call":
            tool_name = body.get("params", {}).get("name", "")
            if tool_status != 200:
                return httpx.Response(tool_status, json=tool_body if tool_body is not None else {})
            if tool_name == "firecrawl_search":
                content = SEARCH_TEXT
            else:
                content = SCRAPE_TEXT
            result_payload = _rpc({"content": [{"type": "text", "text": content}]})
            if tool_body is not None and "error" in tool_body:
                return httpx.Response(200, json=tool_body)
            if mode == "sse":
                return httpx.Response(
                    200,
                    text="data: " + json.dumps(result_payload) + "\n\n",
                    headers={"Content-Type": "text/event-stream"}
                )
            return httpx.Response(200, json=result_payload)

        return httpx.Response(200, json={})

    client = FirecrawlMcpClient(transport=httpx.Client(transport=httpx.MockTransport(handler)))
    return FirecrawlBackend(client=client)


def test_search_parses_hits_json():
    backend = make_backend()
    hits = backend.search("q", "en", 5)
    assert len(hits) == 2
    assert hits[0].title == "Alpha"
    assert hits[0].url == "https://example.com/a"
    assert hits[0].text == "First hit"
    assert hits[0].kind == "web"
    assert hits[0].source_name == "firecrawl"


def test_search_parses_hits_sse():
    backend = make_backend(mode="sse")
    hits = backend.search("q", "en", 5)
    assert len(hits) == 2
    assert hits[0].title == "Alpha"


def test_scrape_returns_markdown():
    backend = make_backend()
    result = backend.scrape("https://example.com/a", "obj")
    assert result == "# Hello\n\nBody text."


def test_http_429_raises_quota():
    backend = make_backend(tool_status=429, tool_body={})
    with pytest.raises(BackendError) as exc_info:
        backend.search("q", "en", 5)
    assert exc_info.value.status_code == 429
    assert "quota" in str(exc_info.value).lower()


def test_jsonrpc_error_raises():
    error_payload = {"jsonrpc": "2.0", "id": 2, "error": {"code": -32000, "message": "boom"}}
    backend = make_backend(tool_status=200, tool_body=error_payload)
    with pytest.raises(BackendError) as exc_info:
        backend.search("q", "en", 5)
    assert "boom" in str(exc_info.value).lower()


class _StubClient:
    def __init__(self):
        self.calls = 0

    def call_tool(self, name, **kwargs):
        self.calls += 1
        return {"content": [{"type": "text", "text": SEARCH_TEXT}]}


def test_quota_cap_and_latch():
    stub = _StubClient()
    backend = FirecrawlBackend(client=stub, max_calls=1)
    hits = backend.search("q", "en", 5)
    assert len(hits) == 2
    with pytest.raises(BackendError) as exc_info:
        backend.search("q", "en", 5)
    assert exc_info.value.status_code == 429
    assert stub.calls == 1


def test_stage_services_gate():
    assert services_from_env({}).firecrawl is None
    assert services_from_env({"PPTV01_FIRECRAWL": "1"}).firecrawl is not None


def test_research_gate():
    backends = research_from_env({}).backends
    names = [b.name for b in backends]
    assert "firecrawl" not in names
    backends = research_from_env({"PPTV01_FIRECRAWL": "1"}).backends
    names = [b.name for b in backends]
    assert "firecrawl" in names
