# Smoke 7 Review Summary (Round 7, `feature/v01-office-renderer` @ `702dc3c`)

`PYTEST_ALL_EXIT=0` — clean-environment full Windows suite ([`pytest_all.txt`](pytest_all.txt)): `================= 606 passed, 1 skipped in 422.52s (0:07:02) ==================` (the single skip is `tests\test_stage_h.py:92` because LibreOffice `soffice` and `pdftoppm` are not installed on this Windows host; all PowerPoint COM and cached CLIP+RapidOCR `vision_model` tests executed and passed).

All four smoke tasks (`T36`, `T12`, `T26`, **`T02`**) completed fresh end-to-end on Windows **PowerPoint COM (`16.0.20625.20108`, HomeStudent2021Retail)** through Stages **A–H** with `RUN_EXIT=0` and `GATES_EXIT=0`, using `origin/feature/v01-pipeline`'s `agent-answers` verbatim without modifying any `*_response_*.json`. No `bg_canvas` z-order occlusion or half-empty content slides remain after fixing `executor.py` (`702dc3c`) and re-running all four decks.

---

## 1. Per-Task Exit Codes, PPTX Sizes & Stage Timings

| Task | `RUN_EXIT` | `GATES_EXIT` | `overall` | `deck.pptx` | Stage A | Stage B | Stage C | **Stage D** | Stage E | Stage F | Stage G | Stage H |
| --- | :---: | :---: | --- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **T36** | `0` | `0` | `complete_with_placeholders` (`pass`) | `0.06 MB` | `0.01s` | `2.83s` | `0.02s` | **`8.60s`** | `0.00s` | `0.10s` | `42.74s` | `42.03s` |
| **T12** | `0` | `0` | `complete_with_placeholders` (`pass`) | `0.78 MB` | `0.00s` | `2.66s` | `0.03s` | **`19.55s`** | `0.01s` | `0.21s` | `43.91s` | `42.94s` |
| **T26** | `0` | `0` | `complete_with_placeholders` (`pass`) | `2.89 MB` | `0.00s` | `2.91s` | `0.05s` | **`69.18s`** | `0.01s` | `0.43s` | `43.14s` | `42.34s` |
| **T02** | `0` | `0` | `complete_with_placeholders` (`pass`) | `2.09 MB` | `0.01s` | `2.69s` | `0.04s` | **`56.94s`** | `0.01s` | `0.61s` | `46.39s` | `43.48s` |

*(All `deck.pptx` files are `<= 10 MB` and committed in each task folder alongside `render/slide_*.png`, `contact_sheet.png`, `before_after.png`, `gate_report.json`, `qa_report.json`, `page_design.json`, `asset_registry.json`, `run.log.tail.txt`, and `gates.log`.)*

---

## 2. Layout Sequence Comparison (Smoke 5 → Smoke 6 → Smoke 7)

| Task | Slides | Smoke 5 Sequence | Smoke 6 Sequence | **Smoke 7 Sequence** |
| --- | :---: | --- | --- | --- |
| **T36** | 5 | `cover, timeline, big-number, comparison, quote` | `cover, quote, section, quote, section` | **`cover, hero-statement, three-card, two-content, three-card`** |
| **T12** | 7 | `cover, timeline, image-right, comparison, big-number, section, quote` | `cover, section, image-right, quote, section, quote, section` | **`cover, three-card, image-right, hero-statement, two-content, three-card, hero-statement`** |
| **T26** | 8 | `cover, image-left, image-right, section, comparison, image-left, image-right, timeline` | `cover, image-left, image-right, section, image-right, image-left, full-bleed, quote` | **`cover, image-left, image-right, hero-statement, image-right, image-left, full-bleed, three-card`** |
| **T02** | 7 | `cover, comparison, timeline, section, big-number, image-left, quote` | *stopped at E (mismatch)* | **`cover, three-card, two-content, hero-statement, three-card, comparison, hero-statement`** |

