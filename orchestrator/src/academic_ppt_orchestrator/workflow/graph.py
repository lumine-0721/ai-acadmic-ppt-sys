"""LangGraph control plane. Module nodes call adapters; edges encode dependencies."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from academic_ppt_orchestrator.adapters import apc, aprd, seaa
from academic_ppt_orchestrator.adapters.common import write_json
from academic_ppt_orchestrator.backends.plan import blocked_schema_plan, build_plan
from academic_ppt_orchestrator.backends.probe_pptx import write_probe
from academic_ppt_orchestrator.contracts.ir import build_ir
from academic_ppt_orchestrator.journal import append_event
from academic_ppt_orchestrator.qa.router import route_report
from academic_ppt_orchestrator.workflow.policies import design_conflicts, evidence_decision


def add_list(left, right):
    return list(left or []) + list(right or [])


def merge_dict(left, right):
    merged = dict(left or {})
    merged.update(right or {})
    return merged


class OrchestratorState(TypedDict, total=False):
    run_id: str
    run_dir: str
    task: dict
    budgets: dict
    cache: dict
    python: str
    cache_updates: Annotated[dict, merge_dict]
    logs: Annotated[list, add_list]
    module_runs: Annotated[list, add_list]
    artifacts: Annotated[dict, merge_dict]
    conflicts: Annotated[dict, merge_dict]
    adjustments: Annotated[list, add_list]
    apc_result: dict
    aprd_result: dict
    seaa_result: dict
    evidence_decision: dict
    iteration: int
    no_progress: bool
    handoff_path: str
    requirements_path: str
    previous_handoff_path: str
    design_package: str
    evidence_package: str
    research_bundle: str
    reference_pptx: str
    ir: dict
    ir_path: str
    plan: dict
    plan_path: str
    pptx_path: str
    qa: dict
    terminal: str
    terminal_reason: str
    fault_once: str


def _cache_update(result: dict) -> dict:
    if result.get("cache_key"):
        return {result["cache_key"]: result}
    return {}


def prepare(state: dict) -> dict:
    run_dir = Path(state["run_dir"])
    run_dir.mkdir(parents=True, exist_ok=True)
    task = state["task"]
    cache_path = task.get("cache_path")
    cache = {}
    if cache_path and Path(cache_path).is_file():
        cache = json.loads(Path(cache_path).read_text(encoding="utf-8"))
    budgets = {
        "max_iterations": 2,
        "timeout_seconds": 60,
        "retry_budget": 1,
        "max_parallel": 3,
        "allow_claim_downgrade": True,
    }
    budgets.update(task.get("budgets") or {})
    append_event(state["run_dir"], {"module_id": "orchestrator", "action": "prepare", "run_id": state["run_id"]})
    return {
        "budgets": budgets,
        "cache": cache,
        "python": task.get("python"),
        "iteration": 0,
        "no_progress": False,
        "handoff_path": task.get("handoff"),
        "requirements_path": task.get("requirements"),
        "design_package": task.get("design_package"),
        "evidence_package": task.get("evidence_package"),
        "research_bundle": task.get("research_bundle"),
        "reference_pptx": task.get("reference_pptx"),
        "fault_once": task.get("fault_once"),
    }


def node_apc(state: dict) -> dict:
    result = apc.validate(state)
    return {"apc_result": result, "module_runs": [result], "cache_updates": _cache_update(result)}


def node_aprd(state: dict) -> dict:
    result = aprd.execute(state)
    return {"aprd_result": result, "module_runs": [result], "cache_updates": _cache_update(result)}


def node_seaa(state: dict) -> dict:
    result = seaa.execute(state)
    produced = (result.get("payload") or {}).get("evidence_package")
    update = {"seaa_result": result, "module_runs": [result], "cache_updates": _cache_update(result)}
    if produced:
        update["evidence_package"] = produced
    return update


def node_gate(state: dict) -> dict:
    decision = evidence_decision(state.get("apc_result") or {}, state.get("seaa_result") or {})
    conflicts = {item["id"]: item for item in design_conflicts(state.get("apc_result") or {}, state.get("aprd_result") or {})}
    append_event(state["run_dir"], {
        "module_id": "orchestrator",
        "action": "gate",
        "evidence_gate": decision["gate"],
        "conflicts": sorted(conflicts),
    })
    return {"evidence_decision": decision, "conflicts": conflicts}


def route_after_gate(state: dict) -> str:
    decision = (state.get("evidence_decision") or {}).get("gate")
    budgets = state.get("budgets") or {}
    if (
        decision == "insufficient"
        and not state.get("no_progress")
        and int(state.get("iteration") or 0) < int(budgets.get("max_iterations", 2))
        and budgets.get("allow_claim_downgrade", True)
    ):
        return "revise"
    return "assemble"


def node_revise(state: dict) -> dict:
    claim_ids = (state.get("evidence_decision") or {}).get("claim_ids") or []
    result = apc.revise_for_insufficient_evidence(state, claim_ids)
    payload = result.get("payload") or {}
    update = {
        "apc_result": result,
        "module_runs": [result],
        "iteration": int(state.get("iteration") or 0) + 1,
        "no_progress": bool(payload.get("no_progress") or result.get("acceptance") != "accepted"),
        "adjustments": [{
            "kind": "downgrade_finding_to_hypothesis",
            "claim_ids": claim_ids,
            "acceptance": result.get("acceptance"),
        }],
    }
    if payload.get("handoff_path") and result.get("acceptance") == "accepted":
        update["handoff_path"] = payload["handoff_path"]
        update["requirements_path"] = payload["requirements_path"]
        update["previous_handoff_path"] = payload["previous_handoff_path"]
        update["no_progress"] = False
    return update


def node_assemble(state: dict) -> dict:
    marker = Path(state["run_dir"]) / "faults" / "assemble.consumed"
    if state.get("fault_once") == "assemble" and not marker.exists():
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("consumed\n", encoding="utf-8")
        append_event(state["run_dir"], {"module_id": "orchestrator", "action": "fault", "node": "assemble"})
        raise RuntimeError("injected assemble failure")
    ir = build_ir(state)
    path = Path(state["run_dir"]) / "academic_deck_ir.json"
    write_json(path, ir)
    append_event(state["run_dir"], {"module_id": "orchestrator", "action": "assemble", "status": ir["validation_state"]["status"]})
    return {"ir": ir, "ir_path": str(path)}


def node_plan(state: dict) -> dict:
    ir = state["ir"]
    schema_errors = ir["validation_state"].get("schema_errors")
    plan = blocked_schema_plan(state, ir) if schema_errors else build_plan(state, ir)
    path = Path(state["run_dir"]) / "backend_execution_plan.json"
    write_json(path, plan)
    pptx_path = None
    if not schema_errors and state["task"].get("write_probe_pptx", True):
        pptx_path = str(Path(state["run_dir"]) / "probe.pptx")
        write_probe(Path(pptx_path), state["ir"])
        plan["output_artifact_targets"] = [{
            "path": pptx_path,
            "kind": "probe_pptx",
            "production": False,
        }]
        plan["backend"]["probe"] = "python-pptx==1.0.2"
        write_json(path, plan)
    return {"plan": plan, "plan_path": str(path), "pptx_path": pptx_path}


def node_qa(state: dict) -> dict:
    routed = route_report(state["task"].get("qa_report"))
    append_event(state["run_dir"], {"module_id": "qa", "action": "route", "routes": routed["routes"]})
    return {"qa": routed}


def node_terminal(state: dict) -> dict:
    status = ((state.get("ir") or {}).get("validation_state") or {}).get("status")
    mapping = {"accepted": "SUCCEEDED", "partial": "PARTIAL", "blocked": "BLOCKED"}
    terminal = mapping.get(status, "FAILED")
    validation = ((state.get("ir") or {}).get("validation_state") or {})
    reason_parts = [validation.get("evidence_reason"), *(validation.get("reasons") or []),
                    *(validation.get("schema_errors") or [])]
    reason = "; ".join(str(part) for part in reason_parts if part) or status or "no ir"
    manifest = {
        "run_id": state["run_id"],
        "terminal": terminal,
        "reason": reason,
        "ir_path": state.get("ir_path"),
        "plan_path": state.get("plan_path"),
        "pptx_path": state.get("pptx_path"),
        "iteration": state.get("iteration"),
        "qa": state.get("qa"),
        "cache_updates": state.get("cache_updates") or {},
    }
    write_json(Path(state["run_dir"]) / "run_manifest.json", manifest)
    cache_path = state["task"].get("cache_path")
    if cache_path:
        merged = dict(state.get("cache") or {})
        merged.update(state.get("cache_updates") or {})
        Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
        Path(cache_path).write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    append_event(state["run_dir"], {"module_id": "orchestrator", "action": "terminal", "terminal": terminal})
    return {"terminal": terminal, "terminal_reason": reason}


def compile_graph(checkpointer):
    graph = StateGraph(OrchestratorState)
    graph.add_node("prepare", prepare)
    graph.add_node("apc", node_apc)
    graph.add_node("aprd", node_aprd)
    graph.add_node("seaa", node_seaa)
    graph.add_node("gate", node_gate)
    graph.add_node("revise", node_revise)
    graph.add_node("assemble", node_assemble)
    graph.add_node("plan", node_plan)
    graph.add_node("qa", node_qa)
    graph.add_node("terminal", node_terminal)
    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "apc")
    graph.add_edge("prepare", "aprd")
    graph.add_edge("prepare", "seaa")
    graph.add_edge("apc", "gate")
    graph.add_edge("aprd", "gate")
    graph.add_edge("seaa", "gate")
    graph.add_conditional_edges("gate", route_after_gate, {"revise": "revise", "assemble": "assemble"})
    graph.add_edge("revise", "seaa")
    graph.add_edge("assemble", "plan")
    graph.add_edge("plan", "qa")
    graph.add_edge("qa", "terminal")
    graph.add_edge("terminal", END)
    return graph.compile(checkpointer=checkpointer)


def open_checkpointer(path: str):
    return SqliteSaver.from_conn_string(path)


def initial_state(task: dict, run_dir: str, run_id: str) -> dict:
    return {
        "run_id": run_id,
        "run_dir": run_dir,
        "task": task,
        "logs": [],
        "module_runs": [],
        "artifacts": {},
        "cache_updates": {},
        "conflicts": {},
        "adjustments": [],
    }


def invoke_run(task: dict, run_dir: str, run_id: str, checkpoint_path: str) -> dict:
    with open_checkpointer(checkpoint_path) as saver:
        app = compile_graph(saver)
        config = {"configurable": {"thread_id": run_id}, "recursion_limit": 40}
        return app.invoke(initial_state(task, run_dir, run_id), config)


def resume_run(run_dir: str, run_id: str, checkpoint_path: str) -> dict:
    with open_checkpointer(checkpoint_path) as saver:
        app = compile_graph(saver)
        config = {"configurable": {"thread_id": run_id}, "recursion_limit": 40}
        return app.invoke(None, config)
