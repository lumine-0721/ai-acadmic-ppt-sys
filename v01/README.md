# pptv01 — AI PPT Sys V0.1

One utterance → an inspectable, editable PPTX through a short linear chain
(A→H) connected by JSON contracts on disk. Design: [`docs/V0.1-技术方案.md`](docs/V0.1-技术方案.md).
Benchmark protocol: [`docs/V0.1-基准测试规范.md`](docs/V0.1-基准测试规范.md); frozen suite in `benchmark/v1.0/`.

The old code in the repository root (`apc/`, `seaa/`, `aprd/`, `orchestrator/`, …) is frozen and untouched.
No multi-agent orchestrator, no Deck IR, no legacy handoff files.

## Status

| Stage | State |
| --- | --- |
| Contracts (`contracts.py`, `contracts/*.schema.json`) | 12 contracts (incl. `research_brief`, `user_input_requests`, `grade_report`, `gate_report`), validated |
| LLM client (`llm.py`) | OpenAI-compatible or labelled `agent` backend; env-only config; 1 repair retry |
| **A** production plan (+ `retrieval_brief`) | implemented |
| **B** research + provisional slide plan | implemented; real retrieval (Wikipedia, Crossref, optional SearXNG) + relevance filter (structural rules, fastembed, optional bge-reranker; kept AND filtered hits recorded); structured `asset_needs` |
| **C** asset requirements | implemented: every B need is covered or dropped with a reason; checked for traceability, concrete purpose, acceptance criteria |
| **D** asset registry & user requests | implemented: Wikimedia Commons images (licence allowlist, author, source page) with Wikidata disambiguation/P18, cross-requirement deduplication (`shared_with`), CLIP + RapidOCR pixel filtering (`pixel_rules`), OpenAlex (httpx + quota circuit breaker) + Crossref DOI verification; deterministic `user_input_requests.json` for `missing` items (`--on-missing wait\|placeholder`, `requests`/`provide` CLI) |
| **E** slide asset mapping | implemented: maps only acquired/drawn/substituted or user-provided assets; optional VLM relevance (`PPTV01_E_VISION_MODEL`, else `not_assessed`) |
| **F** page layout design + previews | implemented: 13.333×7.5 in geometry, CJK/Latin `slot_capacity` fitting, aspect-ratio adaptation, `PLACEHOLDER_U###` boxes, Pillow schematic previews (`layout schematic, not a render`), centralized `ThemeTokens` (`academic-default` via `PageDesign.theme_id` → Stage G) |
| **G** native PPTX generation | implemented: `python-pptx` native text/shapes/charts/tables/images, slide notes attribution & licence checks, re-open verification |
| **H** render, QA & bounded repair | implemented: LibreOffice (`soffice`) → PDF → `pdftoppm` (`render/slide_*.png`), 6-kind QA checks, bounded repair + recheck (`qa_report.json`); fails explicitly when `soffice`/`pdftoppm` missing |
| Grading | PPTEval rubric (MIT, vendored verbatim) + separate deterministic hard gates: `pptv01 grade`, `pptv01 gates` (see `docs/grading.md`) |

Unit tests use a scripted fake client / fake research / HTTP replay that exist only under `tests/`; the package has no mock fallback.
Real model runs need an LLM (API key, or the `agent` backend where a human/agent answers); see below.

## Setup

```bash
cd v01
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"            # add ,rank for the fastembed relevance scorer (,rerank for bge-reranker)
pytest
```

Python ≥ 3.11. Dependencies: pydantic v2, python-pptx, httpx, cn2an, pillow (pytest for dev; fastembed / rapidocr-onnxruntime optional). See [`docs/proxy.md`](docs/proxy.md) for proxy, `HF_ENDPOINT` mirror, and model cache configuration.

## Environment variables (secrets only from env, never in code/logs)

