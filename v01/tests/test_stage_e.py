"""Stage E (Slide Asset Mapping) unit tests."""

from __future__ import annotations

import io
import json
from pathlib import Path

import httpx
import pytest
from PIL import Image

from pptv01.contracts import (
    AssetRegistry,
    AssetRequirements,
    ProductionPlan,
    SlideAssetMapping,
    SlidePlan,
    StageStatus,
    UserInputRequests,
    load_contract,
    write_contract,
)
from pptv01.e_mapping import run_stage_e, vision_client_from_env
from pptv01.llm import LLMConfigError, LLMOutputError
from pptv01.runner import run_pipeline
from tests.conftest import GOOD_A, GOOD_B, GOOD_C, GOOD_TH
from tests.fakes import FakeClient
from tests.test_stage_d import PARTHENON, Net, rp

RID = "20260101-000000-abcd"


def _png(w=100, h=80) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (w, h), (30, 90, 150)).save(b, "PNG")
    return b.getvalue()


def _setup_artifacts(tmp_path: Path, *, with_provided_u001: bool = False):
    pp = ProductionPlan(run_id=RID, source_utterance="帮我做一个城市热岛效应的 PPT。", **GOOD_A)
    sp = SlidePlan(
        run_id=RID,
        status="provisional",
        narrative=GOOD_B["narrative"],
        pages=[{"page_id": f"S{i:03d}", "status": "provisional", **p} for i, p in enumerate(GOOD_B["pages"], 1)],
    )
    reqs = AssetRequirements(
        run_id=RID,
        requirements=[
            {
                "asset_id": "A001",
                "page_ids": ["S001"],
                "need_refs": ["S001#1"],
                "type": "image",
                "purpose": "在第一页展示热像图对比",
                "targets": [{"name": "某城市热像图", "entity_type": "dataset"}],
                "query_hints": ["urban heat island"],
                "priority": "required",
                "acceptance": ["相关", "许可明确"],
            },
            {
                "asset_id": "A002",
                "page_ids": ["S002"],
                "need_refs": ["S002#1"],
                "type": "diagram",
                "purpose": "绘制热岛成因机制图",
                "targets": [],
                "query_hints": ["heat island diagram"],
                "priority": "optional",
                "acceptance": ["机制完整", "许可明确"],
            },
        ],
    )
    (tmp_path / "assets").mkdir(exist_ok=True)
    img_bytes = _png()
    (tmp_path / "assets" / "A001.png").write_bytes(img_bytes)
    import hashlib
    sha = hashlib.sha256(img_bytes).hexdigest()
    reg = AssetRegistry(
        run_id=RID,
        assets=[
            {
                "asset_id": "A001",
                "status": "acquired",
                "type": "image",
                "file_path": "assets/A001.png",
                "source_url": "https://commons.wikimedia.org/wiki/File:UHI.png",
                "source_name": "Wikimedia Commons",
                "license_or_terms": "CC0",
                "identifier": "File:UHI.png",
                "sha256": sha,
                "mime": "image/png",
                "width": 100,
                "height": 80,
                "retrieved_at": "2026-01-01T00:00:00+08:00",
                "candidates": [{"title": "File:UHI.png", "decision": "selected", "reason": "ok"}],
            },
            {
                "asset_id": "A002",
                "status": "missing",
                "type": "diagram",
                "notes": "diagram: not retrieved in D",
            },
        ],
    )
    req_entry = {
        "request_id": "U001",
        "kind": "diagram",
        "asset_id": "A002",
        "page_ids": ["S002"],
        "need": "绘制热岛成因机制图",
        "why": "D 未找到",
        "format_wanted": "JSON 或 PNG",
        "acceptance": ["机制完整"],
        "blocking": False,
        "fallback": "placeholder",
        "status": "open",
    }
    if with_provided_u001:
        (tmp_path / "inputs").mkdir(exist_ok=True)
        (tmp_path / "inputs" / "U001.png").write_bytes(img_bytes)
        req_entry["status"] = "provided"
        req_entry["provided"] = {
            "file": "inputs/U001.png",
            "sha256": sha,
            "source": "IPCC",
            "license": "CC BY 4.0",
        }
    uir = UserInputRequests(run_id=RID, requests=[req_entry], summary="0 open" if with_provided_u001 else "1 open")
    return pp, sp, reqs, reg, uir


