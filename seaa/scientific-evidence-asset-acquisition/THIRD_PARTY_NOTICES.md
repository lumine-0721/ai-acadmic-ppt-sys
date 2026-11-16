# Third-Party Notices & Open-Source Attribution

This software (`scientific-evidence-asset-acquisition` and the standalone `research-evidence-asset-core`)
integrates and adapts open-source libraries and engineering designs under permissive licenses.
No AGPL/GPL copyleft code is included.

---

## 1. Runtime Library Dependencies (Permissive Open-Source Licenses)

### 1.1 `pypdf` (`7.0.1`)
- **Repository**: https://github.com/py-pdf/pypdf
- **Commit / Tag**: `7.0.1`
- **License**: BSD-3-Clause License
- **Copyright**: Copyright (c) 2006-2008, Mathieu Fenniak; Phaseit, Inc.; pypdf contributors
- **Usage**: Read-only PDF page tree inspection, metadata dictionary extraction, and embedded bitmap stream extraction.

### 1.2 `pdfplumber` (`0.11.10`) & `pdfminer.six` (`20260107`)
- **Repository**: https://github.com/jsvine/pdfplumber
- **Commit / Tag**: `v0.11.10`
- **License**: MIT License
- **Copyright**: Copyright (c) 2015 Jeremy Singer-Vine
- **Usage**: Character/word coordinate extraction (`extract_words`), geometric table extraction (`find_tables`), and figure/caption spatial layout analysis.

### 1.3 `pypdfium2` (`5.15.2`) & `Pillow` (`13.0.0`)
- **Repository**: https://github.com/pypdfium2-team/pypdfium2 & https://github.com/python-pillow/Pillow
- **Commit / Tag**: `5.15.2` / `13.0.0`
- **License**: Apache-2.0 OR BSD-3-Clause (`pypdfium2`), MIT-CMU (`Pillow`)
- **Usage**: High-DPI compound scientific figure region rendering and cropping so vector plots, spectra, and multi-panel labels (`A/B/C/D`) are preserved intact without AGPL dependencies.

### 1.4 `jsonschema` (`4.25.1`, Optional)
- **Repository**: https://github.com/python-jsonschema/jsonschema
- **License**: MIT License
- **Usage**: Draft 2020-12 JSON Schema validation for `research-core-bundle.schema.json`.

---

## 2. Architectural & Algorithmic Design Adoptions

1. **AllenAI PDFFigures 2.0** (`https://github.com/allenai/pdffigures2`, Apache-2.0):
   - Adopted the caption-prefix spatial region heuristic and multi-panel bounding-box preservation concept in `research_core/pdf_processor.py` (clean-room Python implementation over `pdfplumber` + `pypdfium2`).
2. **Microsoft ResearchStudio / Paper2Assets** (`https://github.com/microsoft/ResearchStudio`, MIT) & **PaperForge** (`https://github.com/matvergara19/paperforge`, MIT):
   - Adopted the Unified Asset Manifest and Figure–Caption–Mention Context triad structure in `research_core/models.py` and `research_core/pdf_processor.py`.
3. **Future-House PaperQA2** (`https://github.com/Future-House/paper-qa`, Apache-2.0) & **paper-evidence** (`https://github.com/ChiShengChen/paper-evidence`, MIT):
   - Adopted the two-stage page-aware passage indexing, Evidence Card structure, quantitative measurement + experimental condition extraction, and six-stage verification state machine in `research_core/grounding.py` and `research_core/consistency.py`.
4. **PaperSeek** (`https://github.com/paperseek/paperseek`, MIT):
   - Adopted multi-source Crossref/OpenAlex normalization, DOI + Token Jaccard title deduplication, and `ABSTRACT_ONLY` vs `FULL_TEXT_LOCAL` separation in `research_core/discovery.py`.
