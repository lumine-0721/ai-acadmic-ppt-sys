"""R6-4 tests: PixelFilter (CLIP + RapidOCR), candidate ranking, degraded propagation, and no_text_only_visuals gate."""

from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import httpx
import pytest
from PIL import Image, ImageDraw
from pptx import Presentation

from pptv01.contracts import (
    AssetRegistry,
    AssetRequirements,
    AssetStatus,
    ProductionPlan,
    SlidePlan,
    load_contract,
    write_contract,
)
from pptv01.d_retrieve import pixel_rules as px_mod
from pptv01.d_retrieve.pixel_rules import (
    CLIP_NEG_REJECT_MARGIN,
    CLIP_NEGATIVE_PROMPTS,
    OCR_TEXT_AREA_RATIO_MAX,
    ClipScoreResult,
    PixelFilter,
    is_clip_cached,
)
from pptv01.d_retrieve.stage import run_stage_d, services_from_env
from pptv01.grading.gates import _check_no_text_only_visuals_gate, check_gates
from pptv01.h_qa.stage import run_stage_h
from tests.conftest import GOOD_A, GOOD_B

V01_ROOT = Path(__file__).resolve().parents[1]
PIX_FIXTURES = V01_ROOT / "tests" / "fixtures" / "r6_pixel_images"
RID = "20261121-000000-r6p4"


def _png_bytes(tag: int, size: tuple[int, int] = (200, 120)) -> bytes:
    """Create valid deterministic JPEG bytes whose content varies by tag."""
    im = Image.new("RGB", size, (tag % 250, (tag * 37) % 250, (tag * 73) % 250))
    draw = ImageDraw.Draw(im)
    draw.rectangle([10, 10, 80, 60], fill=(255 - tag % 200, 180, 90))
    buf = io.BytesIO()
    im.save(buf, "JPEG")
    return buf.getvalue()


def _commons_page(title: str, url: str, mime: str = "image/jpeg") -> dict:
    return {
        "pageid": abs(hash(title)) % 900000 + 1000,
        "ns": 6,
        "title": title,
        "index": 1,
        "imageinfo": [
            {
                "url": url,
                "thumburl": url,
                "descriptionurl": f"https://commons.wikimedia.org/wiki/{title.replace(' ', '_')}",
                "width": 1280,
                "height": 832,
                "mime": mime,
                "extmetadata": {
                    "LicenseShortName": {"value": "CC BY 4.0"},
                    "LicenseUrl": {"value": "https://creativecommons.org/licenses/by/4.0"},
                    "Artist": {"value": "Photographer"},
                    "ImageDescription": {"value": f"Photograph showing {title}"},
                    "Categories": {"value": "Photographs|Architecture"},
                },
            }
        ],
    }


def _make_plan_sp(topic: str = "Urban Green Infrastructure") -> tuple[ProductionPlan, SlidePlan]:
    pp = ProductionPlan(run_id=RID, source_utterance=topic, **{**GOOD_A, "topic": topic})
    sp = SlidePlan(
        run_id=RID,
        status="provisional",
        narrative="n",
        pages=[{"page_id": f"S{i:03d}", "status": "provisional", **p} for i, p in enumerate(GOOD_B["pages"], 1)],
    )
    return pp, sp


def _make_img_req(aid: str, target_name: str, hint: str, purpose: str) -> dict:
    return {
        "asset_id": aid,
        "page_ids": ["S001"],
        "need_refs": ["S001#1"],
        "type": "image",
        "purpose": purpose,
        "targets": [{"name": target_name, "entity_type": "concept", "verification": "to_verify"}],
        "query_hints": [hint],
        "priority": "required",
        "acceptance": ["real scene photo with clear license"],
    }


