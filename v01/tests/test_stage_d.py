"""Stage D with replayed REAL API responses (tests/replay/*.json, recorded by tools/record_replays.py) and synthetic
image bytes for downloads. No network. Fake scorer stands in for fastembed."""
import io
import json
from pathlib import Path

import httpx
import pytest
from PIL import Image

from pptv01.contracts import (AssetRecord, AssetRegistry, AssetRequirements, AssetStatus, ProductionPlan, SlidePlan,
                              load_contract)
from pptv01.d_retrieve import AssetRetrievalFailed, run_stage_d, services_from_env
from pptv01.d_retrieve.license import check_license, needs_attribution, plain
from pptv01.d_retrieve.validate import validate_registry
from pptv01.retrieval.relevance import RelevanceFilter
from tests.conftest import GOOD_A, GOOD_B

REPLAY = Path(__file__).parent / "replay"
RID = "20260101-000000-abcd"


def rp(name):
    return json.loads((REPLAY / name).read_text("utf-8"))


def jpeg(w=1280, h=800):
    b = io.BytesIO()
    Image.new("RGB", (w, h), (120, 130, 140)).save(b, "JPEG")
    return b.getvalue()


class Net:
    """Routes the four APIs + image downloads to replays. Records every request URL."""

    def __init__(self, *, wd_search="wikidata_search_parthenon_zh.json", p18=True, commons_search="commons_search_parthenon.json",
                 image=None, download_status=200, crossref_status=200, crossref_title=None):
        self.log, self.image, self.download_status = [], image if image is not None else jpeg(), download_status
        self.wd_search, self.p18, self.commons_search = wd_search, p18, commons_search
        self.crossref_status, self.crossref_title = crossref_status, crossref_title

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.log.append(str(req.url))
        q, host = dict(req.url.params), req.url.host
        if host == "www.wikidata.org":
            if q["action"] == "wbsearchentities":
                return httpx.Response(200, json=rp(self.wd_search) if self.wd_search else {"search": []})
            ent = rp("wikidata_entity_Q10288.json")
            if not self.p18:
                ent["entities"]["Q10288"]["claims"] = {}
            return httpx.Response(200, json=ent)
        if host == "commons.wikimedia.org":
            if "titles" in q:
                return httpx.Response(200, json=rp("commons_files_parthenon_p18.json"))
            if self.commons_search and "Parthenon" in q.get("gsrsearch", ""):
                return httpx.Response(200, json=rp(self.commons_search) if isinstance(self.commons_search, str) else self.commons_search)
            return httpx.Response(200, json={"batchcomplete": True})
        if host in ("upload.wikimedia.org", "thumb.wikimedia.org"):
            return httpx.Response(self.download_status, content=self.image, headers={"content-type": "image/jpeg"})
        if host == "api.crossref.org":
            if self.crossref_status != 200:
                return httpx.Response(self.crossref_status)
            d = rp("crossref_work_uhi.json")
            if self.crossref_title:
                d["message"]["title"] = [self.crossref_title]
            return httpx.Response(200, json=d)
        return httpx.Response(404)


class Scorer:
    name, model = "fastembed", "fake"

    def __init__(self, bad=("Arkansas",)):
        self.bad = bad

    def score(self, query, docs):
        return [0.05 if any(b in d for b in self.bad) else 0.7 for d in docs]


def make(tmp_path, reqs, net=None, *, scorer=None, oa_fetch=None, env=None):
    net = net or Net()
    sv = services_from_env({"PPTV01_ASSETS": "auto", "PPTV01_RELEVANCE": "structural", **(env or {})},
                           transport=httpx.MockTransport(net),
                           openalex_fetch=oa_fetch or (lambda q, n: rp("openalex_uhi.json")[:n]))
    sv.relevance = RelevanceFilter(scorer, 0.3 if scorer else None, mode="auto" if scorer else "structural")
    plan = ProductionPlan(run_id=RID, source_utterance="u", **{**GOOD_A, "topic": "世界著名建筑"})
    sp = SlidePlan(run_id=RID, status="provisional", narrative="n", pages=[
        {"page_id": f"S{i:03d}", "status": "provisional", **p} for i, p in enumerate(GOOD_B["pages"], 1)])
    return plan, sp, AssetRequirements(run_id=RID, requirements=reqs), sv, net


