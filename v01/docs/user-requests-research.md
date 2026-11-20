# 用户补充请求与占位机制：现成方案调研 + 融合决定（一页）

日期：2026-11-20。原则：先搜成熟方案，再决定融合或自研。

| 需求 | 候选方案 | 许可证 / 实测与依据 | 决定 |
| --- | --- | --- | --- |
| Human-in-the-Loop 中断与恢复 | [LangGraph](https://github.com/langchain-ai/langgraph) `interrupt()` / `Command(resume=...)` | MIT。依赖图状态机与 Checkpointer 持久化暂停现场，适合通用图编排，但会引入重型运行时与图状态模型，违背 V0.1 线性链 + 纯文件契约原则 | **不引入库**；借鉴其"强类型请求清单 + 外部响应落盘后 `--resume` 重入"模式 |
| 外部边界强类型校验 | [Pydantic v2](https://github.com/pydantic/pydantic)（本项目已依赖） | MIT。用统一模型校验人机边界输入，避免隐式缺省或非法字段透传 | **采用**（`UserInputRequests` + `ProvidedInput`） |
| 外部素材/数据溯源与哈希锁定 | 确定性清单 + SHA-256 校验（参考 `resume-contract` 等幂等契约思路） | 无需新依赖；Python 标准库 `hashlib` + Pillow 即可校验图片/JSON/CSV 与来源/许可字段 | **自研轻量实现**，因为 V0.1 要求极简线性盘上契约、禁止黑盒编排器 |
| 幻灯片未提供素材的渲染降级 | 学术排版/设计系统常见的 Explicit Placeholder（明确标记待补充框，而非随机配图） | 与 `python-pptx` 原生形状无缝结合，可同时满足 `no_fabricated_visuals` 与 `placeholders_marked` 硬门槛 | **自研**（`PLACEHOLDER_U###` 虚线框 + 页面备注） |

## 核心融合决定
- **自研原因**：现有开源 HITL 框架多绑定在图执行引擎或服务化队列上，而 V0.1 的架构约束是"单线程线性链 + `runs/<run_id>/` 盘上 JSON 契约 + 无数据库/无多 Agent 编排"。因此只吸收其核心模式（暂停产生强类型请求清单、独立校验用户提供材料、基于盘上状态 `--resume`），用极简 Pydantic 契约与 CLI 实现。
- **诚实边界**：D 阶段结束时确定性生成 `user_input_requests.json`（不调用 LLM），每个 `missing` 的需求都暴露为 `U###` 请求；`--on-missing wait` 明确返回退出码 4 并把 `run_report.overall` 标为 `needs_input`；`--on-missing placeholder` 允许渲染带明确占位的文稿并标为 `complete_with_placeholders`，永不伪装为 `complete`。
