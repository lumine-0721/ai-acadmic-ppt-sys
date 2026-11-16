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
