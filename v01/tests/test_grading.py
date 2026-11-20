"""PPTEval grader: prompts verbatim + attributed, scoring flow, backends (api via MockTransport, agent via files), gates."""
import json
from pathlib import Path

import httpx
import pytest

from pptv01.contracts import (GateReport, GradeReport, GraderIdentity, ProductionPlan, SlidePlan,
                              write_contract)
from pptv01.grading import ppteval as pe
from pptv01.grading.gates import check_gates
from pptv01.llm import AGENT_MODEL_ID, AwaitingAgentResponse, LLMOutputError
from tests.conftest import GOOD_A, GOOD_B
from tests.fakes import FakeClient

RID = "20260101-000000-abcd"


def plan():
    return SlidePlan(run_id=RID, status="provisional", narrative=GOOD_B["narrative"], pages=[
        {"page_id": f"S{i:03d}", "status": "provisional", **p} for i, p in enumerate(GOOD_B["pages"], 1)])


def fake_grader(scores):
    c = FakeClient(scores)
    return pe.Grader(c, c, GraderIdentity(kind="agent", label=AGENT_MODEL_ID)), c


def ok(score, reason="r"):
    return {"reason": reason, "score": score}


# ---------------------------------------------------------------- prompts / attribution
def test_prompts_are_verbatim_pptagent_and_attributed():
    d = pe.PROMPTS
    for n in ("ppteval_content", "ppteval_style", "ppteval_coherence", "ppteval_extract",
              "ppteval_describe_content", "ppteval_describe_style"):
        assert (d / f"{n}.txt").read_text("utf-8").strip()
    assert "{{descr}}" in (d / "ppteval_content.txt").read_text("utf-8")
    assert "{{presentation}}" in (d / "ppteval_coherence.txt").read_text("utf-8")
    assert "MIT License" in (d / "LICENSE-PPTAgent.txt").read_text("utf-8") and "ICIP-CAS" in (d / "LICENSE-PPTAgent.txt").read_text("utf-8")
    assert "VERBATIM" in (d / "README.txt").read_text("utf-8")
    assert pe.RUBRIC.license == "MIT" and "icip-cas/PPTAgent" in pe.RUBRIC.source_url


# ---------------------------------------------------------------- plan-text proxy
def test_plan_proxy_scores_content_and_coherence_and_never_design():
    g, c = fake_grader([ok(4), ok(3), {"slide_descriptions": ["a", "b"], "background": {}}, ok(4)])
    rep, trace = pe.grade_deck(pe.slide_text_from_plan(plan()), g, run_id=RID, level="plan_text_proxy")
    GradeReport.model_validate(json.loads(rep.model_dump_json()))
    assert [s.content.score for s in rep.slides] == [4, 3]
    assert all(s.design is None and s.design_status == "not_assessable" for s in rep.slides)
    sm = rep.summary
    assert (sm.content_mean, sm.design_mean, sm.coherence) == (3.5, None, 4)
    assert sm.not_assessed == ["design"] and sm.passed is None          # undetermined, NOT passed
    assert rep.grader.label == AGENT_MODEL_ID and rep.evidence_level == "plan_text_proxy"
    assert "Title: 什么是城市热岛" in c.calls[0][0]["content"]            # real plan text reached the PPTEval content prompt
    assert "You are an unbiased presentation analysis judge" in c.calls[0][0]["content"]


def test_failing_dimension_fails_even_if_design_undetermined():
    g, _ = fake_grader([ok(2), ok(2), {"slide_descriptions": [], "background": {}}, ok(5)])
    rep, _ = pe.grade_deck(pe.slide_text_from_plan(plan()), g, run_id=RID, level="plan_text_proxy")
    assert rep.summary.failing == ["content"] and rep.summary.passed is False


