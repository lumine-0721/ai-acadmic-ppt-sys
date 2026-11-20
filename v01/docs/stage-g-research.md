# 阶段 G（python-pptx 生成原生 `deck.pptx`）：现成方案调研 + 融合决定（一页）

日期：2026-11-20。原则：先查成熟组件与实现方案，再决定融合或自研。

| 需求 | 候选方案 | 许可证 / 实测与依据 | 决定 |
| --- | --- | --- | --- |
| 原生 PPTX 编译（文本框、矩形、图片、图表、表格、备注） | [scanny/python-pptx](https://github.com/scanny/python-pptx) (`python-pptx>=1.0`，项目已依赖) | MIT。原生支持 `add_textbox`、`add_shape`、`add_picture`、`add_chart(CategoryChartData)`、`add_table`、`notes_slide`；支持 `shape.line.dash_style`（`MSO_LINE_DASH_STYLE.DASH`）绘制虚线框 | **直接采用**，不引入外部转换器（避免 HTML/PDF/SVG 转全页截图） |
| SVG / HTML 转 PPTX（如 PPT Master / DeepPresenter 路线） | `hugohe3/ppt-master`、`marp`、`reveal.js` | 会把整页转成静态图片或需要额外无头浏览器链路，破坏“可编辑原生 PPTX”目标 | **不采用**；只用 `python-pptx` 原生对象逐元素构建 |
| 数据图表 & 流程图 | `python-pptx` 原生 `CategoryChartData` + 原生连接线/节点形状 | 图表数据直接来自 `inputs/` 或 `assets/` 中带出处字段的 JSON/CSV；结构化流程图 JSON `{nodes, edges}` 画成原生形状组；无外部数据时绝不造图 | **自研轻量渲染器**，严格绑定来源与许可 |

## 核心实现与硬门槛闭环
1. **原生内容而非整页截图**：所有文字/要点/标题用原生文本框（`word_wrap = True`），背景卡片用原生 `MSO_SHAPE.ROUNDED_RECTANGLE` / `RECTANGLE`，外部图片用 `add_picture` 嵌入并按 `ASSET_A###__...` / `INPUT_U###__...` 命名；图表（`CHART_INPUT_U###`）和表格（`TABLE_INPUT_U###`）只消费带出处的数据文件。
2. **占位与备注规范**：`ElementKind.placeholder` 生成带虚线框的原生形状，命名 `PLACEHOLDER_U###`，文字固定为 `待补充：<need>（U###）`，并同步写入该页 `notes_slide`。
3. **来源与许可强制检查**：每页备注写清该页使用的素材来源、作者与许可；对需要署名的素材（如 `CC BY` / `CC BY-SA`），校验页面文本框/页脚或备注中确实包含署名文本，缺失则报错失败（`AttributionMissingError`）。
4. **硬门槛打通**：`grading/gates.py` 中的 `pptx_opens`（可重新打开且页数等于计划页数）与 `native_content_not_screenshots`（检测幻灯片不是单张覆盖全画布的整页截图且含原生文本/对象）从 `not_run` 变为真实检查。
