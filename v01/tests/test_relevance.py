"""Relevance filter: structural rules, scorer thresholding (fake scorer), replay of REAL smoke notes, record shape."""
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from pptv01.contracts import FilterReason, ProductionPlan, ResearchRecord
from pptv01.llm import LLMConfigError
from pptv01.retrieval.backends import SearchHit
from pptv01.retrieval.provider import LiveResearch
from pptv01.retrieval.relevance import (Candidate, RelevanceFilter, ScorerUnavailable, filter_from_env,
                                        structural_check)
from tests.conftest import GOOD_A

REPLAY = Path(__file__).resolve().parents[1] / "benchmark" / "relevance"
LABELS = json.loads((REPLAY / "smoke-agent-v2.labels.json").read_text("utf-8"))


def cand(title, text="x" * 200, kind="encyclopedia", doc_type="", topic="t", query="q"):
    return Candidate(title, text, kind, "wikipedia", topic, query, doc_type)


@pytest.mark.parametrize("title", [
    "INTRODUCTION:", "Chapter Sixteen", "Chapter 7", "Inhalt", "[Illustration]", "In Memoriam", "Preface",
    "Figure 3: MediaPipe Hand Landmark detection architecture.", "Table 2. Results", "7. Contents", "目录", "第三章",
    "Table of Contents", "Acknowledgements", "References", "Appendix B"])
def test_structural_junk_titles_are_dropped(title):
    v = structural_check(cand(title, kind="reference"))
    assert not v.keep and v.reason == "junk_title"


@pytest.mark.parametrize("title", [
    "Gothic architecture", "Chapter and Verse: a study of citation", "Introduction to Urban Climatology",
    "Index fossils of the Jurassic", "Table Mountain", "Figure skating", "Notes on the Gothic Cathedral",
    "Inhalt und Form der Architektur", "In Memoriam: Alfred Lord Tennyson's poem", "图灵机", "表观遗传学", "引力波",
    "GOTHIC FORM", "COFFEEHOUSE", "Parthenon"])
def test_structural_does_not_eat_real_topics(title):
    assert structural_check(cand(title, kind="reference")).keep


def test_structural_other_rules():
    assert structural_check(cand("海绵宝宝角色列表", "短" * 33)).reason in ("list_page", "too_short")
    assert structural_check(cand("Foo", "Foo may refer to: a; b; c" + "x" * 80)).reason == "disambiguation"
    assert structural_check(cand("List of tallest buildings", "x" * 200)).reason == "list_page"
    assert structural_check(cand("韩国的咖啡", "在韩国，咖啡一直是韩国文化的重要元素。" * 1)).reason == "too_short"
    v = structural_check(cand("Fig", "x", kind="reference", doc_type="component"))
    assert not v.keep
    assert structural_check(cand("Real paper title here", "x", kind="reference", doc_type="journal-article")).keep
    # short body is only a defect for encyclopedia extracts; Crossref text is a citation line by design
    assert structural_check(cand("Water Bodies as Stable Reference", "Water Bodies (2024); DOI 10.1/x", kind="reference")).keep


class FakeScorer:
    name = "fastembed"
    model = "fake"

    def __init__(self, table):
        self.table, self.calls = table, []

    def score(self, query, docs):
        self.calls.append((query, list(docs)))
        return [next((v for k, v in self.table.items() if k in d), 0.9) for d in docs]


def test_scorer_threshold_keeps_and_records_reason():
    sc = FakeScorer({"Israel": 0.05, "borderline": 0.31})
    f = RelevanceFilter(sc, 0.30)
    out = f.apply([cand("Israel", topic="咖啡店"), cand("borderline", topic="咖啡店"), cand("咖啡店", topic="咖啡店")], "咖啡店创业计划")
    assert [c.title for c, _ in out.kept] == ["borderline", "咖啡店"]
    (c, v), = out.dropped
    assert c.title == "Israel" and v.reason == "low_relevance" and v.score == 0.05 and "0.3" in v.detail
    assert out.scorer == "fastembed" and out.threshold == 0.30
    assert sc.calls[0][0] == "咖啡店创业计划 咖啡店"        # query = plan topic + retrieval sub-topic


def test_structural_runs_before_scorer_and_scorer_sees_only_survivors():
    sc = FakeScorer({})
    out = RelevanceFilter(sc, 0.3).apply([cand("INTRODUCTION:", kind="reference"), cand("Coffee")], "咖啡")
    assert len(sc.calls[0][1]) == 1 and out.dropped[0][1].reason == "junk_title"


def test_structural_only_mode_and_off_mode():
    out = RelevanceFilter(None, None, mode="structural").apply([cand("Inhalt", kind="reference"), cand("Coffee")], "x")
    assert out.scorer is None and len(out.kept) == 1 and len(out.dropped) == 1
    out = RelevanceFilter(None, None, mode="off").apply([cand("Inhalt", kind="reference")], "x")
    assert len(out.kept) == 1 and not out.dropped and "disabled" in out.note


