# 渲染层改用 Office 自动化：现成方案调研 + 融合决定

日期：2026-11-20（调研与结论由宝特做；Antigravity 实施前请先读完，再按 `HANDOFF.md` 第 5 节的任务清单落地）。
原则：**GitHub-search-first、copy-reuse-first**；好改的就大改，不为"保留旧代码"而保留。
用户方向（2026-11-20）：不用 python-pptx 作渲染/生成器，改用 Office COM / VBA / Office.js 自动化；保留现有流水线里的好思路（契约、素材登记、映射、版式计划、硬门槛）。

## 0. 结论先行

1. **主路线 = Windows 上的 PowerPoint COM，用 Python + pywin32 驱动**。理由：只有 COM/VBA 对象模型同时具备：原生图表（`AddChart2`）、原生表格、连接线（`AddConnector` + `BeginConnect/EndConnect`）、母版/版式/占位符、真实文字测量（`TextFrame2.TextRange.BoundHeight/BoundWidth`）、整页导出 PNG/PDF（`Presentation.Export`、`ExportAsFixedFormat`）。这些恰好是我们现在 G/H 缺的（见 `HANDOFF.md` 第 4 节评审结果 B5/B6：画不出连线、文字溢出靠估算、QA 看不到真实渲染）。
2. **VBA 不当主路线**：只当"COM 对象模型的参考文档/录制宏的取代码来源"。VBA 没有包管理、没有单测、JSON 解析要靠第三方，和我们"契约 + 测试"的工作方式不合。
3. **Office.js 不当渲染器**：它是 add-in（要在运行中的 PowerPoint 里跑），而且 **PowerPoint 的 Office.js 到 1.10 仍没有"添加图表"，也不能读写图表数据**（官方 issue 答复："you can not read or write charts in powerpoint programatically"）；表格要 1.8（Microsoft 365 版本 2504+）。它适合做**第二阶段的"把生成好的 deck 插进当前文档/在任务窗格里改"**，不适合现在做生成。
4. **python-pptx 的去留建议**：从**生成路径**里拿掉；只作为**只读校验器**（硬门槛 / 测试里"独立于生成器"地读 `.pptx`），放进可选 extra `[verify]`，代码里明确标注 read-only。用户若连只读也不想要，可用 `zipfile + lxml` 直接解析 OOXML（`aprd/.../ooxml_resolver.py` 已有现成写法可借）。**我们建议保留只读**，因为"生成器自己证明自己没造假"不是独立检查（见 HANDOFF 评审 B4：硬门槛可被改名骗过）。
5. **架构关键：把 F 的输出和渲染器解耦**。F 只产出后端无关的 `page_design.json`；新增一个纯 Python 的"编译层"把它编成确定性的 `deck_ops.json`（一串 add_slide/add_text/add_picture/... 操作）；再由 **COM 执行器**在 Windows 上执行。编译层和契约可以在 Linux/CI 上测，执行器只管"把操作翻成 COM 调用"。

## 1. 用户机器情况（已知 / 未知）

- **已知（来自仓库历史）**：旧包 `aprd/` 与 `orchestrator/` 来自作者的 **Windows** 机器（`PORTABILITY.md`：`C:/Users/86139/...`），而且已经用过 PowerPoint COM：
  - `aprd/academic-presentation-reference-design/scripts/aprd_engine/reconstructor_verifier.py:468 render_and_measure_via_powerpoint_com`：PowerShell + `New-Object -ComObject PowerPoint.Application`，`Presentations.Open(path, -1, 0, 0)`（只读、无窗口），`Export(outDir,"PNG",w,h)`，并逐形状读 `TextFrame2.TextRange.BoundHeight` 判溢出；
  - `orchestrator/README.md` 记录过 "PowerPoint opened probe.pptx read-only: 2 slides"。
  → **可以合理假设用户 Windows + 桌面版 PowerPoint 可用**；**该假设已被用户确认**：Microsoft 365 桌面版 PowerPoint（Version 16.0，Build 20257.20196，Current Channel）、真 PowerPoint、本机运行（见第 7 节）。
