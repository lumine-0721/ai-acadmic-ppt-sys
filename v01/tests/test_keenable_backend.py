"""Tests for KeenableBackend and its integration with LiveResearch."""
from __future__ import annotations

import json

import httpx
import pytest

from conftest import GOOD_A
from pptv01.a_plan import build_plan
from pptv01.contracts import QueryStatus
from pptv01.retrieval import LiveResearch, WikipediaBackend, research_from_env
from pptv01.retrieval.backends import BackendError, KeenableBackend, make_http

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PLAN = build_plan(GOOD_A, run_id="r1", utterance="帮我做一个城市热岛效应的 PPT。")


def transport(handler):
    return httpx.MockTransport(handler)


def mk_http(handler):
    return make_http(5, "x", transport(handler))


# ---------------------------------------------------------------------------
# 1. test_search_parses_results
# ---------------------------------------------------------------------------

def test_search_parses_results():
    results = [
        {"title": "城市热岛效应研究", "url": "https://example.com/1", "snippet": "城区气温比郊区高3°C。"},
        {"title": "Urban Heat Island", "url": "https://example.com/2", "description": "Urban areas are warmer."},
        # missing url → should be skipped
        {"title": "No URL entry", "url": "", "snippet": "Should be skipped."},
    ]

    def handler(r):
        assert r.url.path == "/v1/search"
        return httpx.Response(200, json={"results": results})

    be = KeenableBackend(mk_http(handler), "test-key")
    hits = be.search("城市热岛", "zh", 5)

    assert len(hits) == 2
    assert hits[0].title == "城市热岛效应研究"
    assert hits[0].text == "城区气温比郊区高3°C。"
    assert hits[0].url == "https://example.com/1"
    assert hits[0].kind == "web"
    assert hits[0].source_name == "example.com"

    assert hits[1].title == "Urban Heat Island"
    assert hits[1].text == "Urban areas are warmer."


# ---------------------------------------------------------------------------
# 2. test_search_sends_api_key_header_and_body
# ---------------------------------------------------------------------------

def test_search_sends_api_key_header_and_body(monkeypatch):
    import pptv01.retrieval.backends as b
    monkeypatch.setattr(b, "_sleep", lambda s: None)

    captured = {}

    def handler(r):
        captured["headers"] = dict(r.headers)
        captured["body"] = json.loads(r.content)
        return httpx.Response(200, json={"results": [{"title": "X", "url": "https://x.com/1", "snippet": "x"}]})

    be = KeenableBackend(mk_http(handler), "test-key")
    be.search("热岛", "zh", 4)

    assert captured["headers"].get("x-api-key") == "test-key"
    assert captured["body"]["query"] == "热岛"
    assert captured["body"]["max_results"] == 4


# ---------------------------------------------------------------------------
# 3. test_search_raises_backend_error_on_non_2xx (422 and 500 with monkeypatch)
# ---------------------------------------------------------------------------

def test_search_raises_backend_error_on_422():
    def handler(r):
        return httpx.Response(422, json={"error": "bad request"})

    be = KeenableBackend(mk_http(handler), "k")
    with pytest.raises(BackendError) as exc_info:
        be.search("q", "zh", 1)
    assert "422" in str(exc_info.value)


def test_search_raises_backend_error_on_500(monkeypatch):
    import pptv01.retrieval.backends as b
    monkeypatch.setattr(b, "_sleep", lambda s: None)

    def handler(r):
        return httpx.Response(500, json={"error": "server error"})

    be = KeenableBackend(mk_http(handler), "k")
    with pytest.raises(BackendError) as exc_info:
        be.search("q", "zh", 1)
    assert "500" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 4. test_search_raises_backend_error_on_network_failure
# ---------------------------------------------------------------------------

def test_search_raises_backend_error_on_network_failure():
    def boom(r):
        raise httpx.ConnectError("Connection refused")

    be = KeenableBackend(mk_http(boom), "k")
    with pytest.raises(BackendError) as exc_info:
        be.search("q", "zh", 1)
    assert "ConnectError" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 5. test_search_malformed_response
# ---------------------------------------------------------------------------

def test_search_malformed_response():
    # results is a dict, not a list
    def handler(r):
        return httpx.Response(200, json={"results": {"foo": "bar"}})

    be = KeenableBackend(mk_http(handler), "k")
    with pytest.raises(BackendError) as exc_info:
        be.search("q", "zh", 1)
    assert "malformed" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 6. test_fetch_body_shape_urls_and_prompt
# ---------------------------------------------------------------------------