def test_r6_4_offline_signage_rejected_and_falls_through_to_clean_photo(tmp_path):
    """Candidate with signage negative CLIP match & high OCR ratio is rejected; clean photo is chosen."""
    sign_bytes = _png_bytes(11)
    good_bytes = _png_bytes(77)

    p1 = _commons_page("File:Sponge City Sign.jpg", "https://upload.wikimedia.org/sign.jpg")
    p2 = _commons_page("File:Sponge City Rain Garden.jpg", "https://upload.wikimedia.org/rain_garden.jpg")

    class FakeClip:
        def score_image(self, image_bytes: bytes, positive_prompt: str, negative_prompts=CLIP_NEGATIVE_PROMPTS):
            assert positive_prompt.startswith("a photograph of")
            if image_bytes == sign_bytes:
                return ClipScoreResult(
                    positive_score=0.18,
                    negative_scores={"a sign with text": 0.30, "a logo": 0.21},
                    max_negative_label="a sign with text",
                    max_negative_score=0.30,
                )
            return ClipScoreResult(
                positive_score=0.31,
                negative_scores={"a sign with text": 0.19, "a logo": 0.17},
                max_negative_label="a sign with text",
                max_negative_score=0.19,
            )

    def fake_ocr(image_bytes: bytes) -> float:
        return 0.16 if image_bytes == sign_bytes else 0.01

    def handler(req_: httpx.Request) -> httpx.Response:
        if req_.url.host == "www.wikidata.org":
            return httpx.Response(200, json={"search": []})
        if req_.url.host == "commons.wikimedia.org":
            return httpx.Response(200, json={"query": {"pages": [p1, p2]}})
        if req_.url.path.endswith("/sign.jpg"):
            return httpx.Response(200, content=sign_bytes, headers={"content-type": "image/jpeg"})
        if req_.url.path.endswith("/rain_garden.jpg"):
            return httpx.Response(200, content=good_bytes, headers={"content-type": "image/jpeg"})
        return httpx.Response(404)

    pf = PixelFilter(mode="auto", clip_scorer=FakeClip(), ocr_runner=fake_ocr)
    sv = services_from_env(
        {"PPTV01_ASSETS": "auto", "PPTV01_RELEVANCE": "structural", "PPTV01_PIXEL_RULES": "off"},
        transport=httpx.MockTransport(handler),
        pixel_filter=pf,
    )
    pp, sp = _make_plan_sp("Sponge city")
    reqs = AssetRequirements(
        run_id=RID,
        requirements=[_make_img_req("A001", "Sponge City", "Sponge City rain garden", "Show sponge city green facility")],
    )
    reg = run_stage_d(pp, sp, reqs, tmp_path, sv)
    a = reg.assets[0]
    assert a.status is AssetStatus.acquired
    assert a.identifier == "File:Sponge City Rain Garden.jpg"
    assert a.checks.quality and "pixel_rules passed" in a.checks.quality
    assert "clip_pos=0.310" in a.checks.quality
    sign_cand = next(c for c in a.candidates if c.title == "File:Sponge City Sign.jpg")
    assert sign_cand.decision == "rejected"
    assert "a sign with text" in sign_cand.reason
    assert "ocr_ratio=0.160" in sign_cand.reason


