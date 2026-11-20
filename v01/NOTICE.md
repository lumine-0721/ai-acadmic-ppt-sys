# Third-party material

- **PPTEval prompts** (`src/pptv01/grading/ppteval_prompts/*.txt`): copied verbatim from
  [icip-cas/PPTAgent](https://github.com/icip-cas/PPTAgent) tag v1.1.38, `pptagent/prompts/ppteval/`.
  Copyright (c) 2025 ICIP-CAS, MIT License (full text in `LICENSE-PPTAgent.txt` beside the prompts).
  Zheng et al., *PPTAgent: Generating and Evaluating Presentations Beyond Text-to-Slides*, EMNLP 2025, <https://arxiv.org/abs/2501.03936>.
- **cn2an** (runtime dependency, MIT) <https://github.com/Ailln/cn2an>
- **hugohe3/ppt-master** (`slot_capacity.py` CJK/Latin glyph-advance capacity estimation idea adapted in `src/pptv01/f_layout/capacity.py`),
  MIT License, Copyright (c) 2025-2026 Hugo He, <https://github.com/hugohe3/ppt-master>.
- Optional: **fastembed** (Apache-2.0), **sentence-transformers** (Apache-2.0), **BAAI/bge-reranker-v2-m3** (MIT), **pyalex** (MIT).