Note: Multi-bullet content pages (`T36` `S003/S005`, `T12` `S002/S005/S007`, `T26` `S004/S008`, `T02` `S002/S003/S004/S005/S007`) no longer fall back into bullet-exempt `section`/`quote` layouts; they receive real content layouts (`two-content`, `three-card`, `hero-statement`) and are fully measured by PowerPoint COM under R7-1's hard `fill_rate` gate.

---

## 3. Selected Themes & Rationales (**4 Distinct Themes Across 4 Tasks**)

| Task | `theme_id` | Title / Body Font | `theme_rationale` (recorded on `page_design.json`) |
| --- | --- | --- | --- |
| **T36** | `swiss-minimal` | `SimHei` / `DengXian` | 匹配主题 `swiss-minimal`（配色 monochrome-red / 调性 clean, modular, geometric / 字体 SimHei+DengXian），依据：topic=`人工智能介绍`、style=`minimal_text_visual` 命中关键词 [极简、minimal]。 |
| **T12** | `warm-espresso` | `SimSun` / `DengXian` | 匹配主题 `warm-espresso`（配色 cream-espresso / 调性 warm, artisanal, approachable / 字体 SimSun+DengXian），依据：topic=`咖啡店创业计划`、style=`professional_business` 命中关键词 [咖啡、咖啡店、创业、门店、选址、餐饮]。 |
| **T26** | `architectural-stone` | `SimSun` / `Microsoft YaHei` | 匹配主题 `architectural-stone`（配色 limestone-terracotta / 调性 refined, monumental, museum / 字体 SimSun+Microsoft YaHei），依据：topic=`世界著名建筑`、style=`visual_rich_educational` 命中关键词 [建筑、名建筑、帕特农、现代主义、architecture、building]。 |
| **T02** | `eco-canopy` | `DengXian` / `DengXian` | 匹配主题 `eco-canopy`（配色 botanical-forest / 调性 organic, empirical, grounded / 字体 DengXian+DengXian），依据：topic=`城市热岛效应`、style=`clear_educational` 命中关键词 [热岛、城市热岛、海绵城市、heat island、green]。 |

All 4 decks chose distinct themes (`4 / 4` distinct, exceeding the `>= 3` requirement).

---

## 4. Hard/Warn Gate Lines, `fill_rate` Exemptions & `pixel_rules` Status

### Verbatim Gate Lines

- **T36**:
  - `layout_diversity` (`warn`): `target 5 distinct layouts, got 4 (realizable eligible: 4; reason: deck only qualifies for 4 eligible families [anchor, cards, hero-text, two-column]): cover, hero-statement, three-card, two-content, three-card`
  - `fill_rate` (`pass`): `all 4 non-exempt slide(s) pass fill_rate (max_void=1.25in < 1.60in) | exempt: S001 (role=cover (anchor title slide))`
  - `no_text_only_visuals` (`pass`): `0 image visual(s) checked cleanly (max OCR area ratio <= 0.12)`
- **T12**:
  - `layout_diversity` (`pass`): `5 distinct layout(s) across 7 slide(s): cover, three-card, image-right, hero-statement, two-content, three-card, hero-statement`
  - `fill_rate` (`pass`): `all 6 non-exempt slide(s) pass fill_rate (max_void=1.03in < 1.60in) | exempt: S001 (role=cover (anchor title slide))`
  - `no_text_only_visuals` (`pass`): `2 image visual(s) checked cleanly (max OCR area ratio <= 0.12)`
- **T26**:
  - `layout_diversity` (`pass`): `6 distinct layout(s) across 8 slide(s): cover, image-left, image-right, hero-statement, image-right, image-left, full-bleed, three-card`
  - `fill_rate` (`pass`): `all 7 non-exempt slide(s) pass fill_rate (max_void=0.72in < 1.60in) | exempt: S001 (role=cover (anchor title slide))`
  - `no_text_only_visuals` (`pass`): `8 image visual(s) checked cleanly (max OCR area ratio <= 0.12)`
