# 阶段 E（Slide Asset Mapping）：现成方案调研 + 融合决定（一页）

日期：2026-11-20。原则：先查现成方案与许可证，再决定融合或自研。

| 候选方案 | 仓库 / 标签 / 许可证 | 实测能力与代码结构 | 决定 |
| --- | --- | --- | --- |
| **PPTAgent** (`pptagent/pptgen.py`, `pptagent/document/`) | [icip-cas/PPTAgent](https://github.com/icip-cas/PPTAgent) `v1.1.38`，**MIT**（Copyright (c) 2025 ICIP-CAS） | 先做 `source_doc.validate_medias(image_dir)` 校验真实媒体文件存在性；按页面是否有已核验媒体在 `text_layouts` 与 `multimodal_layouts` 间分流；支持 `vision_model` 视觉核验 | **融合思路**：只映射真实已取回或用户已提供的素材；无素材页面诚实标记为纯文本/占位；支持可选 VLM 看图核验相关度 |
| **DeepPresenter**（同仓库 `v1.1.38` 新增目录 `deeppresenter/agents/`） | [icip-cas/PPTAgent](https://github.com/icip-cas/PPTAgent) `v1.1.38`，**MIT** | 基于工具调用循环（`Agent` + MCP/Docker 沙盒 + Playwright/HTML），且 README 明确声明 **不支持 Windows**，依赖数十个重型包（`docker`、`fastmcp`、`gradio`、`markitdown`、`torch` 等） | **不引入运行时**：与 V0.1"单线程线性链 + 纯盘上 JSON 契约 + 跨平台轻量 CLI"冲突 |
| 本项目已有基础设施（`llm.py` + `contracts.py`） | 本仓库 `v01/src/pptv01/` | 已有 `SlideAssetMapping` 契约草案、`ChatClient`（OpenAI 兼容 / `agent` 后端，带 1 次修复重试）、`user_input_requests.json` | **直接基于已有架构实现 E**，保持零新重型依赖 |

## 融合与诚实边界设计
1. **严格准入**：E 只能映射 `asset_registry.json` 中 `status` 为 `acquired`/`drawn`/`substituted` 的素材，或 `user_input_requests.json` 中 `status == "provided"`（对应到其 `asset_id` / `request_id`）的素材；`missing` / `open` 请求**绝对禁止**被映射成已有素材。
2. **明确用途与复用**：每条映射必须写具体 `usage`；同一素材可跨多页复用，但每页必须给出各自的具体用途；未使用的已取回素材必须列入 `unused_assets` 并写明原因，不许硬塞进无关页。
3. **视觉相关度诚实标注**：若配置了视觉模型环境变量（`PPTV01_E_VISION_MODEL`，以及可选 `PPTV01_E_VISION_BASE_URL` / `PPTV01_E_VISION_API_KEY`），对已映射图片调用 VLM 看图核验与页面主题的相关度并记录结果；未配置时在映射旁诚实记录 `relevance = "not_assessed"`（绝不假装检查过）。
4. **校验 + 1 次修复重试**：LLM 输出走与 A/B/C 相同的 JSON 解析 + 契约与业务规则校验 + 1 次修复重试机制。
