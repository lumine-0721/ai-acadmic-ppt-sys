"""R6 tests: Stage D image-topic relevance verification (B7 fix) using tests/replay/ data."""

from __future__ import annotations

import io
import json
from pathlib import Path

import httpx
from PIL import Image

from pptv01.contracts import (
    AssetRegistry,
    AssetRequirements,
    AssetStatus,
    ProductionPlan,
    SlidePlan,
    load_contract,
)
from pptv01.d_retrieve import run_stage_d, services_from_env
from pptv01.retrieval.relevance import RelevanceFilter
from tests.conftest import GOOD_A, GOOD_B
from tests.fakes import FakeClient

REPLAY = Path(__file__).parent / "replay"
RID = "20260101-000000-r600"


def _rp(name: str) -> dict:
    return json.loads((REPLAY / name).read_text("utf-8"))


def _jpeg(w: int = 1280, h: int = 800, color=(40, 110, 80)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, "JPEG")
    return b.getvalue()


def _commons_page(
    title: str,
    *,
    index: int = 1,
    desc: str = "",
    categories: str = "",
    lic: str = "CC BY 4.0",
    artist: str = "Commons Contributor",
    mime: str = "image/jpeg",
    w: int = 1600,
    h: int = 1000,
) -> dict:
    return {
        "index": index,
        "title": title,
        "imageinfo": [
            {
                "url": f"https://upload.wikimedia.org/{title.replace(' ', '_')}",
                "thumburl": f"https://thumb.wikimedia.org/{title.replace(' ', '_')}",
                "mime": mime,
                "width": w,
                "height": h,
                "size": 123456,
                "descriptionurl": f"https://commons.wikimedia.org/wiki/{title.replace(' ', '_')}",
                "extmetadata": {
                    "LicenseShortName": {"value": lic},
                    "Artist": {"value": artist},
                    "Credit": {"value": "Own work"},
                    "Restrictions": {"value": ""},
                    "ImageDescription": {"value": desc},
                    "Categories": {"value": categories},
                },
            }
        ],
    }


def _make_plan_and_sp(topic: str = "城市热岛效应与绿地降温") -> tuple[ProductionPlan, SlidePlan]:
    plan = ProductionPlan(run_id=RID, source_utterance=f"做一个关于{topic}的 PPT。", **{**GOOD_A, "topic": topic})
    sp = SlidePlan(
        run_id=RID,
        status="provisional",
        narrative=GOOD_B["narrative"],
        pages=[{"page_id": f"S{i:03d}", "status": "provisional", **p} for i, p in enumerate(GOOD_B["pages"], 1)],
    )
    return plan, sp


def _req(aid: str, target_name: str, purpose: str, hints: list[str], etype: str = "place") -> dict:
    return {
        "asset_id": aid,
        "page_ids": ["S001"],
        "need_refs": ["S001#1"],
        "type": "image",
        "purpose": purpose,
        "targets": [{"name": target_name, "entity_type": etype, "verification": "to_verify"}],
        "query_hints": hints,
        "priority": "required",
        "acceptance": ["真实场景照片", "许可明确"],
    }