| Variable | Required | Meaning |
| --- | --- | --- |
| `PPTV01_LLM_BASE_URL` | yes | OpenAI-compatible base, e.g. `https://…/v1` (`/chat/completions` is appended) |
| `PPTV01_LLM_API_KEY` | yes | API key |
| `PPTV01_LLM_MODEL` | yes | model id; recorded in `run_report.json` |
| `PPTV01_LLM_TIMEOUT_S` | no | default 120 |
| `PPTV01_LLM_TEMPERATURE` | no | default 0.3 |
| `PPTV01_RESEARCH` | no | stage-B research: `auto` (default = wikipedia + crossref, no key needed) / `none` (explicitly off, recorded as `disabled`) / comma list `wikipedia,crossref,searxng` |
| `PPTV01_RESEARCH_SEARXNG_URL` | with `searxng` | base URL of a SearXNG instance with the JSON API |
| `PPTV01_RESEARCH_CONTACT` | no | e-mail/URL put in the User-Agent (Wikimedia/Crossref etiquette); Crossref `mailto` if it contains `@` |
| `PPTV01_RELEVANCE` | no | `auto` (default: structural rules + fastembed if installed, else structural only, recorded) / `structural` / `off` |
| `PPTV01_RELEVANCE_SCORER` / `_THRESHOLD` | no | `fastembed` (default, 0.30) or `bge-reranker` (opt-in, see `docs/relevance-benchmark.md`) / float override |
| `PPTV01_ASSETS` | no | stage D: `auto` (default) / `none` (explicitly off, every requirement recorded `missing`) |
| `PPTV01_OPENALEX_API_KEY` / `_EMAIL` | no | OpenAlex free key (the shared anonymous budget is small; quota errors are recorded, never hidden) |
| `PPTV01_E_VISION_MODEL` / `_BASE_URL` / `_API_KEY` / `_TIMEOUT_S` | no | optional stage-E vision model for image-slide relevance checks (falls back to `PPTV01_LLM_BASE_URL`/`_API_KEY`; unset = `relevance: not_assessed`) |
| `PPTV01_GRADER_BACKEND` + `_BASE_URL/_API_KEY/_MODEL/_VISION_MODEL` or `_EXCHANGE_DIR` | for `grade` | PPTEval judge: `api` (OpenAI-compatible MLLM) or `agent` (manual, labelled); unset = error, never a made-up score |
| `PPTV01_RESEARCH_MAX_HITS` / `_MAX_QUERIES` / `_TIMEOUT_S` | no | per query per backend (3) / queries per run (12) / HTTP timeout (20 s) |
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
pptv01 run "…" --out runs/ --run-id my-run-001 --on-missing wait  # pauses after D (exit 4) when assets missing
pptv01 requests runs/<run_id>
pptv01 provide runs/<run_id> U001 --file data.json --source "NOAA 2024" --license "CC0"
pptv01 run "…" --out runs/ --run-id my-run-001 --resume
pptv01 gates runs/<run_id>
pptv01 grade runs/<run_id> --slides-dir runs/<run_id>/render
pptv01 validate slide_plan runs/<run_id>/slide_plan.json
pptv01 export-schemas --out contracts/                       # or: python -m pptv01.contracts
```

A run directory currently contains:

```
runs/<run_id>/
  production_plan.json     # stage A (incl. retrieval_brief)
  research_brief.json      # stage B: queries, per-query status, notes with URL/title/retrieved_at/licence (or status disabled/failed)
  slide_plan.json          # stage B, status=provisional, structured asset_needs
  asset_requirements.json  # stage C
  asset_registry.json      # stage D: one record per requirement (acquired with source/licence/sha256, shared_with, or missing with reason)
  user_input_requests.json # stage D: deterministic checklist of missing items for user supply or placeholder rendering
  assets/                  # stage D files (A001.jpg, A004.json ...)
  inputs/                  # user-supplied files via `pptv01 provide`
  slide_asset_mapping.json # stage E
  page_design.json         # stage F
  previews/                # stage F Pillow layout schematics ("layout schematic, not a render")
  deck.pptx                # stage G native python-pptx presentation
  render/                  # stage H LibreOffice -> PDF -> pdftoppm PNGs (slide_*.png)
  qa_report.json           # stage H QA + bounded repair/recheck report
  gate_report.json, grade_report.json  # optional: pptv01 gates / grade
  llm_trace/A.json, B.json, C.json, E.json # raw model output + validation errors per attempt (no secrets)
  run_report.json          # per-stage status, model id, code commit (+dirty flag), timestamps, tool config
```

`run_report.json.overall` is `complete` only when A–H are `done` and zero open/placeholder requests exist (`complete_with_placeholders` if placeholders were rendered; `needs_input` when paused with `--on-missing wait`).

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

- Stage F selects a theme from the 8-theme library (`src/pptv01/theme/library/*.json` + `selection-index.json`) via Stage `TH` (`select_theme()`, `TH_request_<n>.json`/`TH_response_<n>.json` on active LLM/Agent clients with `theme_method="agent"|"llm"`, or `"keyword_fallback"` only when no client is active) and records `PageDesign.theme_id`, `PageDesign.theme_method`, and `PageDesign.theme_rationale` in `page_design.json`; Stage G (`compile_deck_ops`) reads `design.theme_id` to resolve fallback colors, fonts, and diagram tokens uniformly without hardcoded palette literals.
- Stage F renders the conclusion-sentence `SlidePage.headline` (falling back to `page.title` when unset) in the slide title slot, routes text-only `two-content` / `three-card` strictly from semantic `SlidePage.groups` (`len == 2` / `3`, else single-column), and `compile_deck_ops` writes `SlidePage.speaker_notes` (2–5 spoken sentences, never planning `content_summary`) into slide speaker notes.
- Stage A: no page count/duration/page order unless the user said it (regex post-validation; one repair retry); `defaults_applied` must match plan values and can never default page count/duration; no conclusion-style claims or statistics.
- Stage B: outputs required `page_role`, assertive sentence `headline` (verified by `validate_ghost_deck()`), optional semantic `groups`, and spoken `speaker_notes`; IDs (`S001…`) and `status=provisional` are assigned by code, never by the model; no unsourced numbers/claims; user-stated page limits are enforced.
- Contracts: `schema_version="0.1"`, `run_id`, `S###`/`A###` IDs, relative paths only, missing assets can't carry files, fixes must be re-checked, etc.