def test_r6_4_offline_clip_positive_score_ranks_passing_candidates(tmp_path):
    """When multiple candidates pass pixel checks, CLIP positive score determines the selected photo."""
    low_bytes = _png_bytes(21)
    best_bytes = _png_bytes(88)
    mid_bytes = _png_bytes(44)

    p1 = _commons_page("File:Parthenon Low.jpg", "https://upload.wikimedia.org/p1.jpg")
    p2 = _commons_page("File:Parthenon Best.jpg", "https://upload.wikimedia.org/p2.jpg")
    p3 = _commons_page("File:Parthenon Mid.jpg", "https://upload.wikimedia.org/p3.jpg")

    scores_by_bytes = {low_bytes: 0.23, best_bytes: 0.39, mid_bytes: 0.29}

    class FakeClip:
        def score_image(self, image_bytes: bytes, positive_prompt: str, negative_prompts=CLIP_NEGATIVE_PROMPTS):
            pos = scores_by_bytes[image_bytes]
            return ClipScoreResult(
                positive_score=pos,
                negative_scores={"a sign with text": 0.15},
                max_negative_label="a sign with text",
                max_negative_score=0.15,
            )

    def handler(req_: httpx.Request) -> httpx.Response:
        if req_.url.host == "www.wikidata.org":
            return httpx.Response(200, json={"search": []})
        if req_.url.host == "commons.wikimedia.org":
            return httpx.Response(200, json={"query": {"pages": [p1, p2, p3]}})
        mapping = {"/p1.jpg": low_bytes, "/p2.jpg": best_bytes, "/p3.jpg": mid_bytes}
        if req_.url.path in mapping:
            return httpx.Response(200, content=mapping[req_.url.path], headers={"content-type": "image/jpeg"})
        return httpx.Response(404)

    pf = PixelFilter(mode="auto", clip_scorer=FakeClip(), ocr_runner=lambda _b: 0.0)
    sv = services_from_env(
        {"PPTV01_ASSETS": "auto", "PPTV01_RELEVANCE": "structural", "PPTV01_PIXEL_RULES": "off"},
        transport=httpx.MockTransport(handler),
        pixel_filter=pf,
    )
    pp, sp = _make_plan_sp("Parthenon")
    reqs = AssetRequirements(
        run_id=RID,
        requirements=[_make_img_req("A001", "Parthenon", "Parthenon Athens", "Show Parthenon architecture")],
    )
    reg = run_stage_d(pp, sp, reqs, tmp_path, sv)
    a = reg.assets[0]
    assert a.identifier == "File:Parthenon Best.jpg"
    low_cand = next(c for c in a.candidates if c.title == "File:Parthenon Low.jpg")
    assert low_cand.decision == "rejected"
    assert "ranked below selected candidate by CLIP positive score" in low_cand.reason


def test_r6_4_offline_ocr_and_clip_exempt_for_diagram_chart_table():
    """Diagrams, charts, and tables are exempt from photo signage/OCR rejection."""

    class SignageClip:
        def score_image(self, image_bytes: bytes, positive_prompt: str, negative_prompts=CLIP_NEGATIVE_PROMPTS):
            return ClipScoreResult(0.10, {"a diagram": 0.35}, "a screenshot of text", 0.35)

    pf = PixelFilter(mode="auto", clip_scorer=SignageClip(), ocr_runner=lambda _b: 0.85)
    img = _png_bytes(5)
    for exempt_type in ("diagram", "chart", "chart_data", "table"):
        v = pf.assess_image(img, exempt_type)
        assert v.accepted is True
        assert v.exempt_ocr is True


