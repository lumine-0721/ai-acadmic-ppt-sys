import json

import httpx
import pytest

from conftest import GOOD_A, GOOD_B
from fakes import FakeClient, FakeResearch
from pptv01.a_plan import build_plan
from pptv01.b_slide_plan import ResearchFailed, SlidePlanRuleError, run_stage_b
from pptv01.b_slide_plan.stage import build_slide_plan
from pptv01.contracts import QueryStatus, ResearchRecord, ResearchStatus, load_contract
from pptv01.llm import LLMConfigError
from pptv01.retrieval import (
    CrossrefBackend, DisabledResearch, LiveResearch, SearxngBackend, WikipediaBackend, research_from_env,
)
from pptv01.retrieval.backends import BackendError, make_http

U = "帮我做一个城市热岛效应的 PPT。"
PLAN = build_plan(GOOD_A, run_id="r1", utterance=U)

WIKI = {"query": {"pages": [
    {"pageid": 2, "title": "热岛", "index": 2, "fullurl": "https://zh.wikipedia.org/wiki/B", "extract": "第二条。"},
    {"pageid": 1, "title": "城市热岛", "index": 1, "fullurl": "https://zh.wikipedia.org/wiki/A",
     "extract": "城市热岛是指城区气温\n高于周边郊区的现象。"}]}}
CROSSREF = {"message": {"items": [
    {"DOI": "10.1/xyz", "title": ["Urban heat island review"], "container-title": ["J. Climate"],
     "issued": {"date-parts": [[2015]]}, "URL": "https://doi.org/10.1/xyz"},
    {"title": ["no doi → skipped"]}]}}


def transport(handler):
    return httpx.MockTransport(handler)


def routing(request: httpx.Request):
    if "wikipedia.org" in request.url.host:
        return httpx.Response(200, json=WIKI)
    if "crossref.org" in request.url.host:
        return httpx.Response(200, json=CROSSREF)
    return httpx.Response(404)


def live(handler=routing, **kw):
    http = make_http(5, "test@example.org", transport(handler))
    return LiveResearch([WikipediaBackend(http), CrossrefBackend(http, "test@example.org")], **kw)


def test_wikipedia_backend_parses_and_orders_by_search_rank():
    http = make_http(5, "x", transport(routing))
    hits = WikipediaBackend(http).search("城市热岛", "zh", 3)
    assert [h.title for h in hits] == ["城市热岛", "热岛"]
    assert hits[0].url == "https://zh.wikipedia.org/wiki/A" and "\n" not in hits[0].text
    assert hits[0].retrieved_at.tzinfo is not None and "CC BY-SA" in hits[0].license_or_terms


def test_crossref_backend_keeps_only_records_with_doi_and_is_english_only():
    http = make_http(5, "x", transport(routing))
    be = CrossrefBackend(http)
    assert be.supports("en") and not be.supports("zh")
    hits = be.search("uhi", "en", 3)
    assert len(hits) == 1 and "DOI 10.1/xyz" in hits[0].text and hits[0].kind == "reference"


def test_backend_errors_are_raised_not_swallowed():
    http = make_http(5, "x", transport(lambda r: httpx.Response(503)))
    with pytest.raises(BackendError, match="503"):
        WikipediaBackend(http).search("q", "zh", 1)

    def boom(r):
        raise httpx.ConnectError("no route")
    with pytest.raises(BackendError, match="ConnectError"):
        WikipediaBackend(make_http(5, "x", transport(boom))).search("q", "zh", 1)


def test_live_research_records_sources_from_brief_queries():
    rec = live().research(PLAN)
    assert rec.status is ResearchStatus.ok and rec.run_id == "r1"
    # brief has a zh and an en query -> wikipedia answers both, crossref only the en one
    assert [(q.backend, q.lang) for q in rec.queries] == [("wikipedia", "zh"), ("wikipedia", "en"), ("crossref", "en")]
    assert [n.note_id for n in rec.notes] == ["R001", "R002", "R003"]  # urls deduped across queries
    n = rec.notes[0]
    assert n.source_url.startswith("https://") and n.title == "城市热岛" and n.retrieved_at and n.query == "城市热岛"
    assert {x.query for x in rec.queries} == {"城市热岛", "urban heat island intensity"}


def test_all_queries_failing_is_recorded_as_failed_not_faked():
    rec = live(lambda r: httpx.Response(503)).research(PLAN)
    assert rec.status is ResearchStatus.failed and rec.notes == []
    assert all(q.status is QueryStatus.error and q.error for q in rec.queries)


