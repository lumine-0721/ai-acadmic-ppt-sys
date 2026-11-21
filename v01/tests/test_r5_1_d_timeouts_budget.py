"""R5-1 offline tests: Stage D per-call timeouts, OpenAlex httpx fetch, D total budget, fastembed load timeout, progress logs."""

from __future__ import annotations

import builtins
import json
import sys
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image, ImageDraw
from pptx import Presentation

from pptv01.contracts import (
    AssetRequirements,
    AssetStatus,
    ProductionPlan,
    RunOverall,
    SlidePlan,
    StageStatus,
    UserInputRequests,
    load_contract,
)
from pptv01.d_retrieve import run_stage_d, services_from_env
from pptv01.d_retrieve.sources import OpenAlexClient, openalex_http_fetch, pyalex_fetch
from pptv01.retrieval import relevance as rel_mod
from pptv01.retrieval.backends import make_http, sanitize_url
from pptv01.runner import run_pipeline
from tests.conftest import GOOD_A, GOOD_B, GOOD_C
from tests.fakes import FakeClient, FakeResearch

RID = "20261121-000000-r5d1"


def _req(aid: str, typ: str, target_name: str, etype: str = "building", hints: tuple[str, ...] = ("target query",)):
    return {
        "asset_id": aid,
        "page_ids": ["S001"],
        "need_refs": [f"S001#{int(aid[1:])}"],
        "type": typ,
        "purpose": f"获取 {target_name}",
        "targets": [{"name": target_name, "entity_type": etype, "verification": "to_verify"}],
        "query_hints": list(hints),
        "priority": "required",
        "acceptance": ["来源明确"],
    }


def test_r5_1_single_timeout_records_provider_sanitized_url_and_elapsed(tmp_path, monkeypatch):
    """Single slow/ReadTimeout HTTP call returns within bound, strips api_key, and records provider/URL/elapsed."""
    import pptv01.retrieval.backends as bk

    monkeypatch.setattr(bk, "_sleep", lambda _s: None)
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        if request.url.host == "api.openalex.org":
            raise httpx.ReadTimeout("simulated OpenAlex read timeout", request=request)
        if request.url.host == "api.crossref.org":
            raise httpx.ReadTimeout("simulated Crossref read timeout", request=request)
        return httpx.Response(404)

    sv = services_from_env(
        {
            "PPTV01_ASSETS": "auto",
            "PPTV01_RELEVANCE": "structural",
            "PPTV01_PIXEL_RULES": "off",
            "PPTV01_OPENALEX_API_KEY": "SUPER_SECRET_OA_KEY",
            "PPTV01_ASSETS_CONNECT_TIMEOUT_S": "1.0",
            "PPTV01_ASSETS_READ_TIMEOUT_S": "1.5",
            "PPTV01_ASSETS_BUDGET_S": "10.0",
        },
        transport=httpx.MockTransport(handler),
    )
    assert isinstance(sv.http.timeout, httpx.Timeout)
    assert sv.http.timeout.connect == 1.0 and sv.http.timeout.read == 1.5

    plan = ProductionPlan(run_id=RID, source_utterance="测试超时", **GOOD_A)
    sp = SlidePlan(
        run_id=RID,
        status="provisional",
        narrative=GOOD_B["narrative"],
        pages=[{"page_id": f"S{i:03d}", "status": "provisional", **p} for i, p in enumerate(GOOD_B["pages"], 1)],
    )
    rq = AssetRequirements(
        run_id=RID,
        requirements=[_req("A001", "reference", "Urban Heat Island Study", etype="publication", hints=("urban heat island",))],
    )

    t0 = time.monotonic()
    reg = run_stage_d(plan, sp, rq, tmp_path, sv)
    dt = time.monotonic() - t0
    assert dt < 5.0
    assert reg.assets[0].status is AssetStatus.missing
    # OpenAlex request carried key in transit, but logged URLs/errors stripped it completely
    assert any("SUPER_SECRET_OA_KEY" in u for u in seen_urls)
    oa_calls = [c for c in reg.sources if c.backend == "openalex"]
    assert oa_calls and oa_calls[0].status == "error"
    assert oa_calls[0].url and "api.openalex.org/works" in oa_calls[0].url
    assert "SUPER_SECRET_OA_KEY" not in (oa_calls[0].url or "")
    assert "SUPER_SECRET_OA_KEY" not in (oa_calls[0].error or "")
    assert oa_calls[0].elapsed_s is not None and oa_calls[0].attempts == 3


