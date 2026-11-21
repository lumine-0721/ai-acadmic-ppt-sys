# Smoke 5 Review Summary — T36

- **Prompt**: `做一份不超过5页的人工智能介绍，尽量少用文字。`
- **Commit**: `9499853` (`feature/v01-office-renderer`)
- **Renderer**: `powerpoint-com` (Office 2021 Home & Student Retail, Build `16.0.20625.20108`)
- **`RUN_EXIT_T36`**: `0` (`overall: complete_with_placeholders`, `0 open (0 blocking), 0 provided, 5 placeholders`)
- **`GATES_EXIT_T36`**: `0` (`overall: pass`)
- **`deck.pptx`**: `0.06 MB` (`59438` bytes, included in bundle)
- **QA Report**: `0 issue(s) recorded (0 fixed & passed recheck, 0 unresolved blocker/major)`

## Per-Stage Timings

| Stage | Elapsed |
| --- | ---: |
| A (`production_plan`) | 0.01s |
| B (`provisional_slide_plan`) | 1.38s |
| C (`asset_requirements`) | 0.02s |
| D (`asset_retrieval`) | **33.95s** (180.0s budget) |
| E (`slide_asset_mapping`) | 0.01s |
| F (`page_layout_design`) | 0.15s |
| G (`pptx_generation`) | 42.95s |
| H (`render_qa_repair`) | 42.61s |

## Stage D Retrieval & Non-Acquired Diagnostics

- **Acquired (`2/7`)**:
  - `A002` (`reference`): fallback succeeded via `crossref` (`https://doi.org/10.4337/9781035307555.00008`) after `openalex` error (`attempts=3`, `elapsed=14.453s`, HTTP 429 quota/rate limit on `https://api.openalex.org/works?search=artificial+intelligence+definition&per-page=6&select=id%2Cdoi%2Ctitle%2Cpublication_year%2Ctype%2Cprimary_location%2Cauthorships%2Copen_access%2Ccited_by_count`).
  - `A006` (`reference`): fallback succeeded via `crossref` (`https://doi.org/10.1109/aiiot68874.2026.11569576`) after `openalex` error (`attempts=3`, `elapsed=13.655s`, HTTP 429 quota/rate limit on `https://api.openalex.org/works?search=AI+hallucination+definition&per-page=6&select=id%2Cdoi%2Ctitle%2Cpublication_year%2Ctype%2Cprimary_location%2Cauthorships%2Copen_access%2Ccited_by_count`).
- **Missing (`5/7` -> honest `PLACEHOLDER_U001..U005`)**:
  - `A001` (`diagram`), `A003` (`diagram`), `A005` (`diagram`), `A007` (`diagram`): `provider=none` (`D 未找到可标来源的现成图；不允许凭空绘制，请用户提供结构化数据或图`).
  - `A004` (`icon`): `provider=none` (`D 未找到可标来源的现成图标；不允许凭空绘制，请用户提供开源或有授权的图标文件`).

## Key Gate Lines (Exact)

```text
  pass     layout_diversity  5 distinct layout(s) across 5 slide(s): cover, timeline, big-number, comparison, quote
  pass     mapped_visuals_placed  all 0 accepted visual mapping(s) placed in page_design
```

## Visual Review & Smoke 4 vs Smoke 5 Comparison

- **Visual impression (PNGs)**: All 5 slides use genuinely distinct layouts with crisp typography, anchored DOIs (`S001`, `S004`), and small non-dominant bottom-right corner placeholder badges (`<= 6%` content area; dark-slate badge on `S001` cover) instead of distracting banners or hero yellow boxes.
- **Side-by-side comparison (`runs\smoke4\T36\render` -> `smoke\review\smoke5\T36\render`)**:
  - **Slide 1 (`slide_001.png`)**: Smoke 4 used the legacy pale-blue header + left bullet strip + giant right yellow dashed box (`U001`); Smoke 5 renders a deep navy `cover` with a small dark-slate corner badge (`U001`) and crisp anchored `[A002]` citation.
  - **Slide 2 (`slide_002.png`)**: Smoke 4 repeated header + left giant yellow box (`U002`) + right bullets; Smoke 5 uses a horizontal 2-step `timeline` bar with compact corner badge (`U002`).
  - **Slide 3 (`slide_003.png`)**: Smoke 4 repeated header + left bullets + right giant yellow box (`U003`); Smoke 5 uses `big-number` left accent highlight + right bullets + compact corner badge (`U003`).
  - **Slide 4 (`slide_004.png`)**: Smoke 4 repeated header + left giant yellow box (`U004`) + right bullets + bottom DOI; Smoke 5 uses a balanced two-column `comparison` layout with anchored `[A006]` DOI and corner badge (`U004`).
  - **Slide 5 (`slide_005.png`)**: Smoke 4 repeated header + left bullets + right giant yellow box (`U005`); Smoke 5 uses warm editorial `quote` typography (`把它当作工具来用`) + compact corner badge (`U005`).
