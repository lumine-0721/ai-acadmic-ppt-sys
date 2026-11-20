# Windows Office COM Testing Guide (`pptv01`)

This guide walks through setting up and verifying the **Windows desktop Microsoft PowerPoint + Excel COM** rendering pipeline from scratch through Smoke 4.

## 1. Prerequisites

- **Windows 10 / 11** desktop session (interactive logged-in user — Microsoft KB257757 does not support unattended server/service COM automation).
- **Microsoft PowerPoint & Microsoft Excel desktop** (Office 2021 / Office 365, genuine Microsoft Office — WPS Office is explicitly rejected).
- **Python 3.11+** (verified on Python 3.13.6).
- **CJK fonts installed** (`Microsoft YaHei` / `微软雅黑`, `DengXian` / `等线`, or `SimHei`).
- *(Optional)* LibreOffice (`soffice`) + `pdftoppm` only if you want secondary `--cross-render` compatibility checks.

## 2. Environment Setup (PowerShell)

```powershell
cd F:\ggagy\ai-acadmic-ppt-sys\v01

# Create and activate venv
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install pptv01 with Windows COM + read-only verifier + dev extras
python -m pip install --upgrade pip
pip install -e ".[dev,windows,verify]"
```

## 3. Run `pptv01 doctor`

Verify all required Office COM components, fonts, and optional tools before running tests:

```powershell
.\.venv\Scripts\python.exe -m pptv01.cli doctor
Write-Host "DOCTOR_EXIT=$LASTEXITCODE"
```

For machine-readable JSON output:

```powershell
.\.venv\Scripts\python.exe -m pptv01.cli doctor --json
```

All `required` checks (`windows_os`, `python`, `pywin32`, `powerpoint_com`, `excel_com`, `cjk_fonts`) must report `[OK]` and exit `0`.

## 4. Clean-Environment Test Suites (PowerShell)

