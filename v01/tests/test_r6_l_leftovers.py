"""R6-L tests: OpenAlex 429/402/403 circuit breaker, pyalex removal, proxy docs, caption legibility & contrast."""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

from pptv01.contracts import AssetRequirements, AssetStatus, ProductionPlan, SlidePlan
from pptv01.d_retrieve.stage import run_stage_d, services_from_env
from tests.conftest import GOOD_A, GOOD_B

V01_ROOT = Path(__file__).resolve().parents[1]
RID = "20261121-000000-r6l"


def _make_ref_req(aid: str, title: str, hint: str) -> dict:
    return {
        "asset_id": aid,
        "page_ids": ["S001"],
        "need_refs": [f"S001#{int(aid[1:])}"],
        "type": "reference",
        "purpose": f"Cite foundational paper on {title}",
        "targets": [{"name": title, "entity_type": "publication", "verification": "to_verify"}],
        "query_hints": [hint],
        "priority": "required",
        "acceptance": ["verified DOI via OpenAlex or Crossref"],
    }


def _plan_and_sp() -> tuple[ProductionPlan, SlidePlan]:
    pp = ProductionPlan(run_id=RID, source_utterance="综述城市热岛", **GOOD_A)
    sp = SlidePlan(
        run_id=RID,
        status="provisional",
        narrative=GOOD_B["narrative"],
        pages=[{"page_id": f"S{i:03d}", "status": "provisional", **p} for i, p in enumerate(GOOD_B["pages"], 1)],
    )
    return pp, sp


@pytest.mark.parametrize("status_code", [429, 402, 403])
def test_r6_l_openalex_quota_circuit_breaker_skips_subsequent_calls(tmp_path, status_code: int):
    """After the first 429/402/403 response from OpenAlex, remaining OpenAlex requests are skipped in that run."""
    oa_requests: list[str] = []
    cr_requests: list[str] = []
    logs: list[str] = []

    papers = {
        "urban heat island": ("10.1000/uhi.1", "Urban Heat Island Review"),
        "cool pavement": ("10.1000/uhi.2", "Cool Pavements Mitigation Study"),
        "green roof": ("10.1000/uhi.3", "Green Roof Cooling Performance"),
    }

    def handler(req_: httpx.Request) -> httpx.Response:
        if req_.url.host == "api.openalex.org":
            oa_requests.append(str(req_.url))
            return httpx.Response(status_code, json={"error": f"quota {status_code}"})
        if req_.url.host == "api.crossref.org":
            cr_requests.append(str(req_.url))
            path = req_.url.path
            if path.startswith("/works/10.1000/"):
                doi = path.removeprefix("/works/")
                for doi_val, title in papers.values():
                    if doi == doi_val:
                        return httpx.Response(200, json={"message": {"DOI": doi_val, "title": [title]}})
                return httpx.Response(404)
            q = (req_.url.params.get("query.bibliographic") or req_.url.params.get("query") or "").lower()
            for key, (doi_val, title) in papers.items():
                if key in q:
                    return httpx.Response(
                        200,
                        json={
                            "message": {
                                "items": [
                                    {
                                        "DOI": doi_val,
                                        "title": [title],
                                        "author": [{"given": "Ada", "family": "Lovelace"}],
                                        "published": {"date-parts": [[2023]]},
                                        "container-title": ["Journal of Climate"],
                                        "type": "journal-article",
                                    }
                                ]
                            }
                        },
                    )
            return httpx.Response(200, json={"message": {"items": []}})
        return httpx.Response(404)

    sv = services_from_env(
        {
            "PPTV01_ASSETS": "auto",
            "PPTV01_RELEVANCE": "structural",
            "PPTV01_PIXEL_RULES": "off",
            "PPTV01_ASSETS_BUDGET_S": "30.0",
        },
        transport=httpx.MockTransport(handler),
        progress_fn=logs.append,
    )
    pp, sp = _plan_and_sp()
    reqs = AssetRequirements(
        run_id=RID,
        requirements=[
            _make_ref_req("A001", "Urban Heat Island Review", "urban heat island"),
            _make_ref_req("A002", "Cool Pavements Study", "cool pavement"),
            _make_ref_req("A003", "Green Roof Study", "green roof"),
        ],
    )
    reg = run_stage_d(pp, sp, reqs, tmp_path, sv)

    assert len(oa_requests) == 1, f"expected circuit breaker after 1st {status_code}, got {len(oa_requests)} OA requests"
    assert len(cr_requests) >= 6  # search + DOI verify for each of the 3 papers
    assert all(a.status is AssetStatus.acquired for a in reg.assets)
    assert all(a.source_name == "Crossref" for a in reg.assets)

    marker = f"openalex: circuit_open after {status_code} at "
    assert any(marker in ln for ln in logs)
    assert reg.relevance_filter and marker in reg.relevance_filter.note
    assert marker in reg.summary
    assert "skipped 2 request(s)" in reg.summary
    assert "est. saved ~" in reg.summary


def test_r6_l_no_pyalex_runtime_references_and_proxy_doc_exists():
    """pyalex dependency and runtime/doc wording are removed and docs/proxy.md documents proxy + HF_ENDPOINT."""
    pyproject_txt = (V01_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "pyalex" not in pyproject_txt

    for folder in (V01_ROOT / "src",):
        for py in folder.rglob("*.py"):
            txt = py.read_text(encoding="utf-8")
            assert not re.search(r"\bpyalex\b", txt, re.I), f"found pyalex reference in {py}"

    proxy_doc = (V01_ROOT / "docs" / "proxy.md").read_text(encoding="utf-8")
    for token in ("trust_env", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "HF_ENDPOINT", "FASTEMBED_CACHE_PATH"):
        assert token in proxy_doc, f"missing {token!r} in docs/proxy.md"
    assert "docs/proxy.md" in (V01_ROOT / "README.md").read_text(encoding="utf-8")