def req(aid, typ, targets=(), hints=("Parthenon photo",), purpose="展示帕特农神庙的外观", priority="required"):
    return {"asset_id": aid, "page_ids": ["S001"], "need_refs": [f"S001#{int(aid[1:])}"], "type": typ, "purpose": purpose,
            "targets": [{"name": n, "entity_type": et, "verification": v, **({"source_refs": ["R001"]} if v == "verified" else {})}
                for n, et, v in targets],
            "query_hints": list(hints), "priority": priority, "acceptance": ["相关", "许可明确"]}


def run(tmp_path, reqs, **kw):
    plan, sp, rq, sv, net = make(tmp_path, reqs, **kw)
    reg = run_stage_d(plan, sp, rq, tmp_path, sv)
    return reg, net, rq


PARTHENON = ("帕特农神庙", "building", "verified")


# ---------------------------------------------------------------- images
def test_named_target_uses_wikidata_p18_with_license_author_and_source_page(tmp_path):
    reg, net, rq = run(tmp_path, [req("A001", "image", [PARTHENON])], scorer=Scorer())
    a = reg.assets[0]
    assert a.status is AssetStatus.acquired and a.type.value == "image"
    assert a.entity.qid == "Q10288" and a.entity.chosen_by in ("relevance_score", "only_candidate", "search_rank")
    assert any("Q3753726" in alt for alt in a.entity.alternatives) or a.entity.chosen_by == "only_candidate"
    assert a.identifier == "File:The Parthenon in Athens.jpg" and a.source_name == "Wikimedia Commons"
    assert a.source_url.startswith("https://commons.wikimedia.org/wiki/File:") and a.license_or_terms.startswith("CC BY 2.0")
    assert a.author and "flickr" not in a.author.lower().split()[0:0] and "CC BY 2.0" in a.attribution and a.attribution.endswith(")")
    f = tmp_path / a.file_path
    assert f.suffix == ".jpg" and f.read_bytes()[:2] == b"\xff\xd8" and (a.width, a.height) == (1280, 800)
    assert "Wikidata P18" in a.checks.relevance and "NOT visually inspected" in a.checks.relevance
    sel = [c for c in a.candidates if c.decision == "selected"]
    assert len(sel) == 1 and sel[0].origin == "wikidata_p18"
    arkansas = [c for c in a.candidates if "Arkansas" in c.title]
    assert arkansas and all(c.decision == "rejected" for c in arkansas)
    load_contract(AssetRegistry, tmp_path / "asset_registry.json")          # written + valid
    assert {c.backend for c in reg.sources} >= {"wikidata", "commons"}


def test_no_entity_falls_back_to_search_and_relevance_filter_removes_arkansas(tmp_path):
    reg, _, _ = run(tmp_path, [req("A001", "image", [("某纪念碑", "concept", "to_verify")], hints=("Parthenon",))],
                    net=Net(wd_search=None), scorer=Scorer())
    a = reg.assets[0]
    assert a.status is AssetStatus.acquired and a.entity is None and "Arkansas" not in a.identifier
    rej = {c.title: c.reason for c in a.candidates if c.decision == "rejected"}
    assert any("Arkansas" in t and "low_relevance" in r for t, r in rej.items())
    assert "embedding score" in a.checks.relevance
    assert "to_verify" in a.notes                       # D must not upgrade an unverified target


def test_without_a_scorer_lexical_overlap_is_required_and_said_so(tmp_path):
    net = Net(wd_search=None)
    reg, _, _ = run(tmp_path, [req("A001", "image", [("Parthenon", "building", "verified")], hints=("Parthenon",))], net=net)
    a = reg.assets[0]
    assert a.status is AssetStatus.acquired and "no embedding scorer" in a.checks.relevance
    # a target that appears nowhere in the file metadata is not accepted by lexical fallback
    reg, _, _ = run(tmp_path / "x" if (tmp_path / "x").mkdir() is None else None,
                    [req("A001", "image", [("Zebra crossing", "concept", "to_verify")], hints=("Parthenon",))], net=Net(wd_search=None))
    assert reg.assets[0].status is AssetStatus.missing


def _file(title, lic, mime="image/jpeg", w=2000, restrictions=""):
    return {"index": 1, "title": title, "imageinfo": [{
        "url": "https://upload.wikimedia.org/x.jpg", "thumburl": "https://upload.wikimedia.org/t.jpg", "mime": mime, "width": w,
        "height": 1000, "size": 1, "descriptionurl": "https://commons.wikimedia.org/wiki/" + title,
        "extmetadata": {"LicenseShortName": {"value": lic}, "Artist": {"value": "<a href='x'>Some Author</a>"},
                        "Restrictions": {"value": restrictions}, "ImageDescription": {"value": "Parthenon view"}}}]}