def test_partial_and_no_results():
    def only_wiki(r):
        return routing(r) if "wikipedia" in r.url.host else httpx.Response(500)
    assert live(only_wiki).research(PLAN).status is ResearchStatus.partial
    empty = lambda r: httpx.Response(200, json={"query": {"pages": []}, "message": {"items": []}})
    rec = live(empty).research(PLAN)
    assert rec.status is ResearchStatus.no_results and rec.queries and not rec.notes


def test_max_queries_cap():
    assert len(live(max_queries=1).research(PLAN).queries) == 1 + 0  # zh query -> wikipedia only


def test_disabled_research_record_is_honest():
    rec = DisabledResearch("PPTV01_RESEARCH=none").research(PLAN)
    assert rec.status is ResearchStatus.disabled and rec.notes == [] and rec.queries == []


def test_searxng_backend():
    def h(r):
        assert r.url.params["format"] == "json"
        return httpx.Response(200, json={"results": [{"title": "T", "url": "https://a.org/x", "content": "c"}]})
    hits = SearxngBackend(make_http(5, "x", transport(h)), "https://sx.example").search("q", "zh", 3)
    assert hits[0].kind == "web" and hits[0].url == "https://a.org/x"


def test_research_from_env_modes():
    assert isinstance(research_from_env({"PPTV01_RESEARCH": "none"}), DisabledResearch)
    auto = research_from_env({})
    assert [b.name for b in auto.backends] == ["wikipedia", "crossref"]
    assert [b.name for b in research_from_env({"PPTV01_RESEARCH": "wikipedia"}).backends] == ["wikipedia"]
    with pytest.raises(LLMConfigError, match="SEARXNG_URL"):
        research_from_env({"PPTV01_RESEARCH": "searxng"})
    with pytest.raises(LLMConfigError, match="unknown research backend"):
        research_from_env({"PPTV01_RESEARCH": "google"})
    sx = research_from_env({"PPTV01_RESEARCH": "wikipedia,searxng", "PPTV01_RESEARCH_SEARXNG_URL": "https://sx"})
    assert [b.name for b in sx.backends] == ["wikipedia", "searxng"]


# ---- A: retrieval brief rules
def test_a_requires_retrieval_brief_with_neutral_queries():
    d = {k: v for k, v in GOOD_A.items() if k != "retrieval_brief"}
    with pytest.raises(ValueError):
        build_plan(d, run_id="r", utterance=U)
    bad = {**GOOD_A, "retrieval_brief": [{"topic": "t", "why": "w", "queries": [{"text": "热岛使死亡率上升23%", "lang": "zh"}]}]}
    with pytest.raises(ValueError, match="neutral question"):
        build_plan(bad, run_id="r", utterance=U)
    dup = {**GOOD_A, "retrieval_brief": [{"topic": "t", "why": "w", "queries": [{"text": "a", "lang": "zh"}, {"text": "A", "lang": "zh"}]}]}
    with pytest.raises(ValueError, match="duplicate"):
        build_plan(dup, run_id="r", utterance=U)


# ---- B with research notes
NOTES = [("城市热岛", "城市热岛是指城区气温高于周边郊区的现象。某城区比郊区高 3 摄氏度。"), ("Urban heat island review", "J. Climate; DOI 10.1/xyz")]


def with_refs(b_raw=None, page_refs=("R001",), target=None):
    import copy
    d = copy.deepcopy(b_raw or GOOD_B)
    d["pages"][0]["source_refs"] = list(page_refs)
    if target:
        d["pages"][0]["asset_needs"][0]["targets"][0].update(target)
    return d


def notes_map():
    rec = FakeResearch(NOTES).research(PLAN)
    return {n.note_id: n for n in rec.notes}


def test_b_accepts_real_note_refs_and_writes_research_record(tmp_path):
    plan, _, rec = run_stage_b(PLAN, FakeClient([with_refs()]), tmp_path, FakeResearch(NOTES))
    assert plan.pages[0].source_refs == ["R001"]
    saved = load_contract(ResearchRecord, tmp_path / "research_brief.json")
    assert saved.provider.startswith("FakeResearch") and len(saved.notes) == 2     # fake is labelled as fake
    assert (tmp_path / "slide_plan.json").is_file()


def test_b_rejects_refs_to_notes_that_do_not_exist():
    with pytest.raises(SlidePlanRuleError, match="no such research notes"):
        build_slide_plan(with_refs(page_refs=("R009",)), PLAN, notes_map())
    with pytest.raises(SlidePlanRuleError, match="no research was performed"):
        build_slide_plan(with_refs(page_refs=("R001",)), PLAN, {})


