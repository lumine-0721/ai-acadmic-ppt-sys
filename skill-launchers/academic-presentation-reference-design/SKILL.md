---
name: academic-presentation-reference-design
description: 调用 <仓库根目录>/aprd 里的完整 APRD 包，包括 aprd_cli 设计引擎和原有包校验。不要只靠提示词描述版式。
---

# academic-presentation-reference-design

这个技能只负责调用已经打包的实现，不在对话里另写一套规则或脚本。

ACADEMIC_PPT_ROOT 指本仓库的检出目录（含 apc/、aprd/、seaa/、orchestrator/ 的那一层），使用前先设置该环境变量。

包目录：$ACADEMIC_PPT_ROOT/aprd

先读该目录里的原过程文件和 references，再运行下面的入口。确定性检查必须走这些命令。

python $ACADEMIC_PPT_ROOT/aprd/aprd_pkg_cli.py extract SOURCE.pptx OUTPUT.json
python $ACADEMIC_PPT_ROOT/aprd/aprd_pkg_cli.py package-validate PPT_REFERENCE_PACKAGE --handoff PPT_HANDOFF.json
python $ACADEMIC_PPT_ROOT/aprd/aprd_pkg_cli.py inspect-pptx SOURCE.pptx OUT.json
