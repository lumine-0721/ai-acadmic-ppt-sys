# Third-Party Notices

This repository reuses, adapts, or depends on the following open-source software and media assets. See also [`v01/NOTICE.md`](v01/NOTICE.md) for earlier V0.1 notices.

---

## 1. presenton/presenton

- **Repository**: <https://github.com/presenton/presenton>
- **License**: Apache License, Version 2.0 (<http://www.apache.org/licenses/LICENSE-2.0>)
- **Upstream files referenced/ported**:
  - `servers/fastapi/models/theme_data.py` (`PresentationThemeColors`, `graph_0..graph_9` palette model structure)
  - `servers/fastapi/templates/v2/theme.py` (`contrast_ratio()`, `_relative_luminance()`, `_repair_text_color()`, `_complete_graph_colors()`, `_surface_variant()`, plus hex/RGB/HLS helpers)
  - `layouts.json` (previously noted named layout registry and structured slot separation)
- **Local files**:
  - `v01/src/pptv01/theme/tokens.py`
  - `v01/src/pptv01/f_layout/layouts.py`
- **Changes made**:
  - Replaced SQL/template ORM wiring with a frozen Python dataclass (`ThemeTokens`) carrying our semantic tokens (`academic-default`), light/dark caption/citation tokens, and Presenton's `graph_0..9` series completed deterministically with `_complete_graph_colors()`.
  - Made `_repair_text_color()` accept empty candidate sequences in addition to candidate profiles so unit callers and layout builders can repair contrast directly against any background color.
- **Upstream NOTICE**:
  - Upstream includes a dependency attribution `NOTICE` file covering bundled Python/npm packages (<https://github.com/presenton/presenton/blob/main/NOTICE>); none of those upstream third-party binary bundles are redistributed here. Licensed under the Apache License, Version 2.0:
    Copyright (c) Presenton contributors. Licensed under the Apache License, Version 2.0; you may not use these files except in compliance with the License.

---

## 2. SlideSpeak/slide-design-skill

- **Repository**: <https://github.com/SlideSpeak/slide-design-skill>
- **License**: MIT License
- **Upstream files referenced/ported**:
  - `engine/occupancy.ts` (`scoreOccupancy`, `scoreCellOccupancy`, `VOID_MAX_PX`, `CELL_VOID_MAX_PX`, `CELL_VOID_MAX_FRACTION`, `CELL_VOID_MIN_HEIGHT`, `SPARSE_CELL_MIN_AREA`, `SPARSE_TEXT_COVERAGE`)
- **Local files**:
  - `v01/src/pptv01/h_qa/occupancy.py`
  - `v01/src/pptv01/grading/gates.py` (`fill_rate`)
- **Changes made**:
  - Translated TypeScript occupancy scorers to pure Python operating on Stage F `PageDesignEntry` coordinates (inches on `13.333 x 7.5 in` canvas, scaling `230 px / 1080 px -> 1.60 in` and `180 px / 1080 px -> 1.25 in`) with optional Windows COM `TextMeasurement.bound_h_pt` cross-check and explicit `cover`/`section`/`quote` exemption reasons.
- **MIT License (verbatim)**:

```text
MIT License

Copyright (c) 2026 SlideSpeak

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## 3. Earlier V0.1 Notices (carried from `v01/NOTICE.md`)

- **icip-cas/PPTAgent** (`v1.1.38`, MIT License, Copyright (c) 2025 ICIP-CAS, <https://github.com/icip-cas/PPTAgent>): PPTEval prompts vendored in `v01/src/pptv01/grading/ppteval_prompts/`.
- **hugohe3/ppt-master** (MIT License, Copyright (c) 2025-2026 Hugo He, <https://github.com/hugohe3/ppt-master>): `slot_capacity.py` CJK/Latin glyph-advance estimation adapted in `v01/src/pptv01/f_layout/capacity.py`.
- **slidevjs/slidev** (MIT License, Copyright (c) 2021-present Anthony Fu & Slidev contributors, <https://github.com/slidevjs/slidev>): canonical layout taxonomy adapted in `v01/src/pptv01/f_layout/layouts.py`.
- **gitbrent/PptxGenJS** (MIT License, Copyright (c) 2015-present Brent Ely, <https://github.com/gitbrent/PptxGenJS>): structural layout hierarchy patterns referenced in `v01/src/pptv01/f_layout/layouts.py`.
- **thomas-villani/pptlive** (commit `4b33e69217ba`, MIT License, Copyright (c) 2026 Tom Villani, <https://github.com/thomas-villani/pptlive>): COM chart/table/fake-COM patterns adapted in `v01/src/pptv01/renderers/powerpoint_com/` and `v01/tests/fake_com.py`.
- **ykuwai/ppt-mcp** (commit `2155ecc9050e`, MIT License, Copyright (c) 2026 ykuwai, <https://github.com/ykuwai/ppt-mcp>): chart/connector/layout-check patterns adapted in `v01/src/pptv01/renderers/powerpoint_com/` and `v01/src/pptv01/h_qa/`.
- **cn2an** (MIT License, <https://github.com/Ailln/cn2an>), **pywin32** (PSF License), **fastembed** (Apache-2.0), **sentence-transformers** (Apache-2.0), **BAAI/bge-reranker-v2-m3** (MIT).
