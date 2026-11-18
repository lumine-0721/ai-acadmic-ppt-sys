---
name: scientific-evidence-asset-acquisition
description: 调用 <仓库根目录>/seaa 里的完整 SEAA 包来审计证据、构建证据包和运行 research_core。不要在技能正文里编造检索结果。
---

# scientific-evidence-asset-acquisition

这个技能只负责调用已经打包的实现，不在对话里另写一套规则或脚本。

ACADEMIC_PPT_ROOT 指本仓库的检出目录（含 apc/、aprd/、seaa/、orchestrator/ 的那一层），使用前先设置该环境变量。

包目录：$ACADEMIC_PPT_ROOT/seaa

先读该目录里的原过程文件和 references，再运行下面的入口。确定性检查必须走这些命令。

python $ACADEMIC_PPT_ROOT/seaa/seaa_pkg_cli.py evidence-validate EVIDENCE_PACKAGE --handoff PPT_HANDOFF.json
python $ACADEMIC_PPT_ROOT/seaa/seaa_pkg_cli.py build-package BUNDLE --out-dir EVIDENCE_PACKAGE --handoff PPT_HANDOFF.json
python $ACADEMIC_PPT_ROOT/seaa/seaa_pkg_cli.py probe-pdf SOURCE.pdf --out OUT.json