Always clear `PPTV01_*` environment variables first and check `$LASTEXITCODE` directly (do **not** pipe pytest through `Select-Object` / `head`, which hides pytest's exit code).

### 4.1 Non-COM Unit & Contract Suite (Linux + Windows)

```powershell
cd F:\ggagy\ai-acadmic-ppt-sys\v01
Get-ChildItem Env:PPTV01_* -ErrorAction SilentlyContinue | Remove-Item
.\.venv\Scripts\python.exe -m pytest -m "not windows_com" -rs
Write-Host "PYTEST_EXIT=$LASTEXITCODE"
```

### 4.2 Real Windows Office COM Suite (`-m windows_com`)

Runs live Microsoft PowerPoint + Excel automation (native placeholders, CJK `NameFarEast`, `crop_fill`, native `AddChart2` + Excel cleanup, native tables, arrowed connectors, PNG/PDF export, `TextFrame2.BoundHeight` overflow repair loop, hard gates, and `pptv01 doctor`):

```powershell
cd F:\ggagy\ai-acadmic-ppt-sys\v01
Get-ChildItem Env:PPTV01_* -ErrorAction SilentlyContinue | Remove-Item
.\.venv\Scripts\python.exe -m pytest -m windows_com -p no:faulthandler -v
Write-Host "WIN_COM_EXIT=$LASTEXITCODE"
```

> Note: Always invoke `.\.venv\Scripts\python.exe -m pytest` (not bare `pytest.exe`) so the repository root is on `sys.path` for `from tests.conftest import ...`.

## 5. Running Smoke 4 (T02 / T12 / T26 / T36) with PowerPoint COM

When running with the agent backend (`PPTV01_AGENT_RESPONSES_DIR`) or an OpenAI-compatible API backend, select `--renderer powerpoint-com` (or `$env:PPTV01_RENDERER = "powerpoint-com"`):

```powershell
cd F:\ggagy\ai-acadmic-ppt-sys\v01
$env:PPTV01_LLM_BACKEND = "agent"
$env:PPTV01_AGENT_RESPONSES_DIR = "path\to\agent_responses"
$env:PPTV01_RENDERER = "powerpoint-com"
# Optional custom .potx template:
# $env:PPTV01_TEMPLATE_POTX = "path\to\academic.potx"

.\.venv\Scripts\python.exe -m pptv01.cli run "<utterance>" --out runs --run-id smoke4-t02 --resume --renderer powerpoint-com
Write-Host "RUN_EXIT=$LASTEXITCODE"

# Verify deterministic hard gates
.\.venv\Scripts\python.exe -m pptv01.cli gates runs\smoke4-t02
Write-Host "GATES_EXIT=$LASTEXITCODE"
```

To package artifacts for Linux-side PPTEval / visual review (never commit `runs/`):

```powershell
Compress-Archive -Path `
  runs\smoke4-t02\deck.pptx, `
  runs\smoke4-t02\deck_dump.json, `
  runs\smoke4-t02\qa_report.json, `
  runs\smoke4-t02\render `
  -DestinationPath smoke4-t02-artifacts.zip -Force
```

## 6. Expected Run Artifacts (`runs/<run_id>/`)

| Artifact | Producer | Purpose |
|---|---|---|
| `production_plan.json`, `research_brief.json`, `slide_plan.json` | Stages A–B | Plan & verified research notes |
| `asset_requirements.json`, `asset_registry.json`, `user_input_requests.json` | Stages C–D | Grounded assets (`visual_relevance`, `needs_review`), open user requests |
| `slide_asset_mapping.json`, `page_design.json`, `previews/*.png` | Stages E–F | Page layout (content-shrunk text, uniform `crop_fill` slots, schematic previews) |
| `deck_ops.json` | Stage G (`compile_ops`) | Backend-agnostic operation log with strict provenance bindings |
| `deck.pptx` | Stage G (`PowerPointComRenderer`) | Native editable PowerPoint presentation (native title placeholder, `crop_fill`, editable charts/tables/connectors) |
| `deck_dump.json` | Stage G (`PowerPointComRenderer`) | COM read-back structure dump (shapes, placeholders, connectors, notes) |
| `render/slide_001.png` ... + `render/deck.pdf` | Stage H (`PowerPointComRenderer.export`) | Genuine Microsoft PowerPoint 1920×1080 PNG renders & PDF (`ppSaveAsPDF=32`) |
| `qa_report.json` | Stage H | PowerPoint `BoundHeight` measurements, `capacity_vs_measured`, `missing_fonts`, geometry lint, repair log |
| `gate_report.json` | `pptv01 gates` | Deterministic hard gates (sha256 image blob check, shape whitelist, CC BY attribution) |
| `run_report.json` | Runner | Full stage/substep status, input hashes, and staleness tracking |

## 7. Common Errors & Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ImportError: DLL load failed while importing pywintypes` | `pywin32` post-install hooks missing | Run `.\.venv\Scripts\python.exe -m pywin32_postinstall -install` and reopen PowerShell. |
| PowerPoint hangs during `preflight` or `build` | Modal activation / Sign-In / Safe-Mode recovery dialog open | Open PowerPoint manually once, dismiss any modal dialog, close cleanly, then re-run `pptv01 doctor`. |
| `AddChart2` fails with `E_FAIL (-2147467259)` | Office 2021 requires a presentation window (`WithWindow=-1`) for `AddChart2` | Handled automatically by `executor.py` (opens minimized window when `ops` contains `add_chart`); ensure Excel is installed via `pptv01 doctor`. |
| Orphaned `EXCEL.EXE` or `POWERPNT.EXE` processes | Crash/interrupt during chart build | `executor.py` closes chart workbooks in `finally` and `session.py` only closes presentations it created (never touching user-opened decks). Check Task Manager if a previous process was force-killed. |
| `RPC_S_UNKNOWN_IF (-2147023179)` / `RPC server unavailable` | Repeated `CoInitialize`/`CoUninitialize` thrashing or exiting PowerPoint mid-session | Handled automatically by thread-local `_ensure_com_initialized()` and fresh read-back session in `session.py`/`executor.py`. |
| `ModuleNotFoundError: No module named 'tests'` | Bare `pytest.exe` omits working directory from `sys.path` | Use `.\.venv\Scripts\python.exe -m pytest` instead of `pytest.exe`. |
| `soffice` / `pdftoppm` shown as `[OPT]` in `pptv01 doctor` | LibreOffice / Poppler not installed | Expected on Windows; only needed if you pass `--cross-render`. Primary rendering uses native PowerPoint COM. |