def test_r6_replay_parthenon_rejects_arkansas_conflict_and_marks_uninspected_not_assessed(tmp_path):
    """Using real recorded replay data (commons_search_parthenon.json) without vision or embedding scorer:
    - US Arkansas namesake is rejected by geographic/entity conflict rule.
    - Acquired authentic Parthenon image is honestly flagged visual_relevance='not_assessed' & needs_review=True.
    """
    img = _jpeg()

    def handler(req_: httpx.Request) -> httpx.Response:
        if req_.url.host == "www.wikidata.org":
            return httpx.Response(200, json={"search": []})
        if req_.url.host == "commons.wikimedia.org":
            return httpx.Response(200, json=_rp("commons_search_parthenon.json"))
        if req_.url.host in ("upload.wikimedia.org", "thumb.wikimedia.org"):
            return httpx.Response(200, content=img, headers={"content-type": "image/jpeg"})
        return httpx.Response(404)

    sv = services_from_env(
        {"PPTV01_ASSETS": "auto", "PPTV01_RELEVANCE": "structural"},
        transport=httpx.MockTransport(handler),
    )
    plan, sp = _make_plan_and_sp("古希腊帕特农神庙建筑特征")
    reqs = AssetRequirements(
        run_id=RID,
        requirements=[_req("A001", "Parthenon", "展示雅典卫城帕特农神庙实景", ["Parthenon Athens"], etype="building")],
    )
    reg = run_stage_d(plan, sp, reqs, tmp_path, sv)
    a = reg.assets[0]
    assert a.status is AssetStatus.acquired
    assert "Arkansas" not in (a.identifier or "")
    # Recorded Arkansas hit in tests/replay/commons_search_parthenon.json must be rejected with geo conflict
    ark = [c for c in a.candidates if "Arkansas" in c.title]
    assert ark and all(c.decision == "rejected" for c in ark)
    assert any("geographic/entity conflict" in c.reason for c in ark)
    # Without a vision model, image must NEVER be treated as visually passed
    assert a.visual_relevance == "not_assessed"
    assert a.needs_review is True
    assert a.checks.relevance and a.checks.relevance.startswith("not_assessed")
    load_contract(AssetRegistry, tmp_path / "asset_registry.json")


def test_r6_t02_a009_pekin_illinois_rejected_for_beijing_target(tmp_path):
    """B7 / T02 A009 regression: Pekin, Illinois park must be rejected when targeting Beijing/北京."""
    img = _jpeg()
    pekin_page = _commons_page(
        "File:Pekin park 20231019 0017.jpg",
        index=1,
        desc="Autumn trees at Mineral Springs Park in Pekin, Illinois",
        categories="Pekin, Illinois|Parks in Tazewell County, Illinois|October 2023 in Illinois",
    )
    beijing_page = _commons_page(
        "File:Beijing Olympic Forest Park 2023.jpg",
        index=2,
        desc="Urban green park landscape in Beijing, China",
        categories="Olympic Forest Park (Beijing)|Parks in Beijing|China",
    )

    class HighScorer:
        name, model = "fastembed", "fake"

        def score(self, query, docs):
            # Even if embedding scorer mistakenly ranks Pekin higher than Beijing, deterministic rule blocks Pekin!
            return [0.92 if "Pekin" in d else 0.75 for d in docs]

    def handler(req_: httpx.Request) -> httpx.Response:
        if req_.url.host == "www.wikidata.org":
            return httpx.Response(200, json={"search": []})
        if req_.url.host == "commons.wikimedia.org":
            return httpx.Response(200, json={"query": {"pages": [pekin_page, beijing_page]}})
        if req_.url.host in ("upload.wikimedia.org", "thumb.wikimedia.org"):
            return httpx.Response(200, content=img, headers={"content-type": "image/jpeg"})
        return httpx.Response(404)

    sv = services_from_env(
        {"PPTV01_ASSETS": "auto", "PPTV01_RELEVANCE": "structural"},
        transport=httpx.MockTransport(handler),
    )
    sv.relevance = RelevanceFilter(HighScorer(), 0.30, mode="auto")
    plan, sp = _make_plan_and_sp("北京城市热岛效应与公园绿地缓解策略")
    reqs = AssetRequirements(
        run_id=RID,
        requirements=[_req("A009", "北京城市公园绿地", "展示北京大型城市公园绿地缓解热岛效应实景", ["Beijing urban park"])],
    )
    reg = run_stage_d(plan, sp, reqs, tmp_path, sv)
    a = reg.assets[0]
    assert a.status is AssetStatus.acquired
    assert a.identifier == "File:Beijing Olympic Forest Park 2023.jpg"
    pekin_cands = [c for c in a.candidates if "Pekin" in c.title]
    assert len(pekin_cands) == 1
    assert pekin_cands[0].decision == "rejected"
    assert "geographic/entity conflict" in pekin_cands[0].reason
    assert a.visual_relevance == "not_assessed" and a.needs_review is True


