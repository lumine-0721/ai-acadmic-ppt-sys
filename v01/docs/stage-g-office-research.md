# Stage G / H 渲染层改用 Office 自动化：核对调研与 Office 2021 实测决定

日期：2026-11-20（在宝特 `docs/renderer-office-research.md` 基础上，由编码 Agent 独立核对上游仓库许可证/commit 并在用户本机 Windows + Office 2021 真机完成最小 COM 探针）。

## 1. 上游候选方案独立核对（2026-11-20 实查 GitHub API / LICENSE / PyPI）

| 仓库 / 来源 | 核对后的许可证与版权 | 锁定 commit / 版本 | 关键可复用能力 | 融合决定 |
| --- | --- | --- | --- | --- |
| 自家 `aprd/academic-presentation-reference-design/scripts/aprd_engine/reconstructor_verifier.py:468` | 本仓库自有（只读参考，不改旧目录） | 本仓库 `4550cc1` | `Presentations.Open(path, -1, 0, 0)` 只读无窗打开、`Presentation.Export(dir, "PNG", w, h)`、逐形状 `TextFrame2.TextRange.BoundHeight/BoundWidth` 实测溢出 | **第一优先复用**：改写成 Python + `pywin32` 版本放进 `renderers/powerpoint_com/`，去掉其 AppContainer Pillow 静默回退（缺 PowerPoint 必须抛 `RendererUnavailableError`） |
| [mhammond/pywin32](https://github.com/mhammond/pywin32) | **PSF**（实查 PyPI `pywin32==312` metadata `License: PSF`；GitHub 仓库无 license 字段） | PyPI `312` / commit `682853556b39` (2026-11-12) | `win32com.client.DispatchEx("PowerPoint.Application")`、`pythoncom.CoInitialize` | **采用为 `[windows]` 可选依赖**（不拷贝源码） |
| [thomas-villani/pptlive](https://github.com/thomas-villani/pptlive) | **MIT**（实查 `LICENSE`：`Copyright (c) 2026 Tom Villani`） | `4b33e69217ba` (2026-09-01) | `_charts.py`（`SetSourceData` 必须传字符串区间如 `"Sheet1!$A$1:$C$4"` 而非 Range、`ClearContents` 清旧表、`_safe_close`）、`_tables.py`、`_snapshot.py`、`_linting_geometry.py`（纯算术几何/对齐/越界检查）、`tests/conftest.py` 纯 Python 假 COM 思路 | **优先复制/改写模块**（登记 `NOTICE.md`，不整包作为运行时依赖） |
| [ykuwai/ppt-mcp](https://github.com/ykuwai/ppt-mcp) | **MIT**（实查 `LICENSE`：`Copyright (c) 2026 ykuwai`） | `2155ecc9050e` (2026-11-10) | `src/ppt_com/charts.py`（`CHART_TYPE_MAP`、`finally: wb.Close(False)` 防止 Excel 进程泄漏）、`connectors.py`（`CONNECTOR_TYPE_MAP`/`ARROW_STYLE_MAP`/`BeginConnect`/`EndConnect`）、`layout_check.py`（读取与算术分离）、`export.py`、`placeholders.py` | **第二复制/改写来源**（登记 `NOTICE.md`，不引入其 MCP 层） |
| [enthought/comtypes](https://github.com/enthought/comtypes) | MIT（`LICENSE.txt`，GitHub API 报 `NOASSERTION`） | `af0666913fb1` (2026-11-19) | 备选 COM 绑定 | **不采用**：不同时引入两套 COM 绑定 |
| `Ayushmaniar/powerpoint-mcp` | **无许可证** | — | — | **严禁复制代码**（仅作公开思路了解） |
| Office.js (`PowerPointApi`) / VBA | Microsoft 官方接口 | — | Office.js 到 1.10 仍无图表创建/读写；VBA 无包管理与单测 | **不作为本轮渲染器**：VBA 仅作 COM 常量参考；Office.js 留作后续阶段 |

## 2. 本机 Windows + Office 2021 真机最小 COM 实测（2026-11-20）

在用户本机 Windows（Python 3.13.6 + `pywin32==312`）通过独立 `DispatchEx` 实例做只读/临时目录最小实测（不触碰用户任何已打开文稿）：

- **PowerPoint**：`Version=16.0`, `Build=20527`（微软桌面版 PowerPoint，非 WPS），`initial_open_presentations=0`。
- **Excel**：`Version=16.0`, `Build=20527.0`，`DispatchEx("Excel.Application")` 可正常创建并退出。
- **中文与实测文字尺寸**：`TextFrame2.TextRange.Font.Name = "Calibri"` + `Font.NameFarEast = "Microsoft YaHei"` 生效；`BoundWidth=355.3pt`, `BoundHeight=24.0pt` 可直接读取，证实 R3 可用真实排版测量替代纯估算。
- **流程图原生连接线**：`AddConnector(1, ...)` + `ConnectorFormat.BeginConnect(n1, 4)` + `EndConnect(n2, 2)` + `Line.EndArrowheadStyle = 2` 成功，`BeginConnected=-1`、`EndConnected=-1`（`msoTrue`），证实 R2 可彻底解决旧 G 的 B5 缺陷（不画边）。
- **关键实测发现 — `AddChart2` 与 `WithWindow` 的行为差异**：
  1. 当 `Presentations.Add(WithWindow=0)`（无窗口模式 `msoFalse`）时，调用 `slide.Shapes.AddChart2(-1, 51, ...)` 在 Office 2021 Build 20527 上抛出 `pywintypes.com_error: (-2147352567, '发生意外。', (0, None, None, None, 0, -2147467259), None)`（`E_FAIL`）。
  2. 当 `Presentations.Add(WithWindow=-1)`（带窗口 `msoTrue`，同时设置 `app.WindowState = 2` 最小化、不抢前台、结束时仅关闭自己创建的 `Presentation`）时：`AddChart2` → `ChartData.Activate()` → 写入 `Workbook.Worksheets(1)` 单元格 → `chart.SetSourceData("Sheet1!$A$1:$B$3")` → `finally: wb.Close(False)` → `pres.SaveAs()` → 关闭并用只读 `python-pptx` 重新打开，**全部成功**（`reopened slides=1, has_chart=True`）。
  3. **工程决策**：
     - 纯读取/导出/测量（`export`、`measure`）继续使用 `Presentations.Open(path, ReadOnly=-1, Untitled=0, WithWindow=0)`，全程无窗口；
     - 构建含 `add_chart` 的文稿时，若无窗口 `AddChart2` 不可用（Office 2021 已实测证实），对含图表的构建会话自动以 `WithWindow=-1` + 最小化窗口创建该 `Presentation`（仍严格只操作和关闭自己创建的 `Presentation`，绝不触碰用户打开的其它文稿）；若环境中 Excel 缺失导致 `AddChart2` 失败，则显式报错，绝不静默画假图；
     - **PDF 导出实测**：Office 2021 + `pywin32` 延迟绑定调用 2 参数 `ExportAsFixedFormat(path, 2)` 会因可选 `PrintRange` 封送报 `The Python instance can not be converted to a COM object`，改用 `Presentation.SaveAs(pdf_path, 32)`（`ppSaveAsPDF = 32`）实测稳定产出有效 PDF；
     - **COM 生命周期实测**：关闭最后一个带窗口文稿可能触发 `POWERPNT.EXE` 自动退出，故读回 `deck_dump.json` 使用独立只读会话；同时 Windows 同一线程反复 `CoInitialize`/`CoUninitialize` 交替调用 PowerPoint 与 Excel 会破坏 Office 16.0 RPC 代理（`RPC_S_UNKNOWN_IF / -2147023179`），改用线程局部一次性 `CoInitialize()` + 退出时等待自启 PID 结束，彻底消除跨会话断连。

## 3. R1 融合架构与契约设计

1. **编译与执行彻底解耦**：
   - 上游 F 产出后端无关的 `page_design.json`。
   - 新增纯 Python 确定性编译层 `v01/src/pptv01/g_build/compile_ops.py`：`page_design.json + asset_registry.json + user_input_requests.json → deck_ops.json`（可在 Linux / Windows 无 Office 环境下全量单测和黄金文件比对）。
   - 新增契约 `DeckOps(_RunFile)`（`schema_version = "0.1"`，同步导出 `contracts/deck_ops.schema.json` + fixture）。
2. **操作集与防造假硬约束**：
   - 操作类型覆盖：`add_slide`、`set_placeholder_text`、`add_textbox`、`add_shape`、`add_picture`（含 `crop` 模式）、`add_table`、`add_chart`、`add_diagram`（确定性展开为节点 `add_shape` + 连接线 `add_connector`，**节点/边超出槽位时自动多行换行；单页仍装不下则直接抛错 `DiagramOverflowError`，绝不静默截断**）、`add_connector`、`add_placeholder_box`（绑定 `U###`）、`set_notes`、`set_alt_text`、`set_name`。
   - 每个操作携带 `element_id`、`asset_id`/`request_id` 绑定与 `source_refs`；任何视觉操作（`add_picture`/`add_table`/`add_chart`/`add_diagram`）若缺少有效绑定（已 acquired 的 `A###` 或已 provided 的 `U###` 且带出处），编译器和契约校验**直接拒绝**。
3. **`DeckRenderer` 协议与诚实状态**：
   - `v01/src/pptv01/renderers/protocol.py` 定义 `DeckRenderer`（`name`、`preflight()`、`build()`、`export()`、`measure()`）及 `RendererUnavailableError`。包代码里**绝不放 mock 渲染器**。
   - 在无 Windows/PowerPoint 的环境中，G 的编译子步骤（产出 `deck_ops.json`）可完成，但渲染子步骤记为 `not_run` / 未生成 `deck.pptx`，绝不假装 `overall=complete`。
4. **`python-pptx` 去留（遵守规则 9）**：
   - 新渲染路径不调用 `python-pptx` 写文件；只读校验器留作独立验证（R4 迁移到 `[verify]` extra 并明确标注 read-only）；旧 `g_pptx/` 保留至 R2 通过 Smoke 4 后再用单独提交移除。
