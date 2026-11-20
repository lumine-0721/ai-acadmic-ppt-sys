# Stage F Layout & Capacity Calibration (R5)

## 1. Empirical Calibration on Desktop Microsoft PowerPoint 16.0 (Office 2021 / Office 16 Baseline APIs)

> **Environment & API Compatibility Note (B12 / N1):** Measurements below were recorded on local Windows desktop **PowerPoint 16.0** (ClickToRun Current Channel; confirmed user environment: Microsoft 365 / Office 16 desktop Build 20527.20196 / 20257.20196). Code uses **only Office 2021 / Office 16 baseline COM APIs** (`TextFrame2.TextRange.BoundHeight`, `TextFrame2.AutoSize`, `AddChart2`, `AddConnector`, `Presentation.Export`, `SaveAs(..., 32)`) and no Microsoft 365-exclusive features; standalone LTSC/perpetual Office 2021 machines have not yet been independently benchmarked.

Before R5, `f_layout/capacity.py` used `DEFAULT_LINE_HEIGHT_FACTOR = 1.30` and `DEFAULT_PADDING_PT = 6.0` (derived from SVG/LibreOffice heuristics). On real Windows **Microsoft PowerPoint 16.0** (`Microsoft YaHei`, `TextFrame2.AutoSize = msoAutoSizeNone`), we measured actual `TextFrame2.BoundHeight` across 6 CJK, Latin, and mixed-script academic text samples (12 pt – 28 pt):

| Sample | Box Width (in) | Font Size (pt) | Script | Wrapped Lines | PowerPoint `BoundHeight` (pt) | Pre-R5 Est (`1.30`, pt) | Pre-R5 Error | Post-R5 Est (`1.20`, pt) | Post-R5 Error |
|---|---:|---:|---|---:|---:|---:|---:|---:|---:|
| Short CJK title | 12.03 | 28 | CJK | 1 | 33.6 | 36.4 | +8.3% | 33.6 | 0.0% |
| 3 CJK bullets | 5.40 | 18 | CJK | 5 | 108.0 | 117.0 | +8.3% | 108.0 | 0.0% |
| Dense CJK/Latin mixed | 5.40 | 16 | Mixed | 6 | 115.2 | 124.8 | +8.3% | 115.2 | 0.0% |
| Long English paragraph | 5.40 | 14 | Latin | 5 | 84.0 | 91.0 | +8.3% | 84.0 | 0.0% |
| Attribution caption | 6.00 | 10 | Mixed | 2 | 24.0 | 26.0 | +8.3% | 24.0 | 0.0% |
| Dense 12pt academic note | 5.40 | 12 | Mixed | 7 | 100.8 | 109.2 | +8.3% | 100.8 | 0.0% |

**Calibration Summary:**
- Real PowerPoint line pitch (`BoundHeight / lines / font_size_pt`) for `Microsoft YaHei` is **1.20×** (consistent across 10 pt – 28 pt), with default vertical textbox margin `7.2 pt` (`0.10 in` top+bottom).
- Updating `DEFAULT_LINE_HEIGHT_FACTOR` from `1.30` → `1.20` and `DEFAULT_PADDING_PT` from `6.0` → `7.2` reduced mean line-height estimation error from **+8.3% to 0.0%** on the 6-sample Office 2021 probe, while preserving identical wrap line counts across all 6 samples.
- Added `estimate_text_height_in(lines, font_size_pt)` to compute calibrated box height so Stage F shrinks text boxes tightly to content.

## 2. Layout Overhaul (B6 Remediation)

Previous Smoke 3 renders exhibited three systematic visual defects (B6):
1. **Oversized empty rounded cards (`card_main`, `card_text`)** spanning the entire slide height even when a slide had only 2–3 short bullets.
2. **Inconsistent small centered images** caused by shrinking every photo's bounding box to its exact aspect ratio instead of cropping cleanly into a uniform visual slot.
3. **Subtle grid alignment drift** (`title` width `12.00 in` vs body/footer `12.03 in`, 2.16 pt off).

R5 replaces those defaults in `src/pptv01/f_layout/stage.py`:
- **No full-page background cards**: Removed `card_main` and `card_text`. Replaced with clean academic typography anchored to a slim vertical `accent_rail` (`0.06 in`) whose height matches the content-fitted `bullets` box.
- **Content-shrunk text boxes**: `bullets` height shrinks to `estimate_text_height_in(fit.lines, fit.font_size_pt) + 0.24 in` cushion instead of occupying full slide height.
- **Uniform `crop_fill` image slots**: Standard landscape/square/moderate-portrait images (`0.75 <= aspect_ratio <= 2.40`) use uniform slot dimensions (`6.00 in` wide on split slides) filled via PowerPoint COM `crop_fill` (no stretching, no tiny centered gaps). Extreme portraits (`< 0.75`) and panoramas (`> 2.40`) still adapt slot aspect ratio to avoid chopping >50% of the subject.
- **Template/placeholder driven**: Native PowerPoint title placeholder (`set_placeholder_text`) + optional `.potx` slide master (`PPTV01_TEMPLATE_POTX`, copied into `run_dir/templates/` so `deck_ops.json` keeps relative paths).
- **Exact grid margins**: Left `0.65 in`, usable width `12.03 in` (right edge `12.68 in`), gutter `0.43 in`, top alignment `1.50 in` across header divider, title, text panel, visual slot, and footer (`0.0 pt` alignment drift).
- **Default CJK font**: Switched from `Noto Sans CJK SC` (often absent on Windows) to `Microsoft YaHei` (installed on Windows Office 2021, set on both `Font.Name` and `Font.NameFarEast`).

## 3. New QA Rules (`h_qa/geometry.py`, `h_qa/inspect.py`)
- `find_empty_cards()`: Flags background cards with 0% content or `< 8%` effective ink fill (using measured PowerPoint `BoundHeight` when available).
- `find_undersized_image_slots()`: Flags non-icon pictures below `2.0%` of slide area or `< 96 pt` on both dimensions.
- `find_edge_misalignments()`: Flags foreground edge alignment drift in the `1.0–3.5 pt` band (adapted from `thomas-villani/pptlive`, MIT).
