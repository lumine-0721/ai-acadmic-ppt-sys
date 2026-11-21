# Smoke 6 Summary (Round 6 — Windows Real PowerPoint COM)

`PYTEST_ALL_EXIT=0` — `================= 572 passed, 1 skipped in 525.72s (0:08:45) ==================` (clean-env Windows run of `.\.venv\Scripts\python.exe -m pytest -o addopts="" -rs -p no:cacheprovider`, saved in `v01/pytest_all.txt`; the single skip is `LibreOffice (soffice) and pdftoppm not installed in environment`).

- **Branch / Code Commit:** `feature/v01-office-renderer` at `50d26c7ef5981270dc575f6e73b4c42907518747`
- **Renderer / Environment:** Windows 11 (`10.0.26200`), Microsoft PowerPoint `16.0 (build 20625)`, Python `3.13.6`, LLM backend `agent:宝特 (manual, not an API model)`, proxy `http://127.0.0.1:7890`
- **Pixel Filter Status (`pixel_rules`):** Active (`CLIP + RapidOCR`, **no degradation**) on all 4 runs (`T36`, `T12`, `T26`, `T02`). Pre-cached CLIP (`Qdrant/clip-ViT-B-32-vision` + `-text`) loaded in `1.20s–2.46s`; bundled RapidOCR loaded in `0.35s–1.01s`.

---

## Per-Task Matrix & Stage Timings

| Task | `RUN_EXIT` | `GATES_EXIT` | Overall | `deck.pptx` | A (s) | B (s) | C (s) | **D (s)** | E (s) | F (s) | G (s) | H (s) |
| --- | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **T36** | `0` | `0` | `complete_with_placeholders` (`pass`) | `0.06 MB` (`59,950 B`) | `0.01` | `4.19` | `0.05` | **`15.25`** | `0.01` | `0.33` | `46.08` | `45.73` |
| **T12** | `0` | `0` | `complete_with_placeholders` (`pass`) | `0.77 MB` (`812,297 B`) | `0.01` | `3.42` | `0.06` | **`85.24`** | `0.01` | `0.37` | `46.02` | `44.20` |
| **T26** | `0` | `0` | `complete_with_placeholders` (`pass`) | `2.89 MB` (`3,029,448 B`) | `0.00` | `2.93` | `0.09` | **`134.84`** | `0.02` | `0.92` | `44.29` | `42.59` |
| **T02** | `1` | `0` | `failed` at Stage E (`incomplete` gates; awaited new answers) | — | `0.01` | `2.92` | `0.04` | **`171.03`** | `0.09` (`failed`) | `skipped` | `skipped` | `skipped` |

### Notes on Stage D & Execution