def test_proxy_report_cannot_claim_design_score():
    g, _ = fake_grader([ok(4), ok(4), {"slide_descriptions": [], "background": {}}, ok(4)])
    rep, _ = pe.grade_deck(pe.slide_text_from_plan(plan()), g, run_id=RID, level="plan_text_proxy")
    d = json.loads(rep.model_dump_json())
    d["slides"][0]["design"] = {"score": 5, "reason": "x"}
    with pytest.raises(ValueError):
        GradeReport.model_validate(d)


# ---------------------------------------------------------------- rendered evidence flow
def test_pptx_render_flow_describes_image_then_scores_description(tmp_path):
    img = tmp_path / "slide_001.png"
    img.write_bytes(b"\x89PNG\r\n")
    sent = []

    class V(FakeClient):
        def chat(self, messages):
            sent.append(messages)
            return "a description"
    text = FakeClient([ok(5), ok(4), {"slide_descriptions": ["x"], "background": {"speaker": "a"}}, ok(5)])
    g = pe.Grader(text, V([]), GraderIdentity(kind="agent", label=AGENT_MODEL_ID))
    ev = pe.slide_text_from_plan(plan())[:1]
    ev[0].image = img
    rep, _ = pe.grade_deck(ev, g, run_id=RID, level="pptx_render")
    assert [m[0]["images"] for m in sent] == [[str(img)], [str(img)]]       # content + style description calls
    assert "visual appeal" in text.calls[0][0]["content"] and "a description" in text.calls[0][0]["content"]
    s = rep.slides[0]
    assert (s.design.score, s.content.score, s.design_status) == (5, 4, "scored")
    assert rep.summary.passed is True and rep.background_extracted == {"speaker": "a"}


def test_pptx_render_requires_images():
    g, _ = fake_grader([])
    with pytest.raises(ValueError):
        pe.grade_deck(pe.slide_text_from_plan(plan()), g, run_id=RID, level="pptx_render")


def test_bad_judge_output_gets_one_repair_then_fails_loudly():
    g, c = fake_grader([{"reason": "x", "score": 9}, {"reason": "x", "score": 4}])
    pe._judge(g.text, "p", "s", [])
    assert len(c.calls) == 2
    g, c = fake_grader([{"score": "high"}, {"score": 2.5}])
    with pytest.raises(LLMOutputError):
        pe._judge(g.text, "p", "s", [])


# ---------------------------------------------------------------- backends
def test_unconfigured_api_grader_fails_loudly_no_mock():
    with pytest.raises(pe.GraderConfigError) as e:
        pe.grader_from_env({})
    assert "PPTV01_GRADER_BASE_URL" in str(e.value) and "made-up" in str(e.value)
    with pytest.raises(pe.GraderConfigError):
        pe.grader_from_env({"PPTV01_GRADER_BACKEND": "agent"})
    with pytest.raises(pe.GraderConfigError):
        pe.grader_from_env({"PPTV01_GRADER_BACKEND": "gpt"})


def test_api_grader_posts_images_as_data_urls_and_hides_key(tmp_path):
    seen = {}

    def handler(req: httpx.Request):
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "desc"}}]})
    env = {"PPTV01_GRADER_BASE_URL": "https://g.example/v1", "PPTV01_GRADER_API_KEY": "sk-secret",
           "PPTV01_GRADER_MODEL": "judge-1", "PPTV01_GRADER_VISION_MODEL": "vlm-1"}
    g = pe.grader_from_env(env, transport=httpx.MockTransport(handler))
    img = tmp_path / "s.png"
    img.write_bytes(b"abc")
    out = g.vision.chat([{"role": "user", "content": "describe", "images": [str(img)]}])
    assert out == "desc" and seen["body"]["model"] == "vlm-1"
    part = seen["body"]["messages"][0]["content"][1]
    assert part["type"] == "image_url" and part["image_url"]["url"].startswith("data:image/png;base64,")
    assert g.identity.kind == "api" and g.identity.model == "judge-1" and "sk-secret" not in g.identity.model_dump_json()