def test_r6_4_offline_degradation_propagates_to_logs_registry_and_qa_report(tmp_path, monkeypatch):
    """CLIP timeout & missing RapidOCR never crash or hide; degraded status is recorded in D and H."""

    def boom_clip(**_kwargs):
        raise TimeoutError("CLIP load timed out after 0.1s")

    def boom_ocr(**_kwargs):
        raise ImportError("rapidocr_onnxruntime not installed")

    monkeypatch.setattr(px_mod, "FastembedClipScorer", boom_clip)
    monkeypatch.setattr(px_mod, "RapidOcrAreaRunner", boom_ocr)

    logs: list[str] = []
    img_bytes = _png_bytes(33)
    page = _commons_page("File:Parthenon Facade.jpg", "https://upload.wikimedia.org/parthenon.jpg")

    def handler(req_: httpx.Request) -> httpx.Response:
        if req_.url.host == "www.wikidata.org":
            return httpx.Response(200, json={"search": []})
        if req_.url.host == "commons.wikimedia.org":
            return httpx.Response(200, json={"query": {"pages": [page]}})
        if req_.url.path.endswith("/parthenon.jpg"):
            return httpx.Response(200, content=img_bytes, headers={"content-type": "image/jpeg"})
        return httpx.Response(404)

    sv = services_from_env(
        {"PPTV01_ASSETS": "auto", "PPTV01_RELEVANCE": "structural", "PPTV01_PIXEL_RULES": "auto"},
        transport=httpx.MockTransport(handler),
        progress_fn=logs.append,
    )
    assert sv.pixel_filter is not None and "pixel_rules: degraded" in sv.pixel_filter.degraded_note

    pp, sp = _make_plan_sp("Parthenon")
    reqs = AssetRequirements(
        run_id=RID,
        requirements=[_make_img_req("A001", "Parthenon", "Parthenon facade", "Show Parthenon facade")],
    )
    reg = run_stage_d(pp, sp, reqs, tmp_path, sv)
    assert reg.assets[0].status is AssetStatus.acquired
    assert any("pixel_rules: degraded" in line for line in logs)
    assert reg.relevance_filter and "pixel_rules: degraded" in reg.relevance_filter.note
    assert "pixel_rules: degraded" in reg.summary

    # Stage H consumes asset_registry.json and surfaces the degraded pixel_rules note in QAReport
    prs = Presentation()
    prs.slides.add_slide(prs.slide_layouts[6])
    pptx_path = tmp_path / "deck.pptx"
    prs.save(str(pptx_path))

    def _stub_render(_pptx: Path, out_dir: Path) -> list[Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        p = out_dir / "slide_001.png"
        p.write_bytes(_png_bytes(1))
        return [p]

    qa_report, _ = run_stage_h(pptx_path, sp, tmp_path, reg=reg, render_fn=_stub_render)
    assert any("pixel_rules: degraded" in d for d in qa_report.diagnostics)
    assert any("pixel_rules: degraded" in n for n in qa_report.cross_render_notes)
    assert "pixel_rules: degraded" in qa_report.summary

    # Gate also warns cleanly on degraded pixel_rules without failing overall
    gate_res = _check_no_text_only_visuals_gate(tmp_path, reg, ocr_runner=lambda _b: 0.0)
    assert gate_res.status == "warn"
    assert "pixel_rules: degraded" in gate_res.detail


def _write_fixture_registry(run_dir: Path, img_name: str, src_path: Path, aid: str = "A001") -> AssetRegistry:
    (run_dir / "assets").mkdir(parents=True, exist_ok=True)
    rel = f"assets/{img_name}"
    shutil.copy2(src_path, run_dir / rel)
    reg = AssetRegistry(
        run_id=RID,
        assets=[
            {
                "asset_id": aid,
                "status": "acquired",
                "type": "image",
                "file_path": rel,
                "source_url": f"https://commons.wikimedia.org/wiki/File:{img_name}",
                "source_name": "Wikimedia Commons",
                "license_or_terms": "CC BY 4.0",
                "author": "Fixture Author",
                "attribution": f"Fixture Author, CC BY 4.0, via Wikimedia Commons (File:{img_name})",
                "identifier": f"File:{img_name}",
                "sha256": "a" * 64,
                "mime": "image/png" if img_name.endswith(".png") else "image/jpeg",
                "width": 1280,
                "height": 832,
                "retrieved_at": "2026-11-21T00:00:00+08:00",
                "checks": {"quality": "not assessed in D"},
            }
        ],
    )
    write_contract(reg, run_dir / "asset_registry.json")
    return load_contract(AssetRegistry, run_dir / "asset_registry.json")


@pytest.mark.vision_model
def test_r6_4_no_text_only_visuals_gate_distinguishes_sign_from_real_photo(tmp_path):
    """no_text_only_visuals warn gate flags Sponge_City.png via real RapidOCR and passes coffee_pourover.jpg."""
    pytest.importorskip("rapidocr_onnxruntime", reason="rapidocr_onnxruntime ([vision] extra) not installed")
    bad_dir = tmp_path / "bad"
    good_dir = tmp_path / "good"
    _write_fixture_registry(bad_dir, "Sponge_City.png", PIX_FIXTURES / "Sponge_City.png")
    _write_fixture_registry(good_dir, "coffee_pourover.jpg", PIX_FIXTURES / "coffee_pourover.jpg")

    bad_rep = check_gates(bad_dir)
    bad_gate = next(r for r in bad_rep.results if r.gate == "no_text_only_visuals")
    assert bad_gate.status == "warn"
    assert "ocr_ratio=" in bad_gate.detail

    good_rep = check_gates(good_dir)
    good_gate = next(r for r in good_rep.results if r.gate == "no_text_only_visuals")
    assert good_gate.status == "pass"


def test_r7_4_no_text_only_visuals_gate_mocked_ocr_and_degraded_without_rapidocr(tmp_path, monkeypatch):
    """Offline deterministic checks: mock OCR flags sign/passes photo; missing RapidOCR warns degraded (never pass)."""
    from pptv01.contracts import load_contract
    from pptv01.grading import gates as gates_mod

    bad_dir = tmp_path / "bad"
    good_dir = tmp_path / "good"
    _write_fixture_registry(bad_dir, "Sponge_City.png", PIX_FIXTURES / "Sponge_City.png")
    _write_fixture_registry(good_dir, "coffee_pourover.jpg", PIX_FIXTURES / "coffee_pourover.jpg")

    bad_reg = load_contract(AssetRegistry, bad_dir / "asset_registry.json")
    good_reg = load_contract(AssetRegistry, good_dir / "asset_registry.json")
    sign_gate = gates_mod._check_no_text_only_visuals_gate(bad_dir, bad_reg, None, ocr_runner=lambda _b: 0.19)
    assert sign_gate.status == "warn" and "ocr_ratio=0.190" in sign_gate.detail

    photo_gate = gates_mod._check_no_text_only_visuals_gate(good_dir, good_reg, None, ocr_runner=lambda _b: 0.01)
    assert photo_gate.status == "pass" and "checked cleanly" in photo_gate.detail

    # Simulate uninstalled rapidocr_onnxruntime: gate MUST return warn + degraded message, never pass
    monkeypatch.setattr(gates_mod, "_get_gate_ocr_runner", lambda: None)
    rep = check_gates(good_dir)
    deg_gate = next(r for r in rep.results if r.gate == "no_text_only_visuals")
    assert deg_gate.status == "warn"
    assert "pixel_rules: degraded (rapidocr not installed" in deg_gate.detail
    assert "1/1 image visual(s) unverified" in deg_gate.detail
    assert "checked cleanly" not in deg_gate.detail


@pytest.mark.vision_model
def test_r6_4_real_clip_and_rapidocr_reject_sponge_city_and_pass_real_photos():
    """Calibrated CLIP + RapidOCR model test on committed Wikimedia fixtures (skips cleanly if uncached)."""
    pytest.importorskip("rapidocr_onnxruntime", reason="rapidocr_onnxruntime ([vision] extra) not installed")
    if not is_clip_cached():
        pytest.skip("CLIP ViT-B-32 models are not cached in fastembed cache directory; skipping offline")

    pf = PixelFilter.from_env({"PPTV01_PIXEL_RULES": "auto"})
    assert not pf.degraded_note, f"expected healthy vision models, got {pf.degraded_note}"
    assert abs(pf.clip_neg_margin - CLIP_NEG_REJECT_MARGIN) < 1e-9
    assert abs(pf.ocr_area_max - OCR_TEXT_AREA_RATIO_MAX) < 1e-9

    sponge_verdict = pf.assess_file(
        PIX_FIXTURES / "Sponge_City.png",
        "sponge city urban runoff green infrastructure",
    )
    assert sponge_verdict.accepted is False
    assert sponge_verdict.clip_max_negative_label == "a sign with text"
    assert sponge_verdict.ocr_area_ratio is not None and sponge_verdict.ocr_area_ratio > OCR_TEXT_AREA_RATIO_MAX

    positive_cases = [
        ("coffee_pourover.jpg", "coffee pour over brewing"),
        ("parthenon.jpg", "Parthenon temple Athens Greece"),
        ("cafe_de_flore.jpg", "Cafe de Flore Paris street cafe"),
    ]
    for filename, prompt in positive_cases:
        verdict = pf.assess_file(PIX_FIXTURES / filename, prompt)
        assert verdict.accepted is True, f"{filename} unexpectedly rejected: {verdict}"
        assert verdict.ocr_area_ratio is not None and verdict.ocr_area_ratio <= OCR_TEXT_AREA_RATIO_MAX