def test_b_numbers_allowed_only_if_cited_note_contains_them():
    d = with_refs()
    d["pages"][0]["key_points"] = ["某城区比郊区高 3 摄氏度"]
    build_slide_plan(d, PLAN, notes_map())                       # cited R001 literally says "3 摄氏度"
    d2 = with_refs(page_refs=("R002",)); d2["pages"][0]["key_points"] = ["某城区比郊区高 3 摄氏度"]
    with pytest.raises(SlidePlanRuleError, match="unsourced"):
        build_slide_plan(d2, PLAN, notes_map())                  # cites a note that doesn't contain it
    d3 = with_refs(page_refs=()); d3["pages"][0]["key_points"] = ["某城区比郊区高 3 摄氏度"]
    with pytest.raises(SlidePlanRuleError, match="unsourced"):
        build_slide_plan(d3, PLAN, notes_map())                  # cites nothing
    d4 = with_refs(); d4["pages"][0]["key_points"] = ["研究表明城区更热"]
    with pytest.raises(SlidePlanRuleError, match="conclusion-style"):
        build_slide_plan(d4, PLAN, notes_map())                  # conclusion phrases are never exempt


def test_b_verified_target_must_appear_in_cited_note():
    ok = with_refs(target={"name": "城市热岛", "verification": "verified", "source_refs": ["R001"]})
    build_slide_plan(ok, PLAN, notes_map())
    bad = with_refs(target={"name": "某不存在的城市", "verification": "verified", "source_refs": ["R001"]})
    with pytest.raises(SlidePlanRuleError, match="does not appear in the cited"):
        build_slide_plan(bad, PLAN, notes_map())
    ghost = with_refs(target={"name": "城市热岛", "verification": "verified", "source_refs": ["R042"]})
    with pytest.raises(SlidePlanRuleError, match="not retrieved notes"):
        build_slide_plan(ghost, PLAN, notes_map())


def test_b_fails_honestly_when_research_failed(tmp_path):
    failed = live(lambda r: httpx.Response(503))
    client = FakeClient([GOOD_B])
    with pytest.raises(ResearchFailed, match="PPTV01_RESEARCH=none"):
        run_stage_b(PLAN, client, tmp_path, failed)
    assert client.calls == []                                     # the LLM was never asked to make things up
    rec = load_contract(ResearchRecord, tmp_path / "research_brief.json")
    assert rec.status is ResearchStatus.failed
    assert not (tmp_path / "slide_plan.json").exists()


def test_b_prompt_lists_retrieved_notes():
    c = FakeClient([with_refs()])
    run_stage_b(PLAN, c, research=FakeResearch(NOTES))
    body = c.calls[0][-1]["content"]
    assert "[R001] 城市热岛" in body and "[R002] Urban heat island review" in body


def test_resume_reuses_saved_research_instead_of_requerying(tmp_path):
    class Exploding:
        def research(self, plan):
            raise AssertionError("must not re-query on resume")
    run_stage_b(PLAN, FakeClient([with_refs()]), tmp_path, FakeResearch(NOTES))
    plan, _, rec = run_stage_b(PLAN, FakeClient([with_refs()]), tmp_path, Exploding(), reuse_research=True)
    assert len(rec.notes) == 2
    # a *failed* saved record is not reused: retry the network
    failed = live(lambda r: httpx.Response(503))
    with pytest.raises(ResearchFailed):
        run_stage_b(PLAN, FakeClient([GOOD_B]), tmp_path, failed)
    plan, _, rec = run_stage_b(PLAN, FakeClient([with_refs()]), tmp_path, FakeResearch(NOTES), reuse_research=True)
    assert rec.status is ResearchStatus.ok


def test_transient_errors_are_retried_then_succeed():
    calls = {"n": 0}

    def flaky(r):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        if calls["n"] == 2:
            raise httpx.ConnectTimeout("handshake timed out")
        return httpx.Response(200, json=CROSSREF)
    hits = CrossrefBackend(make_http(5, "x", transport(flaky))).search("uhi", "en", 3)
    assert calls["n"] == 3 and len(hits) == 1


def test_persistent_429_fails_after_bounded_retries_and_404_is_not_retried():
    n = {"c": 0}

    def always429(r):
        n["c"] += 1
        return httpx.Response(429)
    with pytest.raises(BackendError, match=r"429.*3 attempts"):
        CrossrefBackend(make_http(5, "x", transport(always429))).search("q", "en", 1)
    assert n["c"] == 3
    n["c"] = 0

    def nf(r):
        n["c"] += 1
        return httpx.Response(404)
    with pytest.raises(BackendError, match="404"):
        WikipediaBackend(make_http(5, "x", transport(nf))).search("q", "zh", 1)
    assert n["c"] == 1
