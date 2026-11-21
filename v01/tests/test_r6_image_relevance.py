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
        {"PPTV01_ASSETS": "auto", "PPTV01_RELEVANCE": "structural", "PPTV01_PIXEL_RULES": "off"},
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
        {"PPTV01_ASSETS": "auto", "PPTV01_RELEVANCE": "structural", "PPTV01_PIXEL_RULES": "off"},
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
        {"PPTV01_ASSETS": "auto", "PPTV01_RELEVANCE": "structural", "PPTV01_PIXEL_RULES": "off"},
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
        {"PPTV01_ASSETS": "auto", "PPTV01_RELEVANCE": "structural", "PPTV01_PIXEL_RULES": "off"},
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


def test_n5_b14_no_hardcoded_smoke_homonyms_in_image_rules():
    """Verify image_rules.py uses general rules without benchmark-specific hardcoding."""
    import inspect
    import re
    import pptv01.d_retrieve.image_rules as ir

    src = inspect.getsource(ir).lower()
    for forbidden in ("_homonym_conflicts", "tazewell", "nashville", "centennial park"):
        assert forbidden not in src, f"found smoke-fitted token {forbidden!r} in image_rules.py"
    assert not re.search(r"\bpekin\b", src), "found hardcoded 'pekin' in image_rules.py"


def test_n5_b15_beijing_never_falls_back_to_new_york_iss_photo(tmp_path):
    """B15 regression: when Pekin is rejected and only an unrelated New York ISS photo remains,
    Stage D must reject New York and honestly mark Beijing as missing (+ generate user input request).
    """
    from pptv01.contracts import UserInputRequests

    img = _jpeg()
    pekin = _commons_page(
        "File:Pekin park 20231019 0017.jpg",
        index=1,
        desc="Park trees in Pekin, Illinois",
        categories="Pekin, Illinois|Parks in Illinois",
    )
    nyc = _commons_page(
        "File:ISS-39 New York City.jpg",
        index=2,
        desc="Satellite view of urban heat island and parks in New York City from the ISS",
        categories="New York City from space|ISS Expedition 39",
    )

    class LenientScorer:
        name, model = "fastembed", "fake"

        def score(self, query, docs):
            return [0.42 for _ in docs]

    def handler(req_: httpx.Request) -> httpx.Response:
        if req_.url.host == "www.wikidata.org":
            return httpx.Response(200, json={"search": []})
        if req_.url.host == "commons.wikimedia.org":
            return httpx.Response(200, json={"query": {"pages": [pekin, nyc]}})
        return httpx.Response(200, content=img, headers={"content-type": "image/jpeg"})

    sv = services_from_env(
        {"PPTV01_ASSETS": "auto", "PPTV01_RELEVANCE": "structural", "PPTV01_PIXEL_RULES": "off"},
        transport=httpx.MockTransport(handler),
    )
    sv.relevance = RelevanceFilter(LenientScorer(), 0.30, mode="auto")
    plan, sp = _make_plan_and_sp("北京城市热岛效应")
    reqs = AssetRequirements(
        run_id=RID,
        requirements=[_req("A009", "北京城市公园绿地", "展示北京公园绿地实景", ["Beijing urban park"])],
    )
    reg = run_stage_d(plan, sp, reqs, tmp_path, sv)
    a = reg.assets[0]
    assert a.status is AssetStatus.missing
    assert all(c.decision == "rejected" for c in a.candidates)
    uir = load_contract(UserInputRequests, tmp_path / "user_input_requests.json")
    assert any(r.asset_id == "A009" and r.status.value == "open" for r in uir.requests)


def test_n5_b14_three_unrelated_homonym_places_general_rules(tmp_path):
    """Prove generality with 3 homonym places completely unrelated to Smoke 4:
    1) Springfield, Illinois vs Springfield, Oregon (same-country US state conflict)
    2) Paris, France vs Paris, Texas (country conflict)
    3) Cambridge, UK vs Cambridge, Massachusetts (country conflict)
    """
    img = _jpeg()
    cases = [
        (
            "springfield",
            _req("A001", "Springfield, Illinois", "Show historic civic architecture in Springfield, Illinois", ["Springfield Illinois"]),
            _commons_page("File:Springfield Oregon downtown.jpg", index=1, desc="Main Street in Springfield, Oregon", categories="Springfield, Oregon|Oregon"),
            _commons_page("File:Old State Capitol Springfield Illinois.jpg", index=2, desc="Historic capitol in Springfield, Illinois", categories="Springfield, Illinois|Illinois"),
            "File:Old State Capitol Springfield Illinois.jpg",
        ),
        (
            "paris",
            _req("A001", "Paris, France", "Show urban streetscape in Paris, France", ["Paris France"]),
            _commons_page("File:Eiffel Tower replica Paris Texas.jpg", index=1, desc="Tower in Paris, Texas", categories="Paris, Texas|Texas"),
            _commons_page("File:Seine and Eiffel Tower Paris France.jpg", index=2, desc="View of Paris, France", categories="Paris|France"),
            "File:Seine and Eiffel Tower Paris France.jpg",
        ),
        (
            "cambridge",
            _req("A001", "Cambridge, England", "Show university colleges in Cambridge, UK", ["Cambridge England"]),
            _commons_page("File:Harvard Square Cambridge Massachusetts.jpg", index=1, desc="Square in Cambridge, Massachusetts", categories="Cambridge, Massachusetts|Massachusetts"),
            _commons_page("File:Kings College Chapel Cambridge England.jpg", index=2, desc="College in Cambridge, England", categories="Cambridge|England"),
            "File:Kings College Chapel Cambridge England.jpg",
        ),
    ]

    class WrongFirstScorer:
        name, model = "fastembed", "fake"

        def score(self, query, docs):
            # Deliberately score the wrong homonym (index 0) higher than the right one (index 1)
            return [0.91, 0.72]

    for subdir, req_dict, wrong_page, right_page, expected_title in cases:
        work = tmp_path / subdir
        work.mkdir()

        def handler(req_: httpx.Request, wp=wrong_page, rp=right_page) -> httpx.Response:
            if req_.url.host == "www.wikidata.org":
                return httpx.Response(200, json={"search": []})
            if req_.url.host == "commons.wikimedia.org":
                return httpx.Response(200, json={"query": {"pages": [wp, rp]}})
            return httpx.Response(200, content=img, headers={"content-type": "image/jpeg"})

        sv = services_from_env(
            {"PPTV01_ASSETS": "auto", "PPTV01_RELEVANCE": "structural", "PPTV01_PIXEL_RULES": "off"},
            transport=httpx.MockTransport(handler),
        )
        sv.relevance = RelevanceFilter(WrongFirstScorer(), 0.30, mode="auto")
        plan, sp = _make_plan_and_sp(f"Architecture of {subdir}")
        reg = run_stage_d(plan, sp, AssetRequirements(run_id=RID, requirements=[req_dict]), work, sv)
        a = reg.assets[0]
        assert a.status is AssetStatus.acquired, f"{subdir}: expected acquired, got {a}"
        assert a.identifier == expected_title, f"{subdir}: picked wrong homonym {a.identifier}"
        wrong_c = next(c for c in a.candidates if c.title == wrong_page["title"])
        assert wrong_c.decision == "rejected"
        assert "geographic/entity conflict" in wrong_c.reason