def test_env_modes(monkeypatch):
    assert filter_from_env({}).mode == "auto"           # no fastembed in the unit-test env => structural + honest note
    f = filter_from_env({"PPTV01_RELEVANCE": "structural"})
    assert f.scorer is None and f.mode == "structural"
    assert filter_from_env({"PPTV01_RELEVANCE": "off"}).mode == "off"
    with pytest.raises(LLMConfigError):
        filter_from_env({"PPTV01_RELEVANCE": "maybe"})
    with pytest.raises(LLMConfigError):
        filter_from_env({"PPTV01_RELEVANCE_SCORER": "magic"})
    with pytest.raises(LLMConfigError):
        filter_from_env({"PPTV01_RELEVANCE_THRESHOLD": "abc"})


def test_unavailable_scorer_degrades_to_structural_with_recorded_reason(monkeypatch):
    import pptv01.retrieval.relevance as r

    def boom(*a, **k):
        raise ScorerUnavailable("fastembed is not installed")
    monkeypatch.setattr(r, "FastembedScorer", boom)
    f = r.filter_from_env({})
    assert f.scorer is None and "fastembed is not installed" in f.unavailable


# ---------------------------------------------------------------- replay of the REAL smoke notes (no network, no model)
@pytest.mark.parametrize("task", ["T02", "T12", "T26"])
def test_structural_rules_on_real_smoke_notes_never_drop_a_relevant_note(task):
    d = json.loads((REPLAY / "notes" / f"{task}.json").read_text("utf-8"))
    noise = set(LABELS[task]["noise"])
    dropped = []
    for n in d["notes"]:
        v = structural_check(Candidate(n["title"], n["text"], n["kind"], n["backend"], "t", n["query"]))
        if not v.keep:
            dropped.append(n["note_id"])
            assert n["note_id"] in noise, f"{task} {n['note_id']} {n['title']!r} is labelled relevant but was dropped ({v.reason})"
    assert dropped        # the rules do remove something on every sampled task


def test_structural_catches_known_real_junk():
    want = {"T12": {"INTRODUCTION:"}, "T26": {"Chapter Sixteen", "Inhalt", "[Illustration]", "In Memoriam"},
            "T02": {"海绵宝宝角色列表"}}
    for task, titles in want.items():
        d = json.loads((REPLAY / "notes" / f"{task}.json").read_text("utf-8"))
        got = {n["title"] for n in d["notes"] if not structural_check(
            Candidate(n["title"], n["text"], n["kind"], n["backend"], "t", n["query"])).keep}
        assert titles <= got


# ---------------------------------------------------------------- provider integration: record shape
def _hit(title, url, text="y" * 100, kind="encyclopedia", doc_type=""):
    return SearchHit(title, text, url, kind, "S", "lic", datetime.now(timezone.utc).astimezone(), doc_type)


class StubBackend:
    name = "wikipedia"

    def __init__(self, hits):
        self.hits = hits

    def supports(self, lang):
        return True

    def search(self, query, lang, limit):
        return self.hits


def test_live_research_records_kept_and_filtered_with_reasons_and_contiguous_ids():
    plan = ProductionPlan(run_id="20260101-000000-abcd", source_utterance="u", **GOOD_A)
    hits = [_hit("INTRODUCTION:", "https://x/1", kind="reference"), _hit("Coffee", "https://x/2"),
            _hit("Israel", "https://x/3"), _hit("Cafe", "https://x/4")]
    lr = LiveResearch([StubBackend(hits)], relevance=RelevanceFilter(FakeScorer({"Israel": 0.0}), 0.3), max_queries=1)
    rec = lr.research(plan)
    ResearchRecord.model_validate(json.loads(rec.model_dump_json()))        # round-trips
    assert [n.note_id for n in rec.notes] == ["R001", "R002"] and [n.title for n in rec.notes] == ["Coffee", "Cafe"]
    assert all(n.relevance == 0.9 for n in rec.notes)
    assert {(f.title, f.reason) for f in rec.filtered} == {("INTRODUCTION:", FilterReason.junk_title),
                                                           ("Israel", FilterReason.low_relevance)}
    rf = rec.relevance_filter
    assert (rf.n_candidates, rf.n_kept, rf.n_filtered, rf.scorer, rf.threshold) == (4, 2, 2, "fastembed", 0.3)
    assert "2 hits filtered" in rec.summary


def test_everything_filtered_is_no_results_not_ok():
    plan = ProductionPlan(run_id="20260101-000000-abcd", source_utterance="u", **GOOD_A)
    lr = LiveResearch([StubBackend([_hit("Inhalt", "https://x/1", kind="reference")])],
                      relevance=RelevanceFilter(None, None, mode="structural"), max_queries=1)
    rec = lr.research(plan)
    assert rec.status.value == "no_results" and not rec.notes and rec.filtered


def test_record_validator_rejects_inconsistent_counts():
    plan = ProductionPlan(run_id="20260101-000000-abcd", source_utterance="u", **GOOD_A)
    rec = LiveResearch([StubBackend([_hit("Coffee", "https://x/2")])], max_queries=1).research(plan)
    d = json.loads(rec.model_dump_json())
    d["relevance_filter"]["n_kept"] = 5
    with pytest.raises(ValueError):
        ResearchRecord.model_validate(d)