def test_mapping_honestly_marks_not_assessed_without_vision_model(tmp_path):
    pp, sp, reqs, reg, uir = _setup_artifacts(tmp_path)
    c = FakeClient([
        {
            "mappings": [
                {"page_id": "S001", "asset_id": "A001", "usage": "置于 S001 右侧展示城区与郊区温度差异对比", "accepted": True},
            ],
            "unused_assets": [],
        }
    ])
    mapping, attempts = run_stage_e(pp, sp, reqs, reg, uir, c, tmp_path)
    assert len(attempts) == 1
    assert mapping.mappings[0].relevance == "not_assessed"
    assert "no vision model" in mapping.mappings[0].relevance_note
    load_contract(SlideAssetMapping, tmp_path / "slide_asset_mapping.json")


def test_mapping_rejects_missing_asset_and_repairs_on_retry(tmp_path):
    pp, sp, reqs, reg, uir = _setup_artifacts(tmp_path)
    bad = {
        "mappings": [
            {"page_id": "S001", "asset_id": "A001", "usage": "置于 S001 右侧展示城区与郊区温度差异对比", "accepted": True},
            {"page_id": "S002", "asset_id": "A002", "usage": "置于 S002 展示热岛成因机制流程图", "accepted": True},
        ],
        "unused_assets": [],
    }
    good = {
        "mappings": [
            {"page_id": "S001", "asset_id": "A001", "usage": "置于 S001 右侧展示城区与郊区温度差异对比", "accepted": True},
        ],
        "unused_assets": [],
    }
    c = FakeClient([bad, good])
    mapping, attempts = run_stage_e(pp, sp, reqs, reg, uir, c, tmp_path)
    assert len(attempts) == 2
    assert "MUST NOT be mapped" in c.calls[1][-1]["content"]
    assert len(mapping.mappings) == 1 and mapping.mappings[0].asset_id == "A001"


def test_mapping_allows_multipage_reuse_with_distinct_usage_and_rejects_identical_usage(tmp_path):
    pp, sp, reqs, reg, uir = _setup_artifacts(tmp_path)
    identical = {
        "mappings": [
            {"page_id": "S001", "asset_id": "A001", "usage": "展示城市热岛温度分布图", "accepted": True},
            {"page_id": "S002", "asset_id": "A001", "usage": "展示城市热岛温度分布图", "accepted": True},
        ],
        "unused_assets": [],
    }
    distinct = {
        "mappings": [
            {"page_id": "S001", "asset_id": "A001", "usage": "置于 S001 展示热岛定义中的城郊温差特征", "accepted": True},
            {"page_id": "S002", "asset_id": "A001", "usage": "置于 S002 辅助说明高密度建成区与温度升高的对应关系", "accepted": True},
        ],
        "unused_assets": [],
    }
    c = FakeClient([identical, distinct])
    mapping, attempts = run_stage_e(pp, sp, reqs, reg, uir, c, tmp_path)
    assert len(attempts) == 2
    assert len(mapping.mappings) == 2 and mapping.mappings[0].usage != mapping.mappings[1].usage


def test_unused_asset_requires_reason_and_unaccounted_asset_fails(tmp_path):
    pp, sp, reqs, reg, uir = _setup_artifacts(tmp_path)
    unaccounted = {"mappings": [], "unused_assets": []}
    explained = {
        "mappings": [],
        "unused_assets": [{"asset_id": "A001", "reason": "热像图分辨率方向与页面布局不匹配，暂不使用"}],
    }
    c = FakeClient([unaccounted, explained])
    mapping, attempts = run_stage_e(pp, sp, reqs, reg, uir, c, tmp_path)
    assert len(attempts) == 2
    assert mapping.unused_assets[0].asset_id == "A001"

    # Both attempts bad -> raises LLMOutputError
    c2 = FakeClient([unaccounted, unaccounted])
    with pytest.raises(LLMOutputError):
        run_stage_e(pp, sp, reqs, reg, uir, c2, tmp_path)


