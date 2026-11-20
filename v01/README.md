# pptv01 — AI PPT Sys V0.1

One utterance → an inspectable, editable PPTX through a short linear chain
(A→H) connected by JSON contracts on disk. Design: [`docs/V0.1-技术方案.md`](docs/V0.1-技术方案.md).
Benchmark protocol: [`docs/V0.1-基准测试规范.md`](docs/V0.1-基准测试规范.md); frozen suite in `benchmark/v1.0/`.

The old code in the repository root (`apc/`, `seaa/`, `aprd/`, `orchestrator/`, …) is frozen and untouched.
No multi-agent orchestrator, no Deck IR, no legacy handoff files.

## Status (this batch = plan steps 1–3 + contracts)

| Stage | State |
| --- | --- |
| Contracts (`contracts.py`, `contracts/*.schema.json`) | all 8 contracts, validated |
| LLM client (`llm.py`) | OpenAI-compatible, env-only config, 1 repair retry |
| **A** production plan | implemented (needs a real LLM) |
| **B** provisional slide plan | implemented (needs a real LLM); research hook is a **stub** (`NullResearch`) |
| C asset requirements, D retrieve, E mapping, F layout, G pptx, H QA | **not implemented** — runner reports `not_implemented` |

A real end-to-end run of A/B has **not** been performed yet: no LLM key was configured when this was written.
Unit tests use a scripted fake client that exists only under `tests/`; the package has no mock fallback.

## Setup

```bash
cd v01
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Python ≥ 3.11. Dependencies: pydantic v2, python-pptx, httpx (pytest for dev).

## Environment variables (secrets only from env, never in code/logs)

| Variable | Required | Meaning |
| --- | --- | --- |
| `PPTV01_LLM_BASE_URL` | yes | OpenAI-compatible base, e.g. `https://…/v1` (`/chat/completions` is appended) |
| `PPTV01_LLM_API_KEY` | yes | API key |
| `PPTV01_LLM_MODEL` | yes | model id; recorded in `run_report.json` |
| `PPTV01_LLM_TIMEOUT_S` | no | default 120 |
| `PPTV01_LLM_TEMPERATURE` | no | default 0.3 |
| `PPTV01_LLM_JSON_MODE` | no | `1` to send `response_format=json_object` (only if your provider supports it) |

### Agent backend (no API key; a human/agent acts as the model)

```bash
export PPTV01_LLM_BACKEND=agent PPTV01_AGENT_RESPONSES_DIR=/path/to/io
pptv01 run "…" --out runs/ --run-id T02          # exit 3: "awaiting agent response"
# read /path/to/io/A_request_1.json, write the model's JSON to /path/to/io/A_response_1.json
pptv01 run "…" --out runs/ --run-id T02 --resume  # replays answered prompts, asks for the next
```

Requests/responses are `<stage>_request_<n>.json` / `<stage>_response_<n>.json` (n=2 is the repair retry).
Answers pass through the same JSON validation and A/B post-checks (no bypass).
`run_report.json` records `model_id = "agent:宝特 (manual, not an API model)"` and `llm_backend = "agent"`.
This is a real answerer, not a mock, but it is NOT an API model; results must be labelled accordingly.

If any required variable is missing, `pptv01 run` exits with code 2 and a clear message; nothing is created.

## CLI

```bash
pptv01 run "帮我做一个城市热岛效应的 PPT。" --out runs/     # creates runs/<run_id>/
pptv01 run "…" --out runs/ --run-id my-run-001
pptv01 validate slide_plan runs/<run_id>/slide_plan.json
pptv01 export-schemas --out contracts/                       # or: python -m pptv01.contracts
```

A run directory currently contains:

```
runs/<run_id>/
  production_plan.json     # stage A
  slide_plan.json          # stage B, status=provisional
  llm_trace/A.json, B.json # raw model output + validation errors per attempt (no secrets)
  run_report.json          # per-stage status, model id, code commit (+dirty flag), timestamps, tool config
```

`run_report.json.overall` is `incomplete` while any of C–H is `not_implemented`; it is never `complete` unless every stage is `done`.

## Layout

```
src/pptv01/  cli.py runner.py contracts.py llm.py textcheck.py
             a_plan/ b_slide_plan/ c_asset_requirements/ d_retrieve/ e_mapping/ f_layout/ g_pptx/ h_qa/
tests/       contract / stage A rules / stage B rules / llm / runner / benchmark-frozen checks
fixtures/    hand-written legal contract samples (unit tests only)
contracts/   JSON Schemas exported from the Pydantic models (a test fails if stale)
benchmark/v1.0/  frozen 40-task suite + layers (smoke 4 / regression 12 / full 40)
docs/        technical plan, benchmark spec
```

## Rules enforced in code

- Stage A: no page count/duration/page order unless the user said it (regex post-validation; one repair retry); `defaults_applied` must match plan values and can never default page count/duration; no conclusion-style claims or statistics.
- Stage B: IDs (`S001…`) and `status=provisional` are assigned by code, never by the model; no unsourced numbers/claims; user-stated page limits are enforced.
- Contracts: `schema_version="0.1"`, `run_id`, `S###`/`A###` IDs, relative paths only, missing assets can't carry files, fixes must be re-checked, etc.
