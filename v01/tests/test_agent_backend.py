import json

import pytest

from conftest import GOOD_A, GOOD_B, GOOD_C, GOOD_TH
from pptv01 import cli
from pptv01.contracts import RunReport, StageStatus, load_contract
from pptv01.llm import AGENT_MODEL_ID, AgentClient, AwaitingAgentResponse, LLMConfigError, client_from_env
from pptv01.runner import run_pipeline

U = "帮我做一个城市热岛效应的 PPT。"


def put(d, stage, n, obj):
    (d / f"{stage}_response_{n}.json").write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def test_factory_selects_backend_and_validates_env(tmp_path):
    assert isinstance(client_from_env({"PPTV01_LLM_BACKEND": "agent", "PPTV01_AGENT_RESPONSES_DIR": str(tmp_path)}), AgentClient)
    with pytest.raises(LLMConfigError, match="PPTV01_AGENT_RESPONSES_DIR"):
        client_from_env({"PPTV01_LLM_BACKEND": "agent"})
    with pytest.raises(LLMConfigError, match="unknown"):
        client_from_env({"PPTV01_LLM_BACKEND": "mock"})
    with pytest.raises(LLMConfigError, match="PPTV01_LLM_API_KEY"):   # default backend stays 'api'
        client_from_env({})


def test_agent_writes_request_and_awaits(tmp_path):
    c = AgentClient(tmp_path)
    c.set_stage("A")
    with pytest.raises(AwaitingAgentResponse) as e:
        c.chat([{"role": "user", "content": "hi"}])
    req = json.loads((tmp_path / "A_request_1.json").read_text(encoding="utf-8"))
    assert req["stage"] == "A" and req["n"] == 1 and req["messages"][0]["content"] == "hi"
    assert "A_response_1.json" in str(e.value)
    put(tmp_path, "A", 1, {"x": 1})
    c.set_stage("A")   # new stage visit restarts numbering
    assert json.loads(c.chat([])) == {"x": 1}
    assert c.model_id == AGENT_MODEL_ID and "not an API model" in AGENT_MODEL_ID


def test_pipeline_with_agent_answers_goes_through_same_checks(tmp_path):
    io = tmp_path / "io"; io.mkdir()
    c = AgentClient(io)
    # nothing answered yet -> awaiting, no report, no fake output
    with pytest.raises(AwaitingAgentResponse):
        run_pipeline(U, tmp_path, c, run_id="ag-1")
    assert not (tmp_path / "ag-1" / "run_report.json").exists()
    # first answer violates the A post-check (invented page count) -> repair retry needs response_2
    put(io, "A", 1, {**GOOD_A, "content_direction": "共 10 页"})
    with pytest.raises(AwaitingAgentResponse) as e:
        run_pipeline(U, tmp_path, AgentClient(io), run_id="ag-1", resume=True)
    assert str(e.value).count("A_response_2.json") == 1
    req2 = json.loads((io / "A_request_2.json").read_text(encoding="utf-8"))
    assert "invented page count" in req2["messages"][-1]["content"]      # validation error fed back
    put(io, "A", 2, GOOD_A); put(io, "B", 1, GOOD_B); put(io, "C", 1, GOOD_C); put(io, "TH", 1, GOOD_TH)
    report, run_dir = run_pipeline(U, tmp_path, AgentClient(io), run_id="ag-1", resume=True)
    assert {s.stage: s.status for s in report.stages}["C"] is StageStatus.done
    rep = load_contract(RunReport, run_dir / "run_report.json")
    assert rep.model_id == AGENT_MODEL_ID and rep.llm_backend == "agent"
    assert rep.tool_config["llm"]["backend"] == "agent"
    trace = json.loads((run_dir / "llm_trace" / "A.json").read_text(encoding="utf-8"))
    assert [a["error"] is None for a in trace["attempts"]] == [False, True]


def test_agent_answer_failing_validation_twice_fails_the_run(tmp_path):
    io = tmp_path / "io"; io.mkdir()
    bad = {**GOOD_A, "content_direction": "共 10 页"}
    put(io, "A", 1, bad); put(io, "A", 2, bad)
    report, _ = run_pipeline(U, tmp_path, AgentClient(io), run_id="ag-2")
    assert report.overall.value == "failed"            # no bypass for the agent backend


def test_cli_agent_backend_exit_code_3_when_awaiting(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PPTV01_LLM_BACKEND", "agent")
    monkeypatch.setenv("PPTV01_AGENT_RESPONSES_DIR", str(tmp_path / "io"))
    rc = cli.main(["run", U, "--out", str(tmp_path / "runs"), "--run-id", "c1"])
    assert rc == 3 and "awaiting agent response" in capsys.readouterr().err
