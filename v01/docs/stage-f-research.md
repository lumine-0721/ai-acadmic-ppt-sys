# 阶段 F（Page Design + Preview）：现成方案调研 + 融合决定（一页）

日期：2026-11-20。原则：先查现成方案与许可证，明确融合边界，不整包接入、不拼接两套生成流程。

| 候选方案 | 仓库 / 许可证 | 实际入口与能力 | 决定 |
| --- | --- | --- | --- |
| **PPT Master** | [hugohe3/ppt-master](https://github.com/hugohe3/ppt-master)（MIT，Copyright (c) 2025-2026 Hugo He） | 主路线为“LLM 生成全页 SVG → `svg_to_pptx` 转原生 PPTX”；另含 `scripts/svg_quality/slot_capacity.py`（基于字号与 CJK 1.02 / Latin 0.54 字宽因子的二分法每行容量估算，旧仓库 `aprd/.../pattern_generalizer.py` 已有改写先例） | **不接入 SVG→PPTX 整包流程**（避免拼接两套生成链路）；**融合** `slot_capacity` 的中英文容量估算思路，并在 `NOTICE.md` 保留 MIT 署名 |
| **PPTAgent** (`pptagent/pptgen.py`) | [icip-cas/PPTAgent](https://github.com/icip-cas/PPTAgent) `v1.1.38`（MIT） | 按“是否有真实媒体”在 `text_layouts` / `multimodal_layouts` 间分流；用长度因子控制文本不撑爆参考版式；依赖参考 `.pptx` 模板归纳 | **借鉴版式分流与容量保护思想**；不强依赖外部参考母版，直接面向 13.333×7.5 英寸契约 `page_design.json` |
| **Pillow 示意预览** | `pillow>=10`（项目已依赖，MIT-CMU） | 可直接按英寸坐标绘制矩形框、文字与图片缩略图并加水印 | **采用**，专画带 `"layout schematic, not a render"` 水印的版式示意图（真实渲染留给 H 的 LibreOffice） |

## 融合与实现边界
1. **画布与契约**：统一 `13.333 × 7.5` 英寸（16:9）；支持绑定 `asset_id`/`asset_path`、`request_id`（用户提供数据/图或 `PLACEHOLDER_U###` 占位框）、`chart`/`table`/`shape`/`text` 元素；F 不得发明未在 D/用户提供中登记的新素材。
2. **中英文容量估算与溢出保护**：融合 `slot_capacity` 的字宽模型（全角 CJK `1.02 * pt`、Latin `0.54 * pt`），按混排宽度逐行折行估算所需高度；若文本溢出文本框，依次尝试在字号下限内自动降字号、调整折行并把原因记入该页 `notes`，**绝不静默截断**。
3. **几何与约束校验**：检查画布边界、最小字号（如标题 ≥ 20pt、正文 ≥ 12pt、脚注 ≥ 9pt）、非背景元素重叠不可读冲突、图片按原图长宽比适配容器避免变形、以及用户明确约束（如 T36 的页数限制与少字/图文要求）并在调整时写入 `notes`。
4. **示意预览**：输出 `previews/S###.png`，页内及文件名/角标清晰标注 `"layout schematic, not a render"`，避免与 H 阶段的真实渲染混淆。
