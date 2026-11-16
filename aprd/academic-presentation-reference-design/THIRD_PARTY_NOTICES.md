# Third-Party Open-Source Notices — APRD Engine

This directory (`academic-presentation-reference-design`) integrates and adapts open-source components under permissive licenses (MIT / BSD-3-Clause). No unlicensed or copyleft-restricted source code has been copied.

---

## 1. Adapted Open-Source Code

### 1.1 `hugohe3/ppt-master`
- **Repository**: `https://github.com/hugohe3/ppt-master`
- **Commit SHA**: `f3633771fe699d327f02ae86393831bbcf5169f7`
- **Original Files Referenced / Adapted**:
  - `scripts/beautify_identity.py` (Theme palette, font scheme, Master `p:txStyles` level ramp vs. observed run-level frequency sampling)
  - `scripts/pptx_to_svg/color_resolver.py` (OOXML `clrMap` / `clrMapOvr` resolution and color modifiers `lumMod`, `lumOff`, `tint`, `shade`, `satMod`, `alpha`)
  - `scripts/svg_quality/slot_capacity.py` (Binary-search CJK and Latin character-per-line capacity estimation)
- **Adaptations in APRD**:
  - Adapted into `scripts/aprd_engine/ooxml_resolver.py` and `scripts/aprd_engine/pattern_generalizer.py` for direct OOXML-to-DesignSpecification extraction without requiring intermediate full-deck SVG conversion.
- **License (MIT)**:
```text
MIT License
Copyright (c) 2025-2026 Hugo He

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.
```

---

### 1.2 `jameshemson-slides` (`slides` plugin)
- **Repository**: Local skill repository `C:/Users/86139/.codex/skills/jameshemson-slides`
- **Commit SHA**: `245c0e6858df9afe6a3381e36244812f3d2e4452`
- **Original Files Referenced / Adapted**:
  - `source/skills/build-deck/scripts/tokens.py` (`grid_from_rects`, luminance-based `resolve_colour_roles`)
  - `source/skills/build-deck/scripts/lint_pptx.py` (`composite` alpha-over-ground blending, contrast ratio and overlap linting)
- **Adaptations in APRD**:
  - Adapted into `scripts/aprd_engine/geometry_semantics.py` and `scripts/aprd_engine/reconstructor_verifier.py` with academic slide role extensions and pairwise alignment constraint graphs.
- **License (MIT)**:
```text
MIT License
Copyright (c) 2026 James Hemson

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.
```

---

### 1.3 `anotb/pptx-masters`
- **Repository**: `https://github.com/anotb/pptx-masters`
- **Version / Branch**: `main` (`v0.1.1`)
- **Original Files Referenced / Adapted**:
  - `src/parser/colors.js` (ECMA-376 `PRESET_COLORS` mapping and broken all-white/identical theme palette fallback detection)
  - `src/parser/master.js` (`placeholderDefaults` vs. `staticShapes` separation)
- **Adaptations in APRD**:
  - Ported from JavaScript to Python in `scripts/aprd_engine/ooxml_resolver.py`.
- **License (MIT)**:
```text
MIT License
Copyright (c) anotb
```

---

## 2. Runtime Library Dependencies

- `python-pptx==1.0.2` (MIT License, Copyright (c) 2012-2024 Steve Canny)
- `lxml==6.1.3` (BSD-3-Clause License)
- `pillow==13.0.0` (MIT-CMU License)
- `jsonschema==4.25.1` & `referencing==0.37.0` (MIT License)