def test_mapping_accepts_provided_user_input_and_vision_check_rejects_irrelevant(tmp_path):
    pp, sp, reqs, reg, uir = _setup_artifacts(tmp_path, with_provided_u001=True)
    c = FakeClient([
        {
            "mappings": [
                {"page_id": "S001", "asset_id": "A001", "usage": "置于 S001 右侧展示热像图分布特征", "accepted": True},
                {"page_id": "S002", "asset_id": "A002", "request_id": "U001", "usage": "置于 S002 展示热岛成因机制图", "accepted": True},
            ],
            "unused_assets": [],
        }
    ])
    v = FakeClient([
        {"relevance": "relevant", "reason": "热像图与城市热岛温度分布高度匹配"},
        {"relevance": "irrelevant", "reason": "图片实际内容为无关纯色块，并非成因机制图"},
    ])
    mapping, _ = run_stage_e(pp, sp, reqs, reg, uir, c, tmp_path, vision_client=v)
    by_aid = {m.asset_id: m for m in mapping.mappings}
    assert by_aid["A001"].accepted is True and by_aid["A001"].relevance == "relevant"
    assert by_aid["A002"].accepted is False and by_aid["A002"].relevance == "irrelevant"
    assert by_aid["A002"].request_id == "U001"
    assert any(u.asset_id == "A002" and "visual relevance" in u.reason for u in mapping.unused_assets)


def test_vision_client_from_env_checks_config():
    assert vision_client_from_env({}) is None
    with pytest.raises(LLMConfigError):
        vision_client_from_env({"PPTV01_E_VISION_MODEL": "gpt-4o"})


def test_runner_integrates_stage_e_with_real_acquired_asset(tmp_path):
    from pptv01.d_retrieve import services_from_env
    sv = services_from_env(
        {"PPTV01_ASSETS": "auto", "PPTV01_RELEVANCE": "structural", "PPTV01_PIXEL_RULES": "off"},
        transport=httpx.MockTransport(Net()),
        openalex_fetch=lambda q, n: rp("openalex_uhi.json")[:n],
    )
    e_resp = {
        "mappings": [
            {"page_id": "S001", "asset_id": "A001", "usage": "置于 S001 右侧展示代表性实景参考图", "accepted": True},
        ],
        "unused_assets": [],
    }
    # Force GOOD_C to have target Parthenon so D acquires A001
    good_c = json.loads(json.dumps(GOOD_C))
    good_c["requirements"][0]["targets"] = ["帕特农神庙"]
    good_c["requirements"][0]["query_hints"] = ["Parthenon"]
    good_b = json.loads(json.dumps(GOOD_B))
    good_b["pages"][0]["asset_needs"][0]["targets"] = [{"name": "帕特农神庙", "entity_type": "building", "verification": "verified", "source_refs": ["R001"]}]

    # Inject R001 via FakeResearch? Easier: run without R001 by using to_verify target
    good_b["pages"][0]["asset_needs"][0]["targets"][0]["verification"] = "to_verify"
    del good_b["pages"][0]["asset_needs"][0]["targets"][0]["source_refs"]

    report, run_dir = run_pipeline(
        "帮我做一个城市热岛效应的 PPT。",
        tmp_path,
        FakeClient([GOOD_A, good_b, good_c, e_resp, GOOD_TH]),
        run_id="t-e",
        assets=sv,
    )
    st = {s.stage: s for s in report.stages}
    assert st["E"].status is StageStatus.done and "slide_asset_mapping.json" in st["E"].output_files
    m = load_contract(SlideAssetMapping, run_dir / "slide_asset_mapping.json")
    assert m.mappings[0].asset_id == "A001" and m.mappings[0].relevance == "not_assessed"
