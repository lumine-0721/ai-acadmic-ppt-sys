# 评分:PPTEval 口径 + 独立硬门禁

本文补充(不改动)冻结的 `V0.1-基准测试规范.md` 第 6 节。冻结的是题目与分层(`benchmark/v1.0`),质量评分口径按下表执行(Regression / Full)。

## 为什么换口径(GitHub-search-first 的结果)
成熟方案 [PPTAgent/PPTEval](https://github.com/icip-cas/PPTAgent)(MIT,ACL/EMNLP 论文)已有经过验证的三维度 1–5 分评分提示词。
我们不再自写评分提示词,直接**原文**采用并保留版权声明(见 `NOTICE.md`、`src/pptv01/grading/ppteval_prompts/`)。

## 两层,互相独立
1. **硬门禁(确定性,无模型)** — `pptv01 gates <run_dir>` → `gate_report.json`
   合同文件齐全且可校验、run_id 一致、用户明示的页数上限/页数、素材有来源与许可、PPTX 可打开且非整页截图。
   尚未实现的阶段记为 `not_run`(整体 `incomplete`),**绝不记为通过**。
2. **质量(PPTEval,模型或 agent 评判)** — `pptv01 grade <run_dir> [--slides-dir DIR]` → `grade_report.json`
   | PPTEval 维度 | 对应原规范 | 说明 |
   |---|---|---|
   | Content(逐页 1–5) | 内容 | 逐页描述后评分,取平均 |
   | Design(逐页 1–5) | 视觉 | 需要渲染后的页面图片;没有图片时标记 `not_assessable`,不编分 |
   | Coherence(整套 1 分) | 编排(结构/衔接) | 先抽取每页描述与背景信息,再评分 |
   原规范的"素材"维度由硬门禁(来源/许可)和 D 阶段登记表承担,不再由模型打分。
   通过规则沿用原规范 6.3:**每个被评维度均 ≥ 3**;有维度无法评估时 `passed=null`(未定),有维度 <3 则 `passed=false`。

## 证据等级
- `pptx_render`:真正的 PPTEval 流程(渲染图 → MLLM 描述 → 文本评分)。需要 G/H 阶段产出 PPTX 与渲染图。
- `plan_text_proxy`:G/H 未实现前,仅用 `slide_plan.json` 文本评 Content 与 Coherence,Design 为 `not_assessable`。
  这是**计划层代理**,不是对 PPTX 的 PPTEval,报告里标明,且不得当作"通过"(合同层面禁止 `passed=true`)。

## 评判后端(环境变量;当前均未配置)
- `PPTV01_GRADER_BACKEND=api`(默认):`PPTV01_GRADER_BASE_URL` / `_API_KEY` / `_MODEL`,可选 `_VISION_MODEL`(须多模态)。未配置即报错,**不回退到假分数**。
- `PPTV01_GRADER_BACKEND=agent` + `PPTV01_GRADER_EXCHANGE_DIR`:文件交换,由宝特手工评分,
  记录为 `agent:宝特 (manual, not an API model)`。与生成端同一人,**非独立评审**;盲测与正式 Regression/Full 应优先用 API 模型或第三方复核。

## 与 PPTEval 的偏离(写入每份 grade_report)
提示词原文;占位符用 `str.replace`;通过规则为"各维 ≥3";逐页分数按套取平均。