def test_n5_live_wikidata_disambiguation_rejects_pekin_illinois_even_with_multitarget_new_york(tmp_path):
    """Live Smoke 4 T02 A009 regression: Wikidata returns Q578328 (Pekin, Illinois) with exact alias '北京'
    alongside Q956 (北京市), and requirement has secondary target '纽约'.
    Stage D must reject Q578328 at entity disambiguation and select Q956 (Beijing, China).
    """
    img = _jpeg()
    wd_search = {
        "search": [
            {
                "id": "Q578328",
                "label": "Pekin",
                "description": "city and county seat of Tazewell County, Illinois, United States",
                "match": {"type": "alias", "language": "zh", "text": "北京"},
            },
            {
                "id": "Q956",
                "label": "北京市",
                "description": "中华人民共和国首都暨直辖市",
                "match": {"type": "label", "language": "zh", "text": "北京市"},
            },
        ]
    }
    wd_ent_q956 = {
        "entities": {
            "Q956": {
                "id": "Q956",
                "labels": {"en": {"value": "Beijing"}, "zh": {"value": "北京市"}},
                "descriptions": {"en": {"value": "capital city of China"}},
                "aliases": {"zh": [{"value": "北京"}], "en": [{"value": "Peking"}]},
                "claims": {
                    "P17": [{"mainsnak": {"datavalue": {"value": {"id": "Q148"}}}, "rank": "normal"}],
                    "P18": [{"mainsnak": {"datavalue": {"value": "Beijing_skyline_from_northeast_4th_ring_road.jpg"}}, "rank": "normal"}],
                },
            }
        }
    }
    beijing_p18 = _commons_page(
        "File:Beijing skyline from northeast 4th ring road.jpg",
        index=1,
        desc="Beijing skyline in China",
        categories="Beijing|China",
    )

    def handler(req_: httpx.Request) -> httpx.Response:
        q = dict(req_.url.params)
        if req_.url.host == "www.wikidata.org":
            if q.get("action") == "wbsearchentities":
                return httpx.Response(200, json=wd_search)
            if q.get("props") == "claims":
                return httpx.Response(200, json={"entities": {"Q578328": {"claims": {}}, "Q956": {"claims": {}}}})
            return httpx.Response(200, json=wd_ent_q956)
        if req_.url.host == "commons.wikimedia.org":
            return httpx.Response(200, json={"query": {"pages": [beijing_p18]}})
        return httpx.Response(200, content=img, headers={"content-type": "image/jpeg"})

    sv = services_from_env(
        {"PPTV01_ASSETS": "auto", "PPTV01_RELEVANCE": "structural", "PPTV01_PIXEL_RULES": "off"},
        transport=httpx.MockTransport(handler),
    )
    plan, sp = _make_plan_and_sp("城市热岛效应")
    multi_req = {
        "asset_id": "A009",
        "page_ids": ["S001"],
        "need_refs": ["S001#1"],
        "type": "image",
        "purpose": "以北京、纽约等典型城市展示城市热岛实景",
        "targets": [
            {"name": "北京", "entity_type": "place", "verification": "to_verify"},
            {"name": "纽约", "entity_type": "place", "verification": "to_verify"},
        ],
        "query_hints": ["Beijing urban heat island", "New York urban heat island"],
        "priority": "required",
        "acceptance": ["真实场景", "许可明确"],
    }
    reg = run_stage_d(plan, sp, AssetRequirements(run_id=RID, requirements=[multi_req]), tmp_path, sv)
    a = reg.assets[0]
    assert a.status is AssetStatus.acquired
    assert a.entity is not None and a.entity.qid == "Q956"
    assert a.identifier == "File:Beijing skyline from northeast 4th ring road.jpg"
    assert any("Q578328" in alt and "geographic conflict" in alt for alt in a.entity.alternatives)
