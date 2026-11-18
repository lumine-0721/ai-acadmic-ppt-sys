---
name: academic-ppt-orchestrator
description: 调用 <仓库根目录>/orchestrator 里的完整编排包，调度 APC、APRD、SEAA 三个包。不要在技能正文里现写调度流程。
---

# academic-ppt-orchestrator

这个技能只负责调用已经打包的实现，不在对话里另写一套规则或脚本。

ACADEMIC_PPT_ROOT 指本仓库的检出目录（含 apc/、aprd/、seaa/、orchestrator/ 的那一层），使用前先设置该环境变量。

包目录：$ACADEMIC_PPT_ROOT/orchestrator

先读该目录里的原过程文件和 references，再运行下面的入口。确定性检查必须走这些命令。

export PYTHONPATH="$ACADEMIC_PPT_ROOT/orchestrator/src"   # Windows cmd: set PYTHONPATH=%ACADEMIC_PPT_ROOT%/orchestrator/src
python -m academic_ppt_orchestrator run --task TASK.json --out RUNS --run-id RUN_ID
python -m academic_ppt_orchestrator resume --run-dir RUN_DIR --thread-id RUN_ID