def test_agent_grader_file_exchange_is_labelled_and_awaits(tmp_path):
    g = pe.grader_from_env({"PPTV01_GRADER_BACKEND": "agent", "PPTV01_GRADER_EXCHANGE_DIR": str(tmp_path)})
    assert g.identity.kind == "agent" and "manual, not an API model" in g.identity.label
    with pytest.raises(AwaitingAgentResponse):
        pe.grade_deck(pe.slide_text_from_plan(plan()), g, run_id=RID, level="plan_text_proxy")
    req = json.loads((tmp_path / "content_s001_request_1.json").read_text("utf-8"))
    assert "Title: 什么是城市热岛" in req["messages"][0]["content"]
    (tmp_path / "content_s001_response_1.json").write_text(json.dumps(ok(4)), "utf-8")
    with pytest.raises(AwaitingAgentResponse) as e:           # advances to the next slide, then waits again
        pe.grade_deck(pe.slide_text_from_plan(plan()), g, run_id=RID, level="plan_text_proxy")
    assert "content_s002_response_1" in str(e.value)


def test_identity_validation():
    with pytest.raises(ValueError):
        GraderIdentity(kind="agent", label="gpt-4")
    with pytest.raises(ValueError):
        GraderIdentity(kind="api", label="api:x")


# ---------------------------------------------------------------- hard gates (separate from scores)
def make_run(tmp_path, utterance="帮我做一个城市热岛效应的 PPT。", n_pages=2):
    pp = ProductionPlan(run_id=RID, source_utterance=utterance, **GOOD_A)
    write_contract(pp, tmp_path / "production_plan.json")
    pages = [{"page_id": f"S{i:03d}", "status": "provisional", **GOOD_B["pages"][i % 2]} for i in range(1, n_pages + 1)]
    write_contract(SlidePlan(run_id=RID, status="provisional", narrative="n", pages=pages), tmp_path / "slide_plan.json")
    return tmp_path


def test_gates_report_not_run_for_missing_stages_never_pass(tmp_path):
    rep = check_gates(make_run(tmp_path))
    GateReport.model_validate(json.loads(rep.model_dump_json()))
    by = {r.gate: r for r in rep.results}
    assert by["contract:production_plan"].status == "pass" and by["contract:slide_plan"].status == "pass"
    assert by["contract:asset_registry"].status == "not_run" and by["pptx_opens"].status == "not_run"
    assert rep.overall == "incomplete"


def test_gate_page_limit_hard_constraint(tmp_path):
    rep = check_gates(make_run(tmp_path, "做一个城市热岛的 PPT，不超过 1 页", n_pages=2))
    assert {r.gate: r for r in rep.results}["page_constraint"].status == "fail" and rep.overall == "fail"
    d = tmp_path / "ok"
    d.mkdir()
    rep = check_gates(make_run(d, "做一个城市热岛的 PPT，不超过 5 页", n_pages=2))
    assert {r.gate: r for r in rep.results}["page_constraint"].status == "pass"


def test_gate_detects_invalid_contract_and_mixed_run_ids(tmp_path):
    run = make_run(tmp_path)
    (run / "slide_plan.json").write_text("{}", "utf-8")
    assert {r.gate: r for r in check_gates(run).results}["contract:slide_plan"].status == "fail"
    run = make_run(tmp_path / "x") if (tmp_path / "x").mkdir() is None else None
    sp = json.loads((run / "slide_plan.json").read_text("utf-8"))
    sp["run_id"] = "other-run"
    (run / "slide_plan.json").write_text(json.dumps(sp), "utf-8")
    assert {r.gate: r for r in check_gates(run).results}["run_id_consistent"].status == "fail"


def test_gate_report_overall_must_match_results():
    from pptv01.contracts import GateResult
    with pytest.raises(ValueError):
        GateReport(run_id=RID, results=[GateResult(gate="x", status="fail")], overall="pass")
