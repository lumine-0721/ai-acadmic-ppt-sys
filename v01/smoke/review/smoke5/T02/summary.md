# Smoke 5 Review Summary — T02

- **Prompt**: `帮我介绍一下城市热岛效应。`
- **Commit**: `9499853` (`feature/v01-office-renderer`)
- **Renderer**: `powerpoint-com` (Office 2021 Home & Student Retail, Build `16.0.20625.20108`)
- **`RUN_EXIT_T02`**: `0` (`overall: complete_with_placeholders`, `0 open (0 blocking), 0 provided, 7 placeholders`)
- **`GATES_EXIT_T02`**: `0` (`overall: pass`)
- **`deck.pptx`**: `0.94 MB` (`981386` bytes, included in bundle)
- **QA Report**: `0 issue(s) recorded (0 fixed & passed recheck, 0 unresolved blocker/major)`

## Per-Stage Timings

| Stage | Elapsed |
| --- | ---: |
| A (`production_plan`) | 0.01s |
| B (`provisional_slide_plan`) | 1.45s |
| C (`asset_requirements`) | 0.03s |
| D (`asset_retrieval`) | **111.12s** (180.0s budget) |
| E (`slide_asset_mapping`) | 0.02s |
| F (`page_layout_design`) | 0.25s |
| G (`pptx_generation`) | 44.58s |
| H (`render_qa_repair`) | 43.88s |

## Stage D Retrieval & Non-Acquired Diagnostics

- **Acquired (`6/13`)**:
  - `A002` (`10.1016/b978-0-323-85539-6.00009-3`, `19.61s`), `A004` (`10.1007/978-981-96-3541-2_2`, `17.77s`), `A006` (`10.1533/9780857096463.1.79`, `18.60s`), `A008` (`10.1016/j.eiar.2025.108202`, `15.51s`) via `crossref` fallback after `openalex` HTTP 429 (`attempts=3` each):
    - `https://api.openalex.org/works?search=urban+heat+island+definition&per-page=6&select=id%2Cdoi%2Ctitle%2Cpublication_year%2Ctype%2Cprimary_location%2Cauthorships%2Copen_access%2Ccited_by_count` (`16.915s`)
    - `https://api.openalex.org/works?search=Urban+Heat+Island+Spatial+Pattern+and+Urban+Form+Features+of+Different+Heat+Island+Intensity+Zones&per-page=6&select=id%2Cdoi%2Ctitle%2Cpublication_year%2Ctype%2Cprimary_location%2Cauthorships%2Copen_access%2Ccited_by_count` (`14.551s`)
    - `https://api.openalex.org/works?search=urban+heat+island+causes+review&per-page=6&select=id%2Cdoi%2Ctitle%2Cpublication_year%2Ctype%2Cprimary_location%2Cauthorships%2Copen_access%2Ccited_by_count` (`16.281s`)
    - `https://api.openalex.org/works?search=urban+heat+island+energy+consumption+health+impact+review&per-page=6&select=id%2Cdoi%2Ctitle%2Cpublication_year%2Ctype%2Cprimary_location%2Cauthorships%2Copen_access%2Ccited_by_count` (`13.943s`)
  - `A011` (`Rooftop_farm_at_the_Essex_(65787p).jpg`, `13.03s`) & `A012` (`Sponge_City.png`, `9.31s`) via `wikidata,commons`.
- **Missing (`7/13` -> honest `PLACEHOLDER_U001..U007`)**:
  - `A001` (`image`, thermal map): `commons` checked (`3.40s`), no usable CC image passing filters.
  - `A009` (`image`, `北京`): `wikidata,commons` checked (`11.73s`), rejected / no passing non-P18 mismatch asset.
  - `A010` (`chart_data`): `provider=none` (`no structured data source wired in D V0.1`).
  - `A003`, `A005`, `A007`, `A013` (`diagram`): `provider=none` (`D 未找到可标来源的现成图；不允许凭空绘制`).

## Key Gate Lines (Exact)

```text
  pass     layout_diversity  7 distinct layout(s) across 7 slide(s): cover, comparison, timeline, section, big-number, image-left, quote
  pass     mapped_visuals_placed  all 2 accepted visual mapping(s) placed in page_design
```

## Visual Review & Smoke 4 vs Smoke 5 Comparison

- **Visual impression (PNGs)**: Uses all 7 canonical layout types without a single adjacent repetition; `S006` (`image-left`) vertically stacks both `A011` and `A012` cleanly with full attributions (`0` clipped/dropped visuals, `0` QA issues), while academic DOIs and compact corner badges sit unobtrusively on text/data slides.
- **Side-by-side comparison (`runs\smoke4\T02\render` -> `smoke\review\smoke5\T02\render`)**:
  - **Slide 1 (`slide_001.png`)**: Smoke 4 used a pale-blue header bar + bullets + huge right yellow box (`U001`) + floating bottom DOI; Smoke 5 renders a deep navy `cover` with anchored `[A002]` citation and dark-slate corner badge (`U001`).
  - **Slides 2–5 (`slide_002..005.png`)**: Replaced 4 consecutive repetitions of the legacy box layout with `comparison` (`S002`), `timeline` (`S003`), dark-left `section` (`S004`), and `big-number` (`S005`).
  - **Slide 6 (`slide_006.png`)**: Smoke 4 placed rooftop farm + Sponge City graphic side-by-side beneath top-left bullets; Smoke 5 renders an asymmetric `image-left` stack pairing both acquired visuals cleanly alongside right-hand takeaways.
  - **Slide 7 (`slide_007.png`)**: Upgraded to a warm `quote` summary slide with corner badge (`U007`).
