# Smoke 5 Review Summary — T26

- **Prompt**: `做一份介绍世界著名建筑的PPT。`
- **Commit**: `9499853` (`feature/v01-office-renderer`)
- **Renderer**: `powerpoint-com` (Office 2021 Home & Student Retail, Build `16.0.20625.20108`)
- **`RUN_EXIT_T26`**: `0` (`overall: complete_with_placeholders`, `0 open (0 blocking), 0 provided, 7 placeholders`)
- **`GATES_EXIT_T26`**: `0` (`overall: pass`)
- **`deck.pptx`**: `3.56 MB` (`3737547` bytes, included in bundle)
- **QA Report**: `0 issue(s) recorded (0 fixed & passed recheck, 0 unresolved blocker/major)`

## Per-Stage Timings

| Stage | Elapsed |
| --- | ---: |
| A (`production_plan`) | 0.01s |
| B (`provisional_slide_plan`) | 1.47s |
| C (`asset_requirements`) | 0.05s |
| D (`asset_retrieval`) | **90.38s** (180.0s budget) |
| E (`slide_asset_mapping`) | 0.02s |
| F (`page_layout_design`) | 0.56s |
| G (`pptx_generation`) | 46.16s |
| H (`render_qa_repair`) | 43.69s |

## Stage D Retrieval & Non-Acquired Diagnostics

- **Acquired (`7/14`)**:
  - `A001` (`The_Parthenon_in_Athens.jpg`, `11.12s`), `A002` (`Parthenon_from_south.jpg`, `10.56s`), `A004` (`Westfassade_Chartres.jpg`, `11.00s`), `A008` (`Tour_Eiffel_Wikimedia_Commons.jpg`, `10.51s`), `A011` (`Chrysler_Building_by_David_Shankbone_Retouched.jpg`, `9.70s`), `A012` (`Shanghai_-_Shanghai_Tower_-_0001.jpg`, `10.27s`) via `wikidata,commons`.
  - `A005` (`reference`): `crossref` fallback (`https://doi.org/10.1525/9780520907874-009`) in `15.71s` after `openalex` error (`attempts=3`, `elapsed=13.689s`, HTTP 429 on `https://api.openalex.org/works?search=Gothic+cathedrals+structure+ribbed+vault+flying+buttress+review&per-page=6&select=id%2Cdoi%2Ctitle%2Cpublication_year%2Ctype%2Cprimary_location%2Cauthorships%2Copen_access%2Ccited_by_count`).
- **Missing (`7/14` -> honest `PLACEHOLDER_U001..U007`)**:
  - `A006` (`image`, `佛光寺东大殿`): `wikidata,commons` candidates checked (`5.00s`), rejected structurally (`no lexical overlap / instance of scholarly article`).
  - `A010` (`image`, `萨伏伊别墅`): `wikidata,commons` candidates checked (`4.23s`), rejected structurally (`no lexical overlap`).
  - `A003`, `A007`, `A009`, `A013`, `A014` (`diagram`): `provider=none` (`D 未找到可标来源的现成图；不允许凭空绘制`).

## Key Gate Lines (Exact)

```text
  pass     layout_diversity  6 distinct layout(s) across 8 slide(s): cover, image-left, image-right, section, comparison, image-left, image-right, timeline
  pass     mapped_visuals_placed  all 6 accepted visual mapping(s) placed in page_design
```

## Visual Review & Smoke 4 vs Smoke 5 Comparison

- **Visual impression (PNGs)**: Tall vertical architectural portraits (Parthenon South, Chartres Cathedral, Eiffel Tower, Chrysler Building, Shanghai Tower) preserve aspect ratios cleanly inside `image-left`, `image-right`, and `comparison` columns with legible Wikimedia Commons attributions and small corner badges (`0` QA issues).
- **Side-by-side comparison (`runs\smoke4\T26\render` -> `smoke\review\smoke5\T26\render`)**:
  - **Slide 1 (`slide_001.png`)**: Smoke 4 used the pale-blue header bar + bullets + right Parthenon photo; Smoke 5 upgrades to a full dark navy `cover` framing the Parthenon cleanly.
  - **Slides 2, 3, 6, 7 (`slide_002,003,006,007.png`)**: Smoke 5 alternates `image-left` / `image-right` editorial columns with bottom-right corner badges instead of Smoke 4's uniform top-header + giant placeholder blocks.
  - **Slide 4 (`slide_004.png`)**: Smoke 4 rendered two giant side-by-side yellow rectangles (`U002`, `U003`); Smoke 5 renders a dark-left `section` page (`东方的木构与园林`) with twin compact bottom pills.
  - **Slide 5 (`slide_005.png`)**: Smoke 4 put Eiffel Tower next to a huge yellow box (`U004`); Smoke 5 frames it in a crisp `comparison` column with an unobtrusive corner badge (`U004`).
  - **Slide 8 (`slide_008.png`)**: Smoke 5 concludes on a 3-step `timeline` (`几座建筑的共同线索`) instead of a uniform bullet+box layout.