- **T02**:
  - `layout_diversity` (`pass`): `5 distinct layout(s) across 7 slide(s): cover, three-card, two-content, hero-statement, three-card, comparison, hero-statement`
  - `fill_rate` (`pass`): `all 6 non-exempt slide(s) pass fill_rate (max_void=1.00in < 1.60in) | exempt: S001 (role=cover (anchor title slide))`
  - `no_text_only_visuals` (`pass`): `3 image visual(s) checked cleanly (max OCR area ratio <= 0.12)`

### `fill_rate` Summary
- **Flagged failing pages**: **none (`0`)** across all 27 rendered slides (`max_void`: T36 `1.25in`, T12 `1.03in`, T26 `0.72in`, T02 `1.00in` — all strictly `< 1.60in` on live COM `TextFrame2.BoundHeight`).
- **Exempt pages**: ONLY **`S001` (`role=cover`, reason=`role=cover (anchor title slide)`)** in each deck. All content pages previously flagged in Smoke 6 (`T36` `S003/S005`, `T12` `S002/S005/S007`) were checked as non-exempt content slides and passed.

### `pixel_rules` Status
- **Not degraded**: active on Windows with cached `fastembed==0.9.1` CLIP ViT-B-32 and `rapidocr-onnxruntime==1.2.3`.
- In **T02** Stage D, `pixel_rules` rejected `File:Sponge City.png` (`OCR rejected: text area ratio 0.143 > 0.12`) and 3 UHI text-heavy dataset legend images (`OCR rejected: text area ratio 0.205–0.211 > 0.12`); all accepted photos in T12/T26/T02 passed `ocr_ratio <= 0.052`.

---

## 5. Visual Review of Rendered Contact Sheets & Before/After Comparisons

- **Overall impression**: **Zero half-empty slides remain**, and all four decks clearly look like **four distinct styles** rather than one uniform template. (During initial Smoke 7 inspection we also caught and fixed a COM z-order bug in `executor.py` where secondary `bg_*` bands/cards got pushed behind `bg_canvas`, re-ran all 4 decks on `702dc3c`, and verified every fixed `contact_sheet.png` and `before_after.png`.)
- **T36 (`swiss-minimal`, [`T36/contact_sheet.png`](T36/contact_sheet.png), [`T36/before_after.png`](T36/before_after.png))**: Cool slate-white Swiss minimalist layout with crimson accents eliminates Smoke 6's repeated navy section dividers and fills S002–S005 cleanly with `hero-statement`, staggered `three-card`, and diagonal `two-content` cards without half-empty whitespace.
- **T12 (`warm-espresso`, [`T12/contact_sheet.png`](T12/contact_sheet.png), [`T12/before_after.png`](T12/before_after.png))**: Deep espresso cover with amber highlights, cream `#FFFBF5` canvas, Song/DengXian typography, clean coffee photos (`Café de Flore`, pour-over brewer), and full vertical distribution across all 7 slides give it an unmistakably warm hospitality look compared to Smoke 6.
- **T26 (`architectural-stone`, [`T26/contact_sheet.png`](T26/contact_sheet.png), [`T26/before_after.png`](T26/before_after.png))**: Warm limestone/charcoal palette with terracotta accents complements the architectural photography cleanly, `S004` (`hero-statement`) and `S008` (`three-card`) replace Smoke 6's sparse divider/quote pages, and `S007` (`full-bleed`) renders sharp off-white typography over the charcoal editorial band.
- **T02 (`eco-canopy`, [`T02/contact_sheet.png`](T02/contact_sheet.png), [`T02/before_after.png`](T02/before_after.png))**: Deep botanical green cover with the Be'er Sheva night thermal image, sage-tinted hero panels, staggered three-card pillars, and side-by-side WIPO green-roof / Tianjin park comparison (`S006`, cleanly free of `Sponge City.png`) look empirical, readable, and visually cohesive.