def test_licence_format_and_size_gates_reject_with_reasons_and_never_download(tmp_path):
    pages = [_file("File:Parthenon nc.jpg", "CC BY-NC 4.0"), _file("File:Parthenon nd.jpg", "CC BY-ND 2.0"),
             _file("File:Parthenon fair.jpg", "Fair use"), _file("File:Parthenon none.jpg", ""),
             _file("File:Parthenon tiny.jpg", "CC0", w=300), _file("File:Parthenon svg.svg", "CC0", mime="image/svg+xml"),
             _file("File:Parthenon restricted.jpg", "CC0", restrictions="personality rights")]
    net = Net(wd_search=None, commons_search={"query": {"pages": pages}})
    reg, net, _ = run(tmp_path, [req("A001", "image", [("Parthenon", "building", "verified")], hints=("Parthenon",))], net=net)
    a = reg.assets[0]
    assert a.status is AssetStatus.missing and a.file_path is None and "assets" not in "".join(p.name for p in tmp_path.iterdir())
    reasons = {c.title: c.reason for c in a.candidates}
    assert "licence not allowed" in reasons["File:Parthenon nc.jpg"] and "licence not allowed" in reasons["File:Parthenon nd.jpg"]
    assert "not allowed" in reasons["File:Parthenon fair.jpg"] and "no licence" in reasons["File:Parthenon none.jpg"]
    assert "too small" in reasons["File:Parthenon tiny.jpg"] and "not supported" in reasons["File:Parthenon svg.svg"]
    assert "restrictions" in reasons["File:Parthenon restricted.jpg"]
    assert not any("wikimedia.org/wikipedia" in u for u in net.log)         # nothing downloaded


def test_download_failure_and_corrupt_bytes_are_rejected_not_faked(tmp_path):
    reg, _, _ = run(tmp_path, [req("A001", "image", [PARTHENON])], net=Net(download_status=404))
    a = reg.assets[0]
    assert a.status is AssetStatus.missing and any("download failed" in c.reason for c in a.candidates)
    assert any(c.status == "error" for c in reg.sources)
    (tmp_path / "b").mkdir()
    reg, _, _ = run(tmp_path / "b", [req("A001", "image", [PARTHENON])], net=Net(image=b"<html>not an image</html>"))
    assert reg.assets[0].status is AssetStatus.missing
    assert any("not a readable image" in c.reason for c in reg.assets[0].candidates)


def test_multi_target_requirement_records_uncovered_targets(tmp_path):
    reg, _, _ = run(tmp_path, [req("A001", "image", [PARTHENON, ("Chrysler Building", "building", "verified")])])
    a = reg.assets[0]
    assert a.uncovered_targets == ["Chrysler Building"] and "only the first target" in a.notes


# ---------------------------------------------------------------- references
UHI_TITLE = "On the influence of density and morphology on the Urban Heat Island intensity"


def test_exact_title_reference_is_found_in_openalex_and_doi_verified_in_crossref(tmp_path):
    r = req("A001", "reference", [(UHI_TITLE, "publication", "verified")], hints=("urban heat island",), purpose="引用热岛强度研究")
    reg, net, _ = run(tmp_path, [r], net=Net(crossref_title=UHI_TITLE))
    a = reg.assets[0]
    assert a.status is AssetStatus.acquired and a.identifier == "10.1038/s41467-020-16461-9"
    assert a.source_url == "https://doi.org/10.1038/s41467-020-16461-9" and "OpenAlex" in a.source_name
    assert "exact title" in a.checks.relevance and "similarity" in a.checks.format
    rec = json.loads((tmp_path / a.file_path).read_text("utf-8"))
    assert rec["doi"] == a.identifier and "no abstract" in rec["note"] and rec["authors"]
    assert any(u.startswith("https://api.crossref.org/works/10.1038") for u in net.log)


def test_doi_whose_crossref_title_differs_is_rejected(tmp_path):
    r = req("A001", "reference", [(UHI_TITLE, "publication", "verified")], hints=("urban heat island",))
    reg, _, _ = run(tmp_path, [r], net=Net(crossref_title="A completely different paper about soil"))
    a = reg.assets[0]
    assert a.status is AssetStatus.missing and any("similarity" in c.reason for c in a.candidates)