- **T36 (OpenAlex 429 circuit breaker triggered live):** `A002` succeeded from OpenAlex in `4.97s`, then `A006` received `HTTP 429` from OpenAlex on attempt 1 (`0.30s`), immediately tripping the run circuit breaker without retries (`openalex: circuit_open after 429 at 2026-11-21T23:10:06+08:00 (skipped 0 request(s), est. saved ~6.0s)`) and falling back directly to Crossref (`A006` acquired in `1.48s` total).
- **T12 & T26:** OpenAlex returned `HTTP 200` on `T12 A011` (`10.56s`) and `T26 A005` (`2.78s`), so the circuit breaker stayed closed. *(Note: an earlier `T26` attempt on the same commit hit transient Wikimedia Commons download slowness causing D's `180s` budget to expire with `A008/A010/A011/A012` missing; on one clean retry with default env, `T26` finished Stage D cleanly in `134.84s` acquiring all 8 images + 1 reference.)*
- **T02 (Stopped per Hard Rule on Stage E asset mismatch — no `*_response_*.json` touched):** `pixel_rules` rejected 3 text-heavy UHI map legends on `A001` (`ocr_ratio` `0.209`, `0.211`, `0.205 > 0.12`) and selected `File:Be'er Sheva heat island at night.png` (`clip_pos=0.337, ocr_ratio=0.052`), and rejected `File:Sponge City.png` on `A012` (`ocr_ratio=0.143 > 0.12`) and selected `File:Tianjin Qiaoyuan Park (Tianjin City, built 2008).jpg` (`ocr_ratio=0.000`). Because `A001` was newly acquired compared to pre-recorded Smoke 5 `E_response_*.json`, Stage E rejected with `usable assets neither accepted on any page nor listed in unused_assets: ['A001']`. Per Hard Rule, `T02` halted immediately; `T02/E_request_2.json`, `T02/asset_registry.json`, `T02/gate_report.json`, `T02/gates.log`, and `T02/run.log.tail.txt` are submitted for updated agent mapping answers.

---

## Layout Sequences (`Smoke 5` → `Smoke 6`)

- **T36 (5 slides):**
  - Smoke 5: `cover, timeline, big-number, comparison, quote`
  - Smoke 6: `cover, quote, section, quote, section`
- **T12 (7 slides):**
  - Smoke 5: `cover, timeline, image-right, comparison, big-number, section, quote`
  - Smoke 6: `cover, section, image-right, quote, section, quote, section`
- **T26 (8 slides):**
  - Smoke 5: `cover, image-left, image-right, section, comparison, image-left, image-right, timeline`
  - Smoke 6: `cover, image-left, image-right, section, image-right, image-left, full-bleed, quote`
- **T02 (7 slides):**
  - Smoke 5: `cover, comparison, timeline, section, big-number, image-left, quote`
  - Smoke 6: halted at Stage E (`page_design.json` not yet generated; awaiting `E_request_2.json`).

---

## Verbatim Gate Lines (`fill_rate` & `no_text_only_visuals`)

### T36
```text
  pass     fill_rate  all 0 non-exempt slide(s) pass fill_rate (max_void=0.00in < 1.60in) | exempt: S001 (cover anchor title slide), S002 (editorial statement breathing slide), S003 (section divider breathing slide), S004 (editorial statement breathing slide), S005 (section divider breathing slide)
  pass     no_text_only_visuals  0 image visual(s) checked cleanly (max OCR area ratio <= 0.12)
```

### T12
```text
  pass     fill_rate  all 1 non-exempt slide(s) pass fill_rate (max_void=0.24in < 1.60in) | exempt: S001 (cover anchor title slide), S002 (section divider breathing slide), S004 (editorial statement breathing slide), S005 (section divider breathing slide), S006 (editorial statement breathing slide), S007 (section divider breathing slide)
  pass     no_text_only_visuals  2 image visual(s) checked cleanly (max OCR area ratio <= 0.12)
```

### T26
```text
  pass     fill_rate  all 5 non-exempt slide(s) pass fill_rate (max_void=0.24in < 1.60in) | exempt: S001 (cover anchor title slide), S004 (section divider breathing slide), S008 (editorial statement breathing slide)
  pass     no_text_only_visuals  8 image visual(s) checked cleanly (max OCR area ratio <= 0.12)
```

### T02
```text
  not_run  fill_rate  page_design.json absent (stage F not run)
  pass     no_text_only_visuals  3 image visual(s) checked cleanly (max OCR area ratio <= 0.12)
```

---

## `Sponge_City.png` & High-OCR Signage Rejection Records (`T02`)

From `v01/smoke/review/smoke6/T02/asset_registry.json` and `T02/run.log.tail.txt`:

- **A012 (`File:Sponge City.png` rejected, clean photo selected):**
  - Rejected candidate: `File:Sponge City.png`
  - Verbatim reason: ``OCR rejected: text area ratio 0.143 > 0.12 [clip_pos=0.374, neg='a sign with text':0.298, ocr_ratio=0.143]``
  - Note: Because the prompt target itself is `"sponge city"`, CLIP's positive prompt scored `0.374`, making calibrated RapidOCR (`OCR_TEXT_AREA_RATIO_MAX = 0.12`) decisive in catching the roadside text sign independently (`0.143 > 0.12`).
  - Replacement selected for A012: `File:Tianjin Qiaoyuan Park (Tianjin City, built 2008).jpg` (``clip_pos=0.221, neg='a map legend':0.199, ocr_ratio=0.000``).
- **A001 (3 high-OCR map-legend files rejected):**
  - `File:Global Urban Heat Island (UHI) Data Set, 2013 Summer Daytime Urban-Rural Temperature Difference (30563445286).jpg` -> ``OCR rejected: text area ratio 0.209 > 0.12 [clip_pos=0.270, neg='a screenshot of text':0.215, ocr_ratio=0.209]``
  - `File:Global Urban Heat Island (UHI) Data Set, 2013 Summer Nighttime Urban-Rural Temperature Difference (30563444636).jpg` -> ``OCR rejected: text area ratio 0.211 > 0.12 [clip_pos=0.261, neg='a screenshot of text':0.221, ocr_ratio=0.211]``
  - `File:Global Urban Heat Island (UHI) Data Set, 2013 Average Summer Daytime Maximum Surface Temperature (30563444826).jpg` -> ``OCR rejected: text area ratio 0.205 > 0.12 [clip_pos=0.268, neg='a screenshot of text':0.213, ocr_ratio=0.205]``
  - Selected for A001: `File:Be'er Sheva heat island at night.png` (``clip_pos=0.337, neg='a map legend':0.222, ocr_ratio=0.052``).

---

## Visual Inspection Notes (`contact_sheet.png` & `before_after.png`)

- **T36 (`T36/contact_sheet.png`, `T36/before_after.png`):** Eliminating fake `timeline`/`big-number`/`comparison` slots in favor of `quote` and dark-left `section` slides removed all empty whitespace voids on S002–S005, leaving balanced typography and crisp `10.5pt` `[A002]`/`[A006]` citations.
- **T12 (`T12/contact_sheet.png`, `T12/before_after.png`):** Switching S002/S004/S005 from empty columns and synthetic stat cards to `section`/`quote` paired cleanly with `Café de Flore` (`S001`), pour-over (`S003`), and high-contrast `#475569`/`#CBD5E1` `10.5pt` attributions and citations.
- **T26 (`T26/contact_sheet.png`, `T26/before_after.png`):** All 8 architectural slides look balanced and legible—including Foguang Temple newly rendered on `S004` (`section`), clean `S008` (`quote`) replacing Smoke 5's sparse `timeline`, and `S007` (`full-bleed`) keeping its `10.5pt` `#CBD5E1` attribution cleanly above the placeholder badge (with one residual COM QA ratio warning `shape 2.96 vs image 2.57` on `S007` crop-fill banner noted in `qa_report.json`).
- **T02:** Halted honestly after Stage D (`pixel_rules` blocked `File:Sponge City.png` + 3 high-OCR UHI legend screenshots and acquired clean replacements); awaiting updated agent Stage E response (`T02/E_request_2.json`).
