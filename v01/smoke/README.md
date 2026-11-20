# smoke/ — Smoke 4 回放答案（宝特手写）

`agent-answers/<Txx>/io/*_response_*.json` 是 agent 后端（`PPTV01_LLM_BACKEND=agent`）的 A/B/C/E 答案，`research_brief.json` 是冻结的研究笔记（避免联网检索结果漂移）。

- **不是 API 模型的产出**：这些答案是宝特手写的，运行报告里会标注 `agent:宝特 (manual, not an API model)`。
- 用法见 `v01/HANDOFF.md` 第 5B 节第 4 步。T02/T12/T26/T36 对应基准任务（`benchmark/v1.0`），**不要针对它们做硬编码**。
- 这些答案对应 `feature/v01-office-renderer@7ceb945` 的契约与校验；契约改动后若 agent 后端报 "awaiting agent response"，把对应 `*_request_*.json` 交给宝特重写，别自己编。
- 不含任何素材图片（D 阶段联网取）。