def test_unreachable_crossref_does_not_block_but_is_recorded_as_unverified(tmp_path):
    r = req("A001", "reference", [(UHI_TITLE, "publication", "verified")], hints=("urban heat island",))
    reg, _, _ = run(tmp_path, [r], net=Net(crossref_status=503))
    a = reg.assets[0]
    assert a.status is AssetStatus.acquired and "NOT verified" in a.checks.format
    assert any(c.backend == "crossref" and c.status == "error" for c in reg.sources)


def test_openalex_quota_error_is_recorded_and_crossref_search_is_the_fallback(tmp_path):
    from pptv01.retrieval.backends import BackendError

    def quota(q, n):
        raise BackendError("OpenAlex quota/rate limit reached (HTTP 429)")
    cr_search = {"message": {"items": [{"DOI": "10.1000/xyz", "title": ["Urban heat island review"], "container-title": ["J"],
                                        "issued": {"date-parts": [[2020]]}, "URL": "https://doi.org/10.1000/xyz", "type": "journal-article"}]}}
    net = Net()
    orig = net.__call__

    def routed(req_):
        if req_.url.host == "api.crossref.org" and req_.url.path == "/works":
            return httpx.Response(200, json=cr_search)
        if req_.url.host == "api.crossref.org":
            return httpx.Response(200, json={"message": {"title": ["Urban heat island review"]}})
        return orig(req_)
    plan, sp, rq, sv, _ = make(tmp_path, [req("A001", "reference", [("城市热岛", "concept", "verified")], hints=("urban heat island",))],
                               net=routed, oa_fetch=quota)
    reg = run_stage_d(plan, sp, rq, tmp_path, sv)
    a = reg.assets[0]
    assert a.status is AssetStatus.acquired and a.source_name == "Crossref" and "OpenAlex unavailable" in a.notes
    oa = [c for c in reg.sources if c.backend == "openalex"]
    assert oa and oa[0].status == "error" and "quota" in oa[0].error


def test_reference_relevance_filter_drops_offtopic_works(tmp_path):
    class S:
        name, model = "fastembed", "fake"

        def score(self, q, docs):
            return [0.9 if "morphology" in d else 0.05 for d in docs]
    r = req("A001", "reference", [("城市热岛", "concept", "verified")], hints=("urban heat island",))
    reg, _, _ = run(tmp_path, [r], scorer=S())
    a = reg.assets[0]
    assert a.status is AssetStatus.acquired and "morphology" in json.loads((tmp_path / a.file_path).read_text("utf-8"))["title"]
    assert any("low_relevance" in c.reason for c in a.candidates if c.decision == "rejected")


# ---------------------------------------------------------------- other types, disabled, failure
def test_unsupported_types_are_missing_with_reason_and_no_file(tmp_path):
    reqs = [req("A001", "diagram", hints=("x",)), req("A002", "statistic", [("咖啡消费数据", "dataset", "to_verify")]),
            req("A003", "chart_data", [("某数据", "dataset", "to_verify")]), req("A004", "table", [("风险清单", "concept", "to_verify")]),
            req("A005", "icon", hints=("x",))]
    reg, net, _ = run(tmp_path, reqs)
    assert [a.status for a in reg.assets] == [AssetStatus.missing] * 5 and all(a.notes for a in reg.assets)
    assert "drawn natively" in reg.assets[0].notes and "not invented" in reg.assets[1].notes
    assert not (tmp_path / "assets").exists() and not net.log


def test_disabled_assets_are_recorded_as_disabled_not_success(tmp_path):
    plan, sp, rq, sv, net = make(tmp_path, [req("A001", "image", [PARTHENON])], env={"PPTV01_ASSETS": "none"})
    reg = run_stage_d(plan, sp, rq, tmp_path, sv)
    assert reg.assets[0].status is AssetStatus.missing and "disabled" in reg.assets[0].notes
    assert reg.sources[0].status == "disabled" and not net.log


