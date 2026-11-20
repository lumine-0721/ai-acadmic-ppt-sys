import json
from pathlib import Path

import pytest

from conftest import GOOD_A, GOOD_B, GOOD_C
from fakes import FakeClient
from pptv01 import cli
from pptv01.contracts import ProductionPlan, RunReport, SlidePlan, StageStatus, load_contract
from pptv01.runner import run_pipeline

U = "帮我做一个城市热岛效应的 PPT。"


def test_runner_writes_files_and_honest_report(tmp_path):
    report, run_dir = run_pipeline(U, tmp_path, FakeClient([GOOD_A, GOOD_B, GOOD_C]), run_id="t-001")
    assert run_dir == tmp_path / "t-001"
    for f in ("production_plan.json", "slide_plan.json", "run_report.json", "llm_trace/A.json", "llm_trace/B.json"):
        assert (run_dir / f).is_file(), f
    load_contract(ProductionPlan, run_dir / "production_plan.json")
    assert load_contract(SlidePlan, run_dir / "slide_plan.json").status.value == "provisional"
    rep = load_contract(RunReport, run_dir / "run_report.json")
    st = {s.stage: s.status for s in rep.stages}
    assert [st[c] for c in "ABCDEFG"] == [StageStatus.done] * 7
    assert st["H"] is StageStatus.not_implemented
    assert rep.overall.value == "incomplete"          # never "complete" while H is missing
    assert rep.model_id == "fake-model-for-unit-tests"
    assert rep.code_commit and rep.started_at <= rep.finished_at
    assert rep.defaults_summary
    assert (run_dir / "deck.pptx").is_file()
    # no phantom deliverables for H
    assert not (run_dir / "qa_report.json").exists()


def test_stage_a_failure_is_reported_and_b_skipped(tmp_path):
    bad = {**GOOD_A, "content_direction": "共 10 页"}
    report, run_dir = run_pipeline(U, tmp_path, FakeClient([bad, bad]), run_id="t-002")
    st = {s.stage: s for s in report.stages}
    assert report.overall.value == "failed" and "stage A failed" in report.failure_reason
    assert st["A"].status is StageStatus.failed and st["B"].status is StageStatus.skipped
    assert not (run_dir / "slide_plan.json").exists()
    assert (run_dir / "llm_trace/A.json").is_file()
    assert (run_dir / "run_report.json").is_file()


def test_stage_b_failure_keeps_stage_a_output(tmp_path):
    bad = json.loads(json.dumps(GOOD_B)); bad["pages"][0]["key_points"] = ["数据显示 30% 更热"]
    report, run_dir = run_pipeline(U, tmp_path, FakeClient([GOOD_A, bad, bad]), run_id="t-003")
    assert report.overall.value == "failed"
    assert (run_dir / "production_plan.json").is_file() and not (run_dir / "slide_plan.json").exists()


def test_existing_run_dir_not_overwritten(tmp_path):
    run_pipeline(U, tmp_path, FakeClient([GOOD_A, GOOD_B, GOOD_C]), run_id="t-004")
    with pytest.raises(FileExistsError):
        run_pipeline(U, tmp_path, FakeClient([GOOD_A, GOOD_B, GOOD_C]), run_id="t-004")


def test_invalid_run_id(tmp_path):
    with pytest.raises(ValueError):
        run_pipeline(U, tmp_path, FakeClient([]), run_id="../x")


def test_cli_without_env_fails_clearly_and_creates_nothing(tmp_path, monkeypatch, capsys):
    for k in ("PPTV01_LLM_BASE_URL", "PPTV01_LLM_API_KEY", "PPTV01_LLM_MODEL", "PPTV01_LLM_BACKEND", "PPTV01_AGENT_RESPONSES_DIR"):
        monkeypatch.delenv(k, raising=False)
    rc = cli.main(["run", U, "--out", str(tmp_path / "runs")])
    assert rc == 2
    assert "PPTV01_LLM_API_KEY" in capsys.readouterr().err
    assert not (tmp_path / "runs").exists()


def test_cli_validate_and_export(tmp_path, capsys):
    fx = Path(__file__).resolve().parents[1] / "fixtures" / "production_plan.json"
    assert cli.main(["validate", "production_plan", str(fx)]) == 0
    assert cli.main(["validate", "slide_plan", str(fx)]) == 1
    assert cli.main(["export-schemas", "--out", str(tmp_path)]) == 0
    assert len(list(tmp_path.glob("*.schema.json"))) == 12


def test_runner_records_research_outcome_and_fails_honestly(tmp_path):
    import httpx
    from pptv01.retrieval import CrossrefBackend, LiveResearch, WikipediaBackend
    from pptv01.retrieval.backends import make_http
    http = make_http(5, "x", httpx.MockTransport(lambda r: httpx.Response(503)))
    dead = LiveResearch([WikipediaBackend(http), CrossrefBackend(http)])
    client = FakeClient([GOOD_A, GOOD_B, GOOD_C])
    report, run_dir = run_pipeline(U, tmp_path, client, run_id="t-net", research=dead)
    st = {s.stage: s for s in report.stages}
    assert report.overall.value == "failed" and st["B"].status is StageStatus.failed
    assert "research failed" in report.failure_reason and "research_brief.json" in st["B"].output_files
    assert report.tool_config["research"]["status"] == "failed"
    assert not (run_dir / "slide_plan.json").exists()


def test_runner_research_disabled_is_recorded(tmp_path):
    report, run_dir = run_pipeline(U, tmp_path, FakeClient([GOOD_A, GOOD_B, GOOD_C]), run_id="t-off")
    assert report.tool_config["research"]["status"] == "disabled"     # env PPTV01_RESEARCH=none from conftest
    assert (run_dir / "research_brief.json").is_file()
