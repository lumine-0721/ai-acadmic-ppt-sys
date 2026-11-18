# PORTABILITY / 可移植性说明

Audience: another AI or developer who must understand exactly what changed. Branch: `fix/portable-paths`.
读者：需要精确了解改动的另一个 AI 或开发者。分支：`fix/portable-paths`。

## 1. Why / 背景

The packages were copied from the author's Windows machine. Code, tests and docs pointed at `C:/Users/86139/...` and `F:/codex/...`, and the tests needed smoke files that were never committed. On any other machine 20 of 57 tests failed.
包原先从作者的 Windows 机器复制而来，代码、测试和文档写死了 `C:/Users/86139/...` 与 `F:/codex/...`，测试依赖的 smoke 文件也从未提交，换一台机器 57 个测试里有 20 个失败。

Result now / 现状：57 tests, 57 pass (apc 5, aprd 8, seaa 7, orchestrator 37). No network needed; no skips.

## 2. What changed / 改了什么

| Area | File | Change |
|---|---|---|
| Runtime path defaults | `orchestrator/src/academic_ppt_orchestrator/config.py` | `APC_ROOT`, `APRD_ROOT`, `SEAA_ROOT`, `APRD_FUSION_ROOT` now default to the sibling package directories inside this repo (`REPO_ROOT = Path(__file__).resolve().parents[3]`). An empty env var is treated as unset. Function names and return shapes are unchanged. |
| Export script | `seaa/scientific-evidence-asset-acquisition/scripts/export_standalone_core.py` | `--out-root` default was `C:/Users/86139/Desktop/codex/deliverables`; now env `SEAA_EXPORT_ROOT`, else `<repo>/dist/seaa-standalone-core`. |
| Example task | `orchestrator/examples/instrument-gain-task.json` | Absolute paths replaced by repo-relative paths into `fixtures/`. Run it from the repo root (paths are relative to the current directory, as before). |
| Tests | `apc/.../tests/test_handoff_schema.py`, `aprd/.../tests/test_aprd_fusion.py`, `aprd/.../tests/test_inspect_design.py`, `seaa/.../tests/test_probe_pdf_evidence.py`, `seaa/.../tests/test_seaa_fusion.py`, `orchestrator/tests/test_orchestrator.py` | Hardcoded `C:/...` constants replaced by `FIXTURES = $ACADEMIC_PPT_FIXTURES or <repo>/fixtures`. Scratch dirs default to the system temp dir (`APRD_TEST_TMP`, `SEAA_TEST_TMP`) instead of `C:/...`. Test logic and assertions are untouched. In `test_orchestrator.py`, `SEAA_HANDOFF`/`SEAA_REQUIREMENTS` now point to the same handoff as the other modules (the evidence fixture is built against it). |
| Fixtures (new) | `fixtures/` | Minimal committed stand-ins for the missing smoke files, see section 4. |
| Fixture builders (new) | `tools/*.py` | Scripts that regenerate every fixture. |
| Skill launchers | `skill-launchers/*/SKILL.md` | `F:/codex/projects/academic-ppt` replaced by `$ACADEMIC_PPT_ROOT` (set it to the repo checkout directory). |
| Docs | `README.md`, `orchestrator/README.md`, `MOVE_MAP.md`, `.gitignore` | Portable commands; historical `C:/`/`F:/` mentions are labelled as history. `.gitignore` adds `.tmp-tests/ .pytest_cache/ dist/ runs/`. |

Deliberately NOT changed: `MOVE_MAP.md` / `*/README.md` "Source:" lines / `THIRD_PARTY_NOTICES.md` still mention the original Windows locations as provenance records. No code reads them.
刻意没改：上述文档里的 Windows 路径只是来源记录，没有任何代码读取。

## 3. Environment variables / 环境变量

| Variable | Default | Used by |
|---|---|---|
| `APC_ROOT` | `<repo>/apc/academic-presentation-compiler` | orchestrator `config.module_roots()` |
| `APRD_ROOT` | `<repo>/aprd/academic-presentation-reference-design` | same |
| `SEAA_ROOT` | `<repo>/seaa/scientific-evidence-asset-acquisition` | same |
| `APRD_FUSION_ROOT` | same as `APRD_ROOT` | same |
| `ACADEMIC_PPT_FIXTURES` | `<repo>/fixtures` | all tests |
| `ORCHESTRATOR_TEST_WORK` | `orchestrator/.tmp-tests` (pre-existing) | orchestrator tests |
| `APRD_TEST_TMP` | `<system temp>/aprd-tests` | aprd tests |
| `SEAA_TEST_TMP` | `<system temp>/seaa-tests` | seaa tests |
| `SEAA_EXPORT_ROOT` | `<repo>/dist/seaa-standalone-core` | `export_standalone_core.py` |
| `ACADEMIC_PPT_ROOT` | none (documentation only) | `skill-launchers/*/SKILL.md` |