- **我的 box 是 Linux**：不能跑 COM。所以 COM 相关的验收只能在用户的 Windows 上做；Antigravity 若运行在用户那台 Windows 上，可以直接跑。
- 微软官方不支持无人值守/服务端自动化 Office（[KB257757](https://support.microsoft.com/en-us/visio/considerations-for-server-side-automation-of-office)：非交互环境可能死锁、弹窗卡死）。**结论：这是"用户桌面上跑"的工具，不是云服务**；要上云需要 Windows VM + 交互桌面会话 + 每进程隔离，或另选方案。这点要写进 README，别让人误以为能放服务器。

## 2. 候选方案清单（已用 `gh api` 核对许可证/活跃度，2026-11-20）

### 2.1 COM（Python 驱动）

| 项目 | 许可证 | 活跃度 | 适配度 / 说明 | 决定 |
| --- | --- | --- | --- | --- |
| [mhammond/pywin32](https://github.com/mhammond/pywin32) | PSF（仓库 license 字段为空，随包带 PSF 许可文本；以 PyPI 元数据为准，Agent 引入前请自行核对） | 5.6k★，2026-11-12 仍在推送 | Python 调 COM 的事实标准；`win32com.client.DispatchEx("PowerPoint.Application")` | **采用为底层依赖**（只依赖，不拷贝） |
| [enthought/comtypes](https://github.com/enthought/comtypes) | MIT（LICENSE.txt：Thomas Heller / Comtypes Developers） | 350★，2026-11-19 | 另一种 COM 绑定，类型库生成更严格 | 备选；不同时用两套 |
| [thomas-villani/pptlive](https://github.com/thomas-villani/pptlive) | **MIT** | 1★（很新，2026-05 创建，2026-09 最后推送）但**工程质量高**：~600KB 源码，有 CLI/MCP、`_charts.py`、`_tables.py`、`_snapshot.py`（逐页 PNG）、`_linting_geometry.py`（几何检查）、`export_pdf`，**测试用纯 Python fake COM（`tests/conftest.py` 的 `fake_powerpoint`），GitHub Actions 里 Linux 也能跑** | **最贴合"copy reuse"**：对象模型封装 + 带假 COM 的测试夹具 + 几何 lint | **优先复制/改写**（保留 MIT 声明，写进 `NOTICE.md`）；因星数少，**不整包当运行时依赖**，按需摘取模块并锁定 commit |
| [ykuwai/ppt-mcp](https://github.com/ykuwai/ppt-mcp) | **MIT** | 128★，2026-11-13 | 156 个工具；`src/ppt_com/` 下有 `charts.py`（注明：`AddChart2`/`ChartData.Activate()` 会开 Excel 进程，必须 `wb.Close(False)` 防泄漏；另有不用 Excel 的 gvml 写图路径）、`connectors.py`（连线+箭头）、`layout_check.py`（"用数字而不是看图来查版式问题"，把读取与算术分开以便无 PowerPoint 测试）、`export.py`、`tables.py`、`shape_lookup.py`、`themes.py`、`placeholders.py`；Windows + macOS | **第二复制来源**：图表（含 Excel 泄漏处理）、连接线、版式检查直接参考/改写；不引入其 MCP 层 |
| [trsdn/mcp-server-ppt](https://github.com/trsdn/mcp-server-ppt) | MIT | 42★，2026-11-03 | .NET 实现 + CLI/MCP；"native COM API" | 仅参考（语言栈不同） |
| [Ayushmaniar/powerpoint-mcp](https://github.com/ayushmaniar/powerpoint-mcp) | **无许可证字段（none）** | 130★ | pywin32 MCP，含快照/占位符/模板 | **不复制代码**（无许可证=保留所有权利）；只当思路参考 |
| [NetOfficeFw/NetOffice](https://github.com/NetOfficeFw/NetOffice) | MIT | 769★，2026-10 | .NET 版本无关的 Office 互操作封装 | 若将来不用 Python 才考虑；目前不采用 |
| 旧仓库 `aprd/.../reconstructor_verifier.py:468` | 自有 | — | 已验证过的 PowerShell COM 导出 PNG + `BoundHeight` 溢出探测，**连 `-1,0,0` 参数都对** | **第一优先复用**（自家代码，直接改成 Python/pywin32 版本） |

### 2.2 VBA

- 对象模型文档即 COM 文档：`Presentation.Export`、`ExportAsFixedFormat`、`Slides.InsertFromFile`、`Shapes.AddChart2`、`TextRange2.BoundHeight`。
- 实际用法：**录制宏取调用序列 → 翻成 Python/pywin32**；VBA 里再嵌 [VBA-tools/VBA-JSON](https://github.com/VBA-tools/VBA-JSON)（MIT，2024-03 后无更新）读契约，不如直接让 Python 读。**不采用 VBA 作为运行时。**
- 唯一可能保留 VBA 的场合：把成品 deck 里需要的小宏（如批量重置版式）交给用户手动用——不在 V0.1 范围。

### 2.3 Office.js（PowerPoint add-in）

| 能力 | 需求集 / 版本 | 备注 |
| --- | --- | --- |
| 形状、文本、图片、线 | 1.4+ | `addGeometricShape`、`addLine`、`addTextBox` |
| 表格 `addTable`、绑定、`Slide.getImageAsBase64` | **1.8**（M365 Windows 2504+，Mac 16.96+） | 渲染图可取，但是 add-in 内取 |
| 表格样式 | 1.9（2508+） | |
| `Shape.getImageAsBase64`、背景、超链接、`exportAsBase64Presentation` | 1.10（2601+，Windows 构建 19610） | 很新，**永久授权版 / LTSC 用户可能没有** |
| `insertSlidesFromBase64` | 1.2 | 可把别处生成的 pptx 插入当前文档 |
| **图表创建与读写** | **没有** | 官方 issue（OfficeDev/office-js #5463）：图表读写不可用；社区绕法是用 PptxGenJS 另生成 pptx 再 `insertSlidesFromBase64` |

- 运行方式：必须由运行中的 PowerPoint（桌面或网页）加载 add-in，Python 无法"无头"驱动；本地调试用 [OfficeDev/Office-Addin-Scripts](https://github.com/OfficeDev/Office-Addin-Scripts)（MIT）+ 样例库 [OfficeDev/Office-Add-in-samples](https://github.com/OfficeDev/Office-Add-in-samples)（MIT，1k★，活跃）。
- 结论：**图表缺失与我们的"数据图只能来自带出处的用户数据"硬需求正面冲突；且不能自动化批量出 deck。** 放到第二阶段做"在当前文档里一键插入/替换某页"。该阶段的 deck 来源仍是 COM 生成的 pptx（`insertSlidesFromBase64`）。若坚持跨平台，需要 PptxGenJS（MIT，6.4k★，但 2025-11 后未推送）——属于"换一个生成库"，并非 Office 自动化。

### 2.4 参考（不做渲染，只借思路）

- [icip-cas/PPTAgent](https://github.com/icip-cas/PPTAgent)（MIT，5.2k★，活跃）：以 python-pptx 为后端，但"版式模板编辑 + 对每页做 slot 对齐"的思路与我们的 E/F 一致，可借鉴**模板/版式驱动**而不是自由摆放。
- [hugohe3/ppt-master](https://github.com/hugohe3/ppt-master)（MIT，78k★，活跃）：SVG→原生 PPTX 路线；我们已借其 `slot_capacity`。若 COM 渲染后某些页面容量估算与实测不符，可用 COM 实测的 `BoundHeight` **反过来校准** `slot_capacity`（见任务 R5）。

## 3. 推荐架构

```
slide_plan.json ─┐
asset_registry ──┼─ E mapping ─ F design ──► page_design.json   （后端无关；英寸坐标、元素、绑定、占位）
user_inputs ─────┘                                │
                                                  ▼
                              g_build/compile_ops.py  （纯 Python、确定性、可在 Linux 测）
                                                  │  deck_ops.json  ← 新契约 DeckOps，schema_version 0.1
                                                  ▼
                       Renderer 协议 ┬─ PowerPointComRenderer（Windows；pywin32）   ← 主
                                    └─ （将来）OfficeJsRenderer / 其它
                                                  │  deck.pptx  + deck_dump.json（COM 读回的形状清单）
                                                  ▼
H：export_png/pdf（COM）→ measure（BoundHeight 等）→ QA → 修复（改 page_design 再编译，不手改文件）→ 复验
```

### 3.1 Renderer 协议（建议签名，Agent 可调整但要写理由）

```python
class DeckRenderer(Protocol):
    name: str                       # "powerpoint-com"
    def preflight(self) -> RendererInfo: ...                 # 版本、是否可用、有无 Excel、字体表；不可用就明确报错
    def build(self, ops: DeckOps, run_dir: Path) -> BuildResult: ...     # 产出 deck.pptx + deck_dump.json
    def export(self, pptx: Path, out_dir: Path, width_px: int = 1920) -> ExportResult: ...  # slide_001.png + deck.pdf
    def measure(self, pptx: Path) -> MeasureResult: ...      # 每个文本形状的 BoundHeight/BoundWidth、字体、自动缩放状态
```
- `DeckOps` 里每个操作都带 `element_id`、`bind`（`A###`/`U###`/`PLACEHOLDER_U###`）和 `source_refs`；执行器只许执行，不许"补画"（没有绑定的视觉元素直接拒绝执行）。
- **占位、署名、备注、命名约定沿用现有（`ASSET_A###__...`、`INPUT_U###__...`、`PLACEHOLDER_U###`）**，所以现有硬门槛的思路继续有效。

### 3.2 PowerPoint 原生做法（让"可编辑"变真）

- 从一个 `.potx` 模板（版式/母版/主题字体）建 deck；标题和正文使用**原生占位符**（`Shapes.Placeholders`），不是到处放文本框——这样大纲视图、重置版式、换主题才能正常工作（python-pptx 当前实现全是文本框）。
- 中文字体：同时设置 `Font.Name` 与 `Font.NameFarEast`；字体缺失要在 preflight 报告。
- 图表：`AddChart2` + `ChartData.Workbook` 写数据，`finally: wb.Close(False)`（防 Excel 进程泄漏）——**需要装了 Excel**；ykuwai/ppt-mcp 里有不依赖 Excel 的写图路径（gvml），若用户没装 Excel 可评估是否搬。
- 图/流程图：节点 `AddShape`，边 `AddConnector` + `ConnectorFormat.BeginConnect/EndConnect`；**节点/边数量超出版面时要换行/分页或报错，不得静默截断**（现 G 的缺陷）。
- 图片：`AddPicture(LinkToFile=False, SaveWithDocument=True)`，按槽位**裁切填充**（`PictureFormat.Crop`）而不是缩小留白。
- 不要碰用户已打开的演示文稿：用 `DispatchEx` 起独立实例，或只操作自己创建/打开的 `Presentation` 对象，结束时只关自己的；`Application.DisplayAlerts = False`、`AutomationSecurity = msoAutomationSecurityForceDisable`；PowerPoint 窗口不能被设为不可见，用 `Presentations.Open(..., WithWindow=msoFalse)` / `Presentations.Add(WithWindow=msoFalse)`。

### 3.3 H（渲染 + QA）怎么用 PowerPoint 本体做

1. **导出 PNG**：`Presentation.Export(dir, "PNG", w, h)`（PowerPoint 自己命名为 `Slide1.PNG`…，要重命名成 `slide_001.png`；旧脚本里已有处理）。**导出 PDF**：`ExportAsFixedFormat(path, 2 /*ppFixedFormatTypePDF*/ ...)`。
2. **实测文字尺寸**：`TextFrame2.TextRange.BoundHeight/BoundWidth` 对比形状高宽 → `text_overflow` 证据是**测量值**而不是估算值；同时读 `TextFrame2.AutoSize`、实际字号、`Font.NameFarEast`。
3. **几何检查**：越界、遮挡、间距不齐——直接摘 pptlive `_linting_geometry.py` / ykuwai `layout_check.py`（纯算术部分，可在 Linux 上单测）。
4. **缺字体**：对比 `Presentation.Fonts` 与系统字体。
5. **修复**：只允许改 `page_design.json`（字号、框、拆页）→ 重新编译 → 重新 build → 复验；每轮记录；有上限。
6. **LibreOffice 渲染降级为"第二渲染器交叉检查"**：Linux 上仍可对 Windows 产出的 `deck.pptx` 做 `soffice` 渲染，抓"换个渲染器就崩"的兼容问题；**不再作为主渲染证据**，`render_provenance` 要写明是哪个渲染器。
7. **视觉模型审图**：`PPTV01_E_VISION_MODEL` 同类的可选环节，对 PNG 判断"留白过大/图文不符"。QA 现在看不出这些（见 HANDOFF 评审 B6）。

## 4. 测试策略（谁在哪测）

| 层 | 在哪测 | 方式 |
| --- | --- | --- |
| 契约（`DeckOps`）、编译层 `page_design → deck_ops` | Linux + Windows | 纯 pytest，黄金文件 |
| COM 执行器 | Linux（CI）+ Windows | **假 COM**（摘 pptlive `tests/conftest.py::fake_powerpoint` 思路，只放 `tests/`），验证"哪些调用以什么参数发出"；绝不进包代码 |
| 真 PowerPoint | **仅 Windows** | `@pytest.mark.windows_com`，非 Windows 或无 PowerPoint 时 skip 并写明；本地 Smoke 4 |
| 硬门槛 | Linux + Windows | 对**真实 COM 产出的 pptx** 跑（不是对 python-pptx 产物），含故意造假用例 |

## 5. 风险与诚实声明

- **只能在 Windows + 桌面 PowerPoint 上跑**；macOS 版 PowerPoint 的 COM 不存在（有 AppleScript，能力不等价），Linux 不可行。
- COM 慢且怕弹窗：每次 build 都要有超时、`DisplayAlerts=False`、失败时杀掉自己起的进程；不要在用户正在编辑别的演示文稿时抢占。
- `AddChart2` 依赖 Excel；Excel 进程泄漏是已知坑（ykuwai 源码注释）。
- Microsoft 365 版本差异：COM 对象模型向后兼容性好，但 `PowerPointApi` 需求集那一套只对 Office.js 有意义。COM 侧请在 preflight 里记录 `Application.Version` / `Build`。**实际环境是 Microsoft 365 Build 20257.20196**；策略是只用 Office 2021 / Office 16 基线里也有的 COM 成员，保证换到 2021/LTSC 机器也能跑。
- 这不是云服务方案（KB257757）。
- 我（宝特）在 Linux box 上**没有**实际运行过任何 COM 代码；以上 COM 行为是依据官方文档与上述开源项目的源码/README，真机行为以 Windows 实测为准。

## 6. 复用清单（落地时逐项登记到 `NOTICE.md`）

1. `aprd/.../reconstructor_verifier.py::render_and_measure_via_powerpoint_com`（自家）→ `renderers/powerpoint_com/export.py`、`measure.py`。
2. `thomas-villani/pptlive`（MIT）：对象模型封装、`_charts.py`、`_tables.py`、`_snapshot.py`、`_linting_geometry.py`、`tests/conftest.py` 的假 COM。
3. `ykuwai/ppt-mcp`（MIT）：`charts.py`（Excel 清理）、`connectors.py`、`layout_check.py`、`export.py`。
4. `hugohe3/ppt-master`（MIT）：已借的 `slot_capacity`，用 COM 实测校准。

## 7. 用户已确认的决定（2026-11-20，原"待回答问题"已关闭）

1. **Microsoft 365 桌面版 PowerPoint，Version 16.0，Build 20257.20196（Current Channel）**——用户实测更正（此前误记为 Office 2021）。Excel 由 `pptv01 doctor` 实测。**策略不变：只用 Office 2021 / Office 16 基线也有的 API，保证到处能跑**；不使用 Microsoft 365 专属特性（Office.js 1.8+ 新接口、Designer、Copilot、动态数组图表等）。`AddChart2` 自 Office 2013 起存在，已/将在本机（Microsoft 365）做最小实测（需 Excel）；**Office 2021 基线本身没有实机验证**，结论里要如实标注。
2. **真正的 Microsoft PowerPoint，不是 WPS**。
3. **Antigravity 与用户同一台本机 Windows**，可直接测 COM。
4. **接受 python-pptx 只读校验器**（`[verify]` extra）。
5. **云端批量：以后再说。** 用户有云服务器（免费额度机器，如 Linux Simular 盒子），但 Linux 不能跑 COM；需要 Windows VM + 交互会话才可行，后续再决定，本轮不投入。