def test_r5_1_blocking_openalex_fetch_is_cut_off_by_watchdog(tmp_path):
    """Permanently blocking OpenAlex fetch (modeling untimeouted proxy hang) is cut off without hanging."""
    stop_ev = threading.Event()
    progress_lines: list[str] = []

    def hanging_oa(query: str, limit: int):
        stop_ev.wait(timeout=30.0)
        return []

    def crossref_dead(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("crossref timeout", request=req)

    sv = services_from_env(
        {
            "PPTV01_ASSETS": "auto",
            "PPTV01_RELEVANCE": "structural",
            "PPTV01_PIXEL_RULES": "off",
            "PPTV01_ASSETS_CONNECT_TIMEOUT_S": "0.15",
            "PPTV01_ASSETS_READ_TIMEOUT_S": "0.15",
            "PPTV01_ASSETS_BUDGET_S": "1.2",
        },
        transport=httpx.MockTransport(crossref_dead),
        openalex_fetch=hanging_oa,
        progress_fn=progress_lines.append,
    )
    plan = ProductionPlan(run_id=RID, source_utterance="测试挂住拦截", **GOOD_A)
    sp = SlidePlan(
        run_id=RID,
        status="provisional",
        narrative=GOOD_B["narrative"],
        pages=[{"page_id": f"S{i:03d}", "status": "provisional", **p} for i, p in enumerate(GOOD_B["pages"], 1)],
    )
    rq = AssetRequirements(
        run_id=RID,
        requirements=[_req("A001", "reference", "Some Paper", etype="publication", hints=("some paper",))],
    )

    t0 = time.monotonic()
    try:
        reg = run_stage_d(plan, sp, rq, tmp_path, sv)
    finally:
        stop_ev.set()
    dt = time.monotonic() - t0
    assert dt < 3.0, f"Stage D took too long on hanging OpenAlex fetch: {dt:.2f}s"
    assert reg.assets[0].status is AssetStatus.missing
    oa_calls = [c for c in reg.sources if c.backend == "openalex"]
    assert oa_calls and oa_calls[0].status == "error" and "timed out" in (oa_calls[0].error or "")
    assert any("api.openalex.org" in (c.url or "") for c in oa_calls)
    assert any("start A001" in ln for ln in progress_lines)
    assert any("done A001" in ln and "missing" in ln for ln in progress_lines)


def test_r5_1_total_budget_caps_many_hanging_requirements_and_emits_placeholders(tmp_path):
    """D total budget (0.6s) stops multi-item hangs cleanly, marks remaining as 'not acquired: D budget exhausted', and emits progress."""
    stop_ev = threading.Event()
    progress_lines: list[str] = []

    def slow_transport(request: httpx.Request) -> httpx.Response:
        stop_ev.wait(timeout=5.0)
        return httpx.Response(200, json={})

    def slow_oa(query: str, limit: int):
        stop_ev.wait(timeout=5.0)
        return []

    sv = services_from_env(
        {
            "PPTV01_ASSETS": "auto",
            "PPTV01_RELEVANCE": "structural",
            "PPTV01_PIXEL_RULES": "off",
            "PPTV01_ASSETS_CONNECT_TIMEOUT_S": "0.15",
            "PPTV01_ASSETS_READ_TIMEOUT_S": "0.15",
            "PPTV01_ASSETS_BUDGET_S": "0.6",
        },
        transport=httpx.MockTransport(slow_transport),
        openalex_fetch=slow_oa,
        progress_fn=progress_lines.append,
    )
    plan = ProductionPlan(run_id=RID, source_utterance="多素材预算测试", **GOOD_A)
    sp = SlidePlan(
        run_id=RID,
        status="provisional",
        narrative=GOOD_B["narrative"],
        pages=[{"page_id": f"S{i:03d}", "status": "provisional", **p} for i, p in enumerate(GOOD_B["pages"], 1)],
    )
    rq = AssetRequirements(
        run_id=RID,
        requirements=[
            _req("A001", "image", "Beijing"),
            _req("A002", "image", "Shanghai"),
            _req("A003", "image", "Paris"),
            _req("A004", "reference", "Climate Study", etype="publication", hints=("climate",)),
            _req("A005", "image", "London"),
        ],
    )

    t0 = time.monotonic()
    try:
        reg = run_stage_d(plan, sp, rq, tmp_path, sv, on_missing="placeholder")
    finally:
        stop_ev.set()
    dt = time.monotonic() - t0

    assert dt < 2.5, f"Stage D exceeded 0.6s budget by too much: {dt:.2f}s"
    assert all(a.status is AssetStatus.missing for a in reg.assets)
    assert any("not acquired: D budget exhausted" in (a.notes or "") for a in reg.assets)
    uir = load_contract(UserInputRequests, tmp_path / "user_input_requests.json")
    assert len(uir.requests) == 5
    for idx in range(1, 6):
        aid = f"A00{idx}"
        assert any(f"start {aid}" in ln for ln in progress_lines)
        assert any(f"done {aid}" in ln and "result=missing" in ln for ln in progress_lines)


def test_r5_1_pipeline_continues_to_complete_with_placeholders_after_d_timeout(tmp_path):
    """End-to-end A->H with D hanging completes cleanly with overall=complete_with_placeholders (never stalls)."""
    stop_ev = threading.Event()

    def slow_transport(request: httpx.Request) -> httpx.Response:
        stop_ev.wait(timeout=5.0)
        return httpx.Response(200, json={})

    def slow_oa(query: str, limit: int):
        stop_ev.wait(timeout=5.0)
        return []

    sv = services_from_env(
        {
            "PPTV01_ASSETS": "auto",
            "PPTV01_RELEVANCE": "structural",
            "PPTV01_PIXEL_RULES": "off",
            "PPTV01_ASSETS_CONNECT_TIMEOUT_S": "0.15",
            "PPTV01_ASSETS_READ_TIMEOUT_S": "0.15",
            "PPTV01_ASSETS_BUDGET_S": "0.5",
        },
        transport=httpx.MockTransport(slow_transport),
        openalex_fetch=slow_oa,
    )
    def _stub_render(pptx_path, out_dir):
        prs = Presentation(str(pptx_path))
        out_dir.mkdir(parents=True, exist_ok=True)
        out = []
        for i in range(1, len(prs.slides) + 1):
            p = out_dir / f"slide_{i:03d}.png"
            im = Image.new("RGB", (640, 360), (245, 248, 252))
            ImageDraw.Draw(im).rectangle([20, 20, 200, 100], fill=(30, 64, 175))
            im.save(p, "PNG")
            out.append(p)
        return out

    try:
        report, run_dir = run_pipeline(
            "帮我介绍一下城市热岛效应。",
            tmp_path,
            FakeClient([GOOD_A, GOOD_B, GOOD_C]),
            run_id="r5-d-timeout-e2e",
            research=FakeResearch(),
            assets=sv,
            render_fn=_stub_render,
            on_missing="placeholder",
        )
    finally:
        stop_ev.set()

    by_stage = {s.stage: s for s in report.stages}
    assert by_stage["D"].status is StageStatus.done
    assert by_stage["H"].status is StageStatus.done
    assert report.overall is RunOverall.complete_with_placeholders
    assert (run_dir / "deck.pptx").is_file()


def test_r5_1_fastembed_load_timeout_degrades_to_structural_without_hanging(monkeypatch):
    """If fastembed TextEmbedding hangs (e.g. blocked HuggingFace download), degrade cleanly within load_timeout_s."""
    stop_ev = threading.Event()

    class HangingTextEmbedding:
        def __init__(self, model, **kwargs):
            stop_ev.wait(timeout=30.0)

    fake_fastembed = SimpleNamespace(TextEmbedding=HangingTextEmbedding)
    monkeypatch.setitem(sys.modules, "fastembed", fake_fastembed)

    t0 = time.monotonic()
    try:
        rf = rel_mod.filter_from_env(
            {
                "PPTV01_RELEVANCE": "auto",
                "PPTV01_RELEVANCE_LOAD_TIMEOUT_S": "0.2",
                "HF_ENDPOINT": "https://user:pw@hf-mirror.example",
            }
        )
    finally:
        stop_ev.set()
    dt = time.monotonic() - t0
    assert dt < 1.5
    assert rf.scorer is None
    assert "fastembed" in rf.unavailable and "timed out" in rf.unavailable
    assert "user" not in rf.unavailable and "pw" not in rf.unavailable


def test_r5_1_sanitize_url_and_pyalex_fetch_uses_httpx_timeouts(monkeypatch):
    """pyalex_fetch delegates to timeout-bounded httpx (never raw requests without timeout) and sanitize_url drops secrets."""
    assert (
        sanitize_url("https://user:secret@api.openalex.org/works?search=ai&api_key=MYKEY&mailto=a@b.c")
        == "https://api.openalex.org/works?search=ai&mailto=a%40b.c"
    )
    seen: list[httpx.Timeout] = []

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [{"id": "https://openalex.org/W1", "title": "AI"}]})

    orig_make = make_http

    def wrapped_make(timeout_s, contact, transport=None, *, connect_timeout_s=None, read_timeout_s=None):
        cli = orig_make(
            timeout_s,
            contact,
            transport=httpx.MockTransport(handler),
            connect_timeout_s=connect_timeout_s,
            read_timeout_s=read_timeout_s,
        )
        seen.append(cli.timeout)
        return cli

    import pptv01.d_retrieve.sources as src_mod

    monkeypatch.setattr(src_mod, "make_http", wrapped_make)
    rows = pyalex_fetch("ai", 3, api_key="k", connect_timeout_s=2.5, read_timeout_s=4.5)
    assert len(rows) == 1 and rows[0]["title"] == "AI"
    assert seen and seen[0].connect == 2.5 and seen[0].read == 4.5
