---
name: academic-presentation-reference-design
description: 调用 F:/codex/projects/academic-ppt/aprd 里的完整 APRD 包，包括 aprd_cli 设计引擎和原有包校验。不要只靠提示词描述版式。
---

# academic-presentation-reference-design

这个技能只负责调用已经打包的实现，不在对话里另写一套规则或脚本。

包目录：F:/codex/projects/academic-ppt/aprd

先读该目录里的原过程文件和 references，再运行下面的入口。确定性检查必须走这些命令。

python F:/codex/projects/academic-ppt/aprd/aprd_pkg_cli.py extract SOURCE.pptx OUTPUT.json
python F:/codex/projects/academic-ppt/aprd/aprd_pkg_cli.py package-validate PPT_REFERENCE_PACKAGE --handoff PPT_HANDOFF.json
python F:/codex/projects/academic-ppt/aprd/aprd_pkg_cli.py inspect-pptx SOURCE.pptx OUT.json
