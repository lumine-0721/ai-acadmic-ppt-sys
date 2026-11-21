# Third-party material

- **PPTEval prompts** (`src/pptv01/grading/ppteval_prompts/*.txt`): copied verbatim from
  [icip-cas/PPTAgent](https://github.com/icip-cas/PPTAgent) tag v1.1.38, `pptagent/prompts/ppteval/`.
  Copyright (c) 2025 ICIP-CAS, MIT License (full text in `LICENSE-PPTAgent.txt` beside the prompts).
  Zheng et al., *PPTAgent: Generating and Evaluating Presentations Beyond Text-to-Slides*, EMNLP 2025, <https://arxiv.org/abs/2501.03936>.
- **cn2an** (runtime dependency, MIT) <https://github.com/Ailln/cn2an>
- **hugohe3/ppt-master** (`slot_capacity.py` CJK/Latin glyph-advance capacity estimation idea and page-role taxonomy adapted in `src/pptv01/f_layout/capacity.py` and `src/pptv01/f_layout/layouts.py`),
  MIT License, Copyright (c) 2025-2026 Hugo He, <https://github.com/hugohe3/ppt-master>.
- **slidevjs/slidev** (`packages/client/layouts/` canonical layout taxonomy `cover`, `section`, `fact`, `quote`, `image-left`, `image-right`, `two-cols`, `image` adapted in `src/pptv01/f_layout/layouts.py`),
  MIT License, Copyright (c) 2021-present Anthony Fu & Slidev contributors, <https://github.com/slidevjs/slidev>.
- **presenton/presenton** (`layouts.json` named layout registry and structured slot separation adapted in `src/pptv01/f_layout/layouts.py`),
  Apache-2.0 License, Copyright (c) Presenton contributors, <https://github.com/presenton/presenton>.
- **gitbrent/PptxGenJS** (`defineSlideMaster` structural per-layout contrast & visual hierarchy patterns referenced in `src/pptv01/f_layout/layouts.py`; no JS runtime or python-pptx writer reused),
  MIT License, Copyright (c) 2015-present Brent Ely, <https://github.com/gitbrent/PptxGenJS>.
- **thomas-villani/pptlive** (commit `4b33e69217ba`, MIT License, Copyright (c) 2026 Tom Villani, <https://github.com/thomas-villani/pptlive>):
  chart workbook `SetSourceData` / `_safe_close` pattern, table/snapshot helpers, geometry linting rules, and pure-Python `fake_powerpoint` test fixture pattern adapted in `src/pptv01/renderers/powerpoint_com/` and `tests/fake_com.py`.
- **ykuwai/ppt-mcp** (commit `2155ecc9050e`, MIT License, Copyright (c) 2026 ykuwai, <https://github.com/ykuwai/ppt-mcp>):
  `charts.py` (`CHART_TYPE_MAP` and `finally: wb.Close(False)` Excel leak guard), `connectors.py` (`CONNECTOR_TYPE_MAP`/`ARROW_STYLE_MAP`), `layout_check.py` (pure-arithmetic geometry separation), and `export.py` patterns adapted in `src/pptv01/renderers/powerpoint_com/` and `src/pptv01/h_qa/`.
- **Internal reuse**: PowerPoint COM PNG export and `TextFrame2.TextRange.BoundHeight` measurement adapted from `aprd/academic-presentation-reference-design/scripts/aprd_engine/reconstructor_verifier.py:468` (read-only copy/adaptation; frozen `aprd/` untouched).
- Optional: **pywin32** (PSF License, <https://github.com/mhammond/pywin32>, `[windows]` extra), **fastembed** (Apache-2.0), **sentence-transformers** (Apache-2.0), **BAAI/bge-reranker-v2-m3** (MIT), **pyalex** (MIT).
