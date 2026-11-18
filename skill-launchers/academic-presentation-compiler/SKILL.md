---
name: academic-presentation-compiler
description: 调用 <仓库根目录>/apc 里的完整 APC 包来编译学术演示任务书并运行 check_handoff。不要在技能正文里重写编译规则。
---

# academic-presentation-compiler

这个技能只负责调用已经打包的实现，不在对话里另写一套规则或脚本。

ACADEMIC_PPT_ROOT 指本仓库的检出目录（含 apc/、aprd/、seaa/、orchestrator/ 的那一层），使用前先设置该环境变量。

包目录：$ACADEMIC_PPT_ROOT/apc

先读该目录里的原过程文件和 references，再运行下面的入口。确定性检查必须走这些命令。

python $ACADEMIC_PPT_ROOT/apc/apc_cli.py validate PPT_REQUIREMENTS.md PPT_HANDOFF.json --strict-assets
python $ACADEMIC_PPT_ROOT/apc/apc_cli.py impact OLD.json NEW.json
python $ACADEMIC_PPT_ROOT/apc/apc_cli.py schema PPT_HANDOFF.json
