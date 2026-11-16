---
name: academic-ppt-orchestrator
description: 调用 F:/codex/projects/academic-ppt/orchestrator 里的完整编排包，调度 APC、APRD、SEAA 三个包。不要在技能正文里现写调度流程。
---

# academic-ppt-orchestrator

这个技能只负责调用已经打包的实现，不在对话里另写一套规则或脚本。

包目录：F:/codex/projects/academic-ppt/orchestrator

先读该目录里的原过程文件和 references，再运行下面的入口。确定性检查必须走这些命令。

set PYTHONPATH=F:/codex/projects/academic-ppt/orchestrator/src
python -m academic_ppt_orchestrator run --task TASK.json --out RUNS --run-id RUN_ID
python -m academic_ppt_orchestrator resume --run-dir RUN_DIR --thread-id RUN_ID
