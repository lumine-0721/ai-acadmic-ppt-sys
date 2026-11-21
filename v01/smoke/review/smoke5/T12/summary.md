# Smoke 5 Review Summary — T12

- **Prompt**: `帮我准备一个咖啡店创业计划展示。`
- **Commit**: `9499853` (`feature/v01-office-renderer`)
- **Renderer**: `powerpoint-com` (Office 2021 Home & Student Retail, Build `16.0.20625.20108`)
- **`RUN_EXIT_T12`**: `0` (`overall: complete_with_placeholders`, `0 open (0 blocking), 0 provided, 9 placeholders`)
- **`GATES_EXIT_T12`**: `0` (`overall: pass`)
- **`deck.pptx`**: `0.77 MB` (`812168` bytes, included in bundle)
- **QA Report**: `0 issue(s) recorded (0 fixed & passed recheck, 0 unresolved blocker/major)`

## Per-Stage Timings

| Stage | Elapsed |
| --- | ---: |
| A (`production_plan`) | 0.00s |
| B (`provisional_slide_plan`) | 1.40s |
| C (`asset_requirements`) | 0.03s |
| D (`asset_retrieval`) | **40.62s** (180.0s budget) |
| E (`slide_asset_mapping`) | 0.01s |
| F (`page_layout_design`) | 0.33s |
| G (`pptx_generation`) | 45.19s |
| H (`render_qa_repair`) | 42.67s |

## Stage D Retrieval & Non-Acquired Diagnostics

- **Acquired (`3/12`)**:
  - `A001` (`image`): `wikidata,commons` (`https://upload.wikimedia.org/wikipedia/commons/f/fd/Caf%C3%A9_de_Flore.jpg`), `11.76s`.
  - `A006` (`image`): `wikidata,commons` (`https://commons.wikimedia.org/wiki/File:Pourover_using_a_cupping_brewer_at_NOLA,_Peckham,_London_(50661427221).jpg`), `7.41s`.
  - `A011` (`reference`): `crossref` fallback (`https://doi.org/10.4135/9781529768466`) in `19.63s` after `openalex` error (`attempts=3`, `elapsed=15.994s`, HTTP 429 on `https://api.openalex.org/works?search=small+restaurant+cafe+startup+failure+reasons+review&per-page=6&select=id%2Cdoi%2Ctitle%2Cpublication_year%2Ctype%2Cprimary_location%2Cauthorships%2Copen_access%2Ccited_by_count`).
- **Missing (`9/12` -> honest `PLACEHOLDER_U001..U009`)**:
  - `A002`, `A004` (`statistic`): `provider=none` (`no structured data source is wired in D V0.1`).
  - `A003`, `A005`, `A007`, `A009`, `A012` (`diagram`): `provider=none` (`D 未找到可标来源的现成图；不允许凭空绘制`).
  - `A008` (`chart_data`), `A010` (`table`): `provider=none` (`needs a cited dataset / authored content`).

## Key Gate Lines (Exact)

```text
  pass     layout_diversity  7 distinct layout(s) across 7 slide(s): cover, timeline, image-right, comparison, big-number, section, quote
  pass     mapped_visuals_placed  all 2 accepted visual mapping(s) placed in page_design
```

## Visual Review & Smoke 4 vs Smoke 5 Comparison

- **Visual impression (PNGs)**: Every slide displays a distinct layout with zero visual collisions; real Wikimedia Commons photos anchor `S001` (`cover`) and `S003` (`image-right`) with full attribution underneath, while missing figures appear as small bottom-right corner badges or twin bottom pills (`S002`, `S005`).
- **Side-by-side comparison (`runs\smoke4\T12\render` -> `smoke\review\smoke5\T12\render`)**:
  - **Slide 1 (`slide_001.png`)**: Smoke 4 paired a left photo with a huge right yellow box (`U001`) under a 2010 header bar; Smoke 5 elevates this to a dark navy `cover` hero with Café de Flore on the right and a subtle dark-slate corner badge (`U001`).
  - **Slide 2 (`slide_002.png`)**: Smoke 4 stacked two massive yellow boxes side-by-side below bullets (`U002`, `U003`); Smoke 5 lays out 3 chronological audience steps across a clean horizontal `timeline` bar with compact twin pills below.
  - **Slide 3 (`slide_003.png`)**: Smoke 4 put the pour-over photo beside an oversized yellow box (`U004`); Smoke 5 uses an editorial `image-right` split with a restrained corner badge (`U004`).
  - **Slides 4–7 (`slide_004..007.png`)**: Smoke 4 repeated the same header+bullets+giant yellow card pattern on all 4 pages; Smoke 5 differentiates into `comparison` (`S004`), `big-number` (`S005`), dark-left `section` (`S006`, with `[A011]` DOI), and warm `quote` (`S007`).