## 4. Fixtures / 测试夹具

All fixtures are tiny, synthetic and fictional (project id `instrument-gain-smoke`, two slides S001/S002). They only exercise the validators; they are not research data. Rebuild all with `python tools/build_fixtures.py` (needs `python-pptx`; the rest is pure Python).

| Path | Used by | Notes |
|---|---|---|
| `fixtures/apc/v001/`, `fixtures/apc/v002/` (`PPT_HANDOFF.json`, `PPT_REQUIREMENTS.md`) | apc, aprd, orchestrator tests | v002 passes `check_handoff.py validate --final --strict-assets`; v001 is the previous revision and works with `--previous`. |
| `fixtures/apc/v002/missing-support.json` | apc test | Schema-valid but semantically invalid (finding C002 lost its supporting evidence link). |
| `fixtures/apc/out/instrument-gain-v002.pptx` | aprd, orchestrator tests | 2 slides on `slideLayout6.xml` (Title Only). |
| `fixtures/aprd/source-api-demo.pptx` | aprd tests | 3 slides: inherited title geometry (548640, 365760, 8046720, 914400 from the layout), direct fill `DCE6F1`, theme colour with brightness, a group shape and a table. |
| `fixtures/aprd/hostile-template.pptx` | aprd test | Default template with 11 layouts and zero slides (APRD synthesises 11 pages from layouts). |
| `fixtures/aprd/design-v002/PPT_REFERENCE_PACKAGE/` | orchestrator tests | Passes `check_package.py validate --production --handoff fixtures/apc/v002/PPT_HANDOFF.json`. |
| `fixtures/seaa/v002/EVIDENCE_PACKAGE/` | orchestrator tests | Passes `check_evidence.py validate --production --handoff fixtures/apc/v002/PPT_HANDOFF.json`. Includes one tiny generated PNG asset with matching SHA-256. |
| `fixtures/seaa/synthetic-probe.pdf` | seaa probe test | 4 pages: text + ruled table / vector only / raster only / vector only. |
| `fixtures/seaa/plos-1003285.pdf` | seaa tests | SYNTHETIC 4-page PDF with invented text; it only carries the DOI string `10.1371/journal.pcbi.1003285` and a ruled table so DOI detection and table extraction can be tested. It is not the real paper. |
| `fixtures/seaa/core-literature-catalog.json` | seaa discovery test | 12 invented catalog records (records 1 and 12 share a DOI to test dedup). The test is offline: `discover_from_local_catalog` reads this file; the live Crossref call in `research_core/discovery.py` (`query_crossref_online`) is not exercised by any test, so nothing is skipped. |

Fidelity note: the original smoke files are not in the repo, so these fixtures were rebuilt from what the validators and assertions require, not copied. If the author's originals are available, drop them into the same paths (or point `ACADEMIC_PPT_FIXTURES` at them); the tests only rely on the file names above plus the asserted values (slide counts, geometry numbers, etc.).

## 5. Running the tests / 运行测试

Python >= 3.11 (developed on 3.13). Several tools check exact dependency versions (`jsonschema==4.25.1`, `pypdf==7.0.1`, `pdfplumber==0.11.10`, `python-pptx==1.0.2`, `langgraph==1.2.21`), so install the pinned ones:

```bash
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r orchestrator/requirements.txt \
            -r apc/academic-presentation-compiler/requirements-schema.txt \
            -r aprd/academic-presentation-reference-design/requirements-inspect-design.txt \
            -r seaa/scientific-evidence-asset-acquisition/requirements-pdf-probe.txt pytest
```

Per package (run from the package directory; `python -m unittest discover -s tests` also works):

```bash
(cd apc/academic-presentation-compiler          && python -m pytest -q)   # 5 tests
(cd aprd/academic-presentation-reference-design && python -m pytest -q)   # 8 tests
(cd seaa/scientific-evidence-asset-acquisition  && python -m pytest -q)   # 7 tests
(cd orchestrator                                && python -m pytest -q)   # 37 tests
```

Everything at once from any directory: `python -m pytest -q apc aprd seaa orchestrator/tests` (57 tests).

Running the orchestrator example (from the repo root):
`PYTHONPATH=orchestrator/src python -m academic_ppt_orchestrator run --task orchestrator/examples/instrument-gain-task.json --out runs`
Expected terminal state `BLOCKED` because no evidence package is passed (same as the original example); with `"evidence_package": "fixtures/seaa/v002/EVIDENCE_PACKAGE"` added to the task the run ends `SUCCEEDED` with evidence gate `sufficient` (checked).
