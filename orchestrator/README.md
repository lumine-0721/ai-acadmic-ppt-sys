# Academic PPT Orchestrator

Control plane for the installed APC, APRD, and SEAA CLIs. It schedules them, checks their results, writes Academic Deck IR, and can resume from a LangGraph SQLite checkpoint. It does not invent literature or replace the three modules.

## Run

From PowerShell 7, set PYTHONPATH to academic_ppt_orchestrator/src and run:

    .venv/Scripts/python.exe -m academic_ppt_orchestrator run --task examples/instrument-gain-task.json --out C:/Users/86139/Desktop/codex/outputs/orchestrator-runs --run-id instrument-gain-example-2

Resume a failed thread:

    .venv/Scripts/python.exe -m academic_ppt_orchestrator resume --run-dir RUN_DIR --thread-id RUN_ID

Tests:

    .venv/Scripts/python.exe -m unittest discover -s tests -v

The verified example run is outputs/orchestrator-runs/instrument-gain-example-2. Its terminal state is BLOCKED because no evidence package was supplied. APC and APRD accepted. PowerPoint opened probe.pptx read-only: 2 slides, first shape text S001. That file is a python-pptx probe, not a production render.

Engineering notes are in C:/Users/86139/Desktop/codex/docs/orchestrator.

## IR and execution plan v2

`academic-deck-ir/2` joins APC and SEAA by slide ID and keeps the stricter of
`READY`, `PROVISIONAL`, and `BLOCKED`. The IR records both original statuses,
reasons, and evidence gaps. A missing slide assessment is provisional; a rejected
validation or unknown status blocks the page. The deck can only be accepted when
all pages are ready and the existing module and evidence checks pass.

`evidence_bindings` contains one edge per claim, evidence, and relation
(`supports`, `context`, or `opposes`), with evidence status and references to the
original evidence and source manifests. Only eligible `supports` count as support.
`backend-execution-plan/2` carries page readiness and typed relations; its
per-slide `evidence_ids` contains only eligible support IDs. `input_status` is
separate from `backend_status`: the production backend is still unbound.

Historical v1 artifacts are unchanged. To obtain v2 output from a v1 run, start
a new run with the original task files and a new run ID; do not relabel or resume
an already assembled v1 IR into the v2 execution-plan builder.