def test_r6_t02_a011_solarpunk_illustration_rejected_as_real_scene_evidence(tmp_path):
    """B7 / T02 A011 regression: Solarpunk / AI-generated / Illustration files cannot pass as real scene photos."""
    img = _jpeg()
    solarpunk = _commons_page(
        "File:A Solarpunk tower.png",
        index=1,
        desc="A futuristic Solarpunk green skyscraper concept illustration",
        categories="Solarpunk|AI-generated images|Digital illustrations",
        mime="image/png",
    )

    def handler(req_: httpx.Request) -> httpx.Response:
        if req_.url.host == "www.wikidata.org":
            return httpx.Response(200, json={"search": []})
        if req_.url.host == "commons.wikimedia.org":
            return httpx.Response(200, json={"query": {"pages": [solarpunk]}})
        if req_.url.host in ("upload.wikimedia.org", "thumb.wikimedia.org"):
            return httpx.Response(200, content=img, headers={"content-type": "image/png"})
        return httpx.Response(404)

    sv = services_from_env(
        {"PPTV01_ASSETS": "auto", "PPTV01_RELEVANCE": "structural"},
        transport=httpx.MockTransport(handler),
    )
    plan, sp = _make_plan_and_sp("城市立体绿化与屋顶花园降温案例")
    reqs = AssetRequirements(
        run_id=RID,
        requirements=[_req("A011", "Solarpunk green tower", "展示现实城市立体绿化建筑实景照片", ["green building vertical garden"])],
    )
    reg = run_stage_d(plan, sp, reqs, tmp_path, sv)
    a = reg.assets[0]
    assert a.status is AssetStatus.missing
    assert len(a.candidates) == 1
    assert a.candidates[0].decision == "rejected"
    assert "non-photographic artwork/illustration" in a.candidates[0].reason


def test_r6_vision_client_in_stage_d_rejects_irrelevant_and_verifies_relevant(tmp_path):
    """When a vision model is configured, Stage D inspects pixels: rejects irrelevant, accepts relevant without needs_review."""
    img1 = _jpeg(color=(180, 50, 50))
    img2 = _jpeg(color=(30, 140, 60))
    cand1 = _commons_page(
        "File:Beijing park interior bathroom.jpg",
        index=1,
        desc="Park building in Beijing",
        categories="Parks in Beijing",
    )
    cand2 = _commons_page(
        "File:Beijing green park aerial.jpg",
        index=2,
        desc="Aerial view of urban park in Beijing",
        categories="Parks in Beijing",
    )

    def handler(req_: httpx.Request) -> httpx.Response:
        if req_.url.host == "www.wikidata.org":
            return httpx.Response(200, json={"search": []})
        if req_.url.host == "commons.wikimedia.org":
            return httpx.Response(200, json={"query": {"pages": [cand1, cand2]}})
        if "interior" in str(req_.url):
            return httpx.Response(200, content=img1, headers={"content-type": "image/jpeg"})
        return httpx.Response(200, content=img2, headers={"content-type": "image/jpeg"})

    vision = FakeClient(
        [
            {"relevance": "irrelevant", "reason": "画面仅为建筑内部局部，未体现城市公园绿地"},
            {"relevance": "relevant", "reason": "清晰呈现北京城市公园大面积绿地与水体实景"},
        ]
    )
    sv = services_from_env(
        {"PPTV01_ASSETS": "auto", "PPTV01_RELEVANCE": "structural"},
        transport=httpx.MockTransport(handler),
        vision_client=vision,
    )
    plan, sp = _make_plan_and_sp("北京城市热岛效应")
    reqs = AssetRequirements(
        run_id=RID,
        requirements=[_req("A001", "Beijing park", "展示北京城市公园绿地实景", ["Beijing park"])],
    )
    reg = run_stage_d(plan, sp, reqs, tmp_path, sv)
    a = reg.assets[0]
    assert a.status is AssetStatus.acquired
    assert a.identifier == "File:Beijing green park aerial.jpg"
    assert a.visual_relevance == "relevant"
    assert a.needs_review is False
    assert a.checks.relevance and a.checks.relevance.startswith("relevant")
    rej = [c for c in a.candidates if "interior" in c.title]
    assert rej and rej[0].decision == "rejected" and "vision check irrelevant" in rej[0].reason