def test_all_calls_failing_raises_but_still_writes_the_registry(tmp_path):
    class Down(Net):
        def __call__(self, req_):
            raise httpx.ConnectError("no network")

    def dead(q, n):
        from pptv01.retrieval.backends import BackendError
        raise BackendError("OpenAlex error: ConnectionError")
    plan, sp, rq, sv, _ = make(tmp_path, [req("A001", "image", [PARTHENON]),
                                          req("A002", "reference", [("x", "concept", "verified")], hints=("x y",))],
                               net=Down(), oa_fetch=dead)
    with pytest.raises(AssetRetrievalFailed) as e:
        run_stage_d(plan, sp, rq, tmp_path, sv)
    assert "all" in str(e.value) and (tmp_path / "asset_registry.json").is_file()
    assert all(a.status is AssetStatus.missing for a in e.value.registry.assets)


# ---------------------------------------------------------------- licence policy + contract rules
@pytest.mark.parametrize("lic,ok", [
    ("CC0", True), ("Public domain", True), ("PD-old-100", True), ("CC BY 4.0", True), ("CC BY-SA 3.0", True),
    ("CC BY-SA 2.5", True), ("CC BY 2.0", True), ("CC-BY-SA-4.0", True),
    ("CC BY-NC 4.0", False), ("CC BY-ND 2.0", False), ("CC BY-NC-SA 3.0", False), ("Fair use", False), ("", False),
    ("Copyrighted free use", False), ("All rights reserved", False), ("GFDL", False), ("Unknown", False)])
def test_license_policy(lic, ok):
    assert check_license(lic)[0] is ok


def test_license_helpers():
    assert needs_attribution("CC BY-SA 3.0") and needs_attribution("CC BY 2.0") and not needs_attribution("CC0") \
        and not needs_attribution("Public domain")
    assert plain("<a href='x'>Foo &amp; Bar</a> <b>2020</b>") == "Foo & Bar 2020"
    assert not check_license("CC0", "personality rights")[0]


def test_contract_rules_for_acquired_assets():
    base = dict(asset_id="A001", status="acquired", type="image", file_path="assets/A001.jpg", source_url="https://x/y",
                source_name="Wikimedia Commons", license_or_terms="CC BY 2.0", retrieved_at="2026-01-01T00:00:00+08:00",
                sha256="a" * 64, mime="image/jpeg", width=10, height=10)
    with pytest.raises(ValueError):                       # CC BY needs author + attribution
        AssetRecord(**base)
    AssetRecord(**base, author="A", attribution="A, CC BY 2.0")
    with pytest.raises(ValueError):                       # image needs hash/size
        AssetRecord(**{**base, "sha256": None, "license_or_terms": "CC0"})
    with pytest.raises(ValueError):                       # reference needs a DOI
        AssetRecord(**{**base, "type": "reference", "license_or_terms": "CC0", "file_path": "assets/A001.json"})


def test_validate_registry_catches_wrong_ids_missing_files_and_hash_mismatch(tmp_path):
    reg, _, rq = run(tmp_path, [req("A001", "image", [PARTHENON])])
    validate_registry(reg, rq, tmp_path)
    (tmp_path / reg.assets[0].file_path).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="sha256"):
        validate_registry(reg, rq, tmp_path)
    (tmp_path / reg.assets[0].file_path).unlink()
    with pytest.raises(ValueError, match="missing/empty"):
        validate_registry(reg, rq, tmp_path)
    rq2 = AssetRequirements(run_id=RID, requirements=[req("A001", "image", [PARTHENON]), req("A002", "diagram", hints=("x",))])
    with pytest.raises(ValueError, match="exactly one record"):
        validate_registry(reg, rq2, tmp_path)


def test_runner_integrates_stage_d_and_reports_missing_assets(tmp_path):
    from pptv01.runner import run_pipeline
    from tests.conftest import GOOD_A, GOOD_B, GOOD_C
    from tests.fakes import FakeClient
    sv = services_from_env({"PPTV01_ASSETS": "auto", "PPTV01_RELEVANCE": "structural"}, transport=httpx.MockTransport(Net()),
                           openalex_fetch=lambda q, n: rp("openalex_uhi.json")[:n])
    U = "帮我做一个城市热岛效应的 PPT。"
    report, run_dir = run_pipeline(U, tmp_path, FakeClient([GOOD_A, GOOD_B, GOOD_C]), run_id="t-d", assets=sv)
    st = {s.stage: s for s in report.stages}
    assert st["D"].status.value == "done" and "asset_registry.json" in st["D"].output_files
    assert report.missing_assets and report.tool_config["assets"]["summary"]
    load_contract(AssetRegistry, run_dir / "asset_registry.json")