def test_fetch_body_shape_urls_and_prompt():
    captured = {}

    def handler(r):
        captured["body"] = json.loads(r.content)
        captured["headers"] = dict(r.headers)
        return httpx.Response(200, json={
            "results": [{"url": "https://a.com", "content": "Page content here."}]
        })

    be = KeenableBackend(mk_http(handler), "test-key")

    # without prompt
    be.fetch(["https://a.com", "https://b.com"], prompt="")
    assert captured["body"]["urls"] == ["https://a.com", "https://b.com"]
    assert "content" not in captured["body"]

    # with prompt
    be.fetch(["https://a.com"], prompt="Summarize this article")
    assert captured["body"]["urls"] == ["https://a.com"]
    assert captured["body"]["content"] == {"prompt": "Summarize this article"}
    assert captured["headers"].get("x-api-key") == "test-key"


# ---------------------------------------------------------------------------
# 7. test_fetch_raises_backend_error_on_failure
# ---------------------------------------------------------------------------

def test_fetch_raises_backend_error_on_failure():
    def handler(r):
        return httpx.Response(500, json={"error": "server error"})

    be = KeenableBackend(mk_http(handler), "k")
    with pytest.raises(BackendError) as exc_info:
        be.fetch(["https://a.com"])
    assert "500" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 8. test_research_from_env_appends_when_flag_and_key
# ---------------------------------------------------------------------------

def test_research_from_env_appends_when_flag_and_key(monkeypatch):
    import pptv01.retrieval.backends as b
    monkeypatch.setattr(b, "_sleep", lambda s: None)

    env = {
        "PPTV01_KEENABLE": "1",
        "KEENABLE_API_KEY": "my-secret-key",
    }

    def handler(r):
        return httpx.Response(200, json={"results": []})

    http = make_http(5, "x", transport(handler))
    provider = research_from_env(env, transport=transport(handler))
    assert isinstance(provider, LiveResearch)
    backend_names = [be.name for be in provider.backends]
    # default backends + keenable appended at end
    assert "keenable" in backend_names
    assert backend_names[-1] == "keenable"


def test_research_from_env_skips_without_flag_or_key(monkeypatch):
    import pptv01.retrieval.backends as b
    monkeypatch.setattr(b, "_sleep", lambda s: None)

    env_no_flag = {"KEENABLE_API_KEY": "key"}
    env_no_key = {"PPTV01_KEENABLE": "1"}

    def handler(r):
        return httpx.Response(200, json={"results": []})

    for env in [env_no_flag, env_no_key]:
        provider = research_from_env(env, transport=transport(handler))
        assert isinstance(provider, LiveResearch)
        assert "keenable" not in [be.name for be in provider.backends]


# ---------------------------------------------------------------------------
# 9. test_live_research_falls_back_on_keenable_failure
# ---------------------------------------------------------------------------

def test_live_research_falls_back_on_keenable_failure():
    WIKI_ZH = {
        "query": {
            "pages": [
                {
                    "pageid": 1,
                    "title": "城市热岛",
                    "index": 1,
                    "fullurl": "https://zh.wikipedia.org/wiki/A",
                    "extract": "城市热岛是指城区气温高于周边郊区的现象。",
                }
            ]
        }
    }
    WIKI_EN = {
        "query": {
            "pages": [
                {
                    "pageid": 2,
                    "title": "Urban heat island",
                    "index": 1,
                    "fullurl": "https://en.wikipedia.org/wiki/Urban_heat_island",
                    "extract": "An urban heat island is a metropolitan area that is warmer than its surroundings.",
                }
            ]
        }
    }

    def routing(request: httpx.Request):
        if request.url.host == "api.keenable.ai":
            return httpx.Response(500, json={"error": "Internal Server Error"})
        if request.url.host == "zh.wikipedia.org":
            return httpx.Response(200, json=WIKI_ZH)
        if request.url.host == "en.wikipedia.org":
            return httpx.Response(200, json=WIKI_EN)
        return httpx.Response(404)

    http = make_http(5, "x", transport(routing))
    be_keenable = KeenableBackend(http, "k")
    be_wiki = WikipediaBackend(http)
    live = LiveResearch([be_keenable, be_wiki])
    rec = live.research(PLAN)

    # Wikipedia should have returned notes despite keenable failing
    assert len(rec.notes) > 0
    assert all(n.backend == "wikipedia" for n in rec.notes)

    # keenable queries should be recorded as errors
    keenable_queries = [q for q in rec.queries if q.backend == "keenable"]
    assert len(keenable_queries) > 0
    assert all(q.status is QueryStatus.error for q in keenable_queries)

    # Wikipedia queries should be ok
    wiki_queries = [q for q in rec.queries if q.backend == "wikipedia"]
    assert len(wiki_queries) > 0
    assert all(q.status is QueryStatus.ok for q in wiki_queries)