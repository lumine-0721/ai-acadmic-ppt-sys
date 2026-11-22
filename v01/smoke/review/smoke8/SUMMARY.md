# Smoke 8 验收包与暂停请求汇总 (`feature/v01-office-renderer`, commit `5bf3353`)

`PYTEST_ALL_EXIT=0` — `634 passed, 1 skipped in 431.32s (0:07:11)`（清环境 Windows 全量测试日志见 [pytest_all.txt](./pytest_all.txt)；唯一跳过项为 `tests\test_stage_h.py:92: LibreOffice (soffice) and pdftoppm not installed in environment`）。离线门禁测试 `pytest -o addopts="" -m "not windows_com and not vision_model" -q` 通过（`626 passed, 1 skipped, 8 deselected in 42.24s`，退出码 `0`）；无 `[vision]` 可选依赖隔离检查（拦截 `rapidocr_onnxruntime` 与 `fastembed` 导入）同样通过（`626 passed, 1 skipped, 8 deselected in 29.01s`，退出码 `0`）。

## 1. 环境检查 (`doctor.txt`)

- 见 [doctor.txt](./doctor.txt)，结果：`overall: READY`（退出码 `0`）。
- 环境：`Windows 11 (10.0.26200)`、`Python 3.13.6`、`pywin32 312`、`Microsoft PowerPoint 16.0 (Build 20625)`（`Office 2021·LTSC` / `HomeStudent2021Retail` `16.0.20625.20108` x64）、`Microsoft Excel 16.0 (Build 20625.0)`、`cjk_fonts` 与 8 套主题字体全部就绪、`python-pptx 1.0.2`；可选的 `soffice`/`pdftoppm` 未安装。

## 2. 八套主题结构骨架图 (`theme_skeletons.png`)

- 见 [theme_skeletons.png](./theme_skeletons.png)（`1980x2524` PNG）：使用 `T12` 完整文稿经过 `run_stage_f` 为 8 套主题生成内容页 `S002`（`hero-statement`）的 `PageDesign` 与 `DeckOps`，逐一经 `PowerPointComRenderer` 真实 COM 生成并导出 PNG 拼接而成；COM 导出图灰度结构聚类数为 **8**（满足灰度可辨识至少 4 种骨架的要求）。
- 人工目视确认无遮挡、无溢出、无截断：
  - `academic-default`：纯白背景（`bg=none`）、左对齐标题（`title=left_rule`）、下划线强调（`accent=under_title`）、浅底描边卡片（`card=outlined`）。
  - `architectural-stone`：对角 `corner_bracket` 直角框、居中标题带短横线（`title=centered_rule`, `accent=none`）、描边卡片（`card=outlined`）。
  - `data-journalism`：全局精细网格线（`bg=fine_grid`）+ 浅底色顶栏（`title=filled_header`）+ 顶部强调框卡片（`card=accent_top`）。
  - `eco-canopy`：左右非对称双分色背景（`bg=split_zone`）+ 标题左侧竖条（`accent=left_bar`）+ 无框浅底填充卡片（`card=filled_soft`）。
  - `medical-clinical`：通栏深色反白顶栏（`title=filled_header`）配亮色竖条（`accent=left_bar`）+ 纯白区描边卡片（`card=outlined`）。
  - `swiss-minimal`：高对比顶部黑条与右角徽章（`bg=top_band`, `accent=corner_badge`）、粗块标题标记（`title=numbered_title`）、完全无框内容块（`card=borderless`）。
  - `tech-indigo`：通高深色左侧边栏（`bg=left_rail`）+ 右移标题配竖条（`accent=left_bar`）+ 深色框卡片（`card=accent_top`）。
  - `warm-espresso`：顶部暖色条与角标（`bg=top_band`, `accent=corner_badge`）、浅咖啡底色顶栏（`title=filled_header`）+ 标题分割线 + 柔和填充卡片（`card=filled_soft`）。

## 3. Smoke 8 四题运行状态（等宝特更新 Stage B / Stage TH 答案）

按任务书流程使用从 `origin/feature/v01-pipeline` 同步的 `smoke/agent-answers/<id>` 答案副本，在全新 `runs/smoke8/<id>` 下以 `PPTV01_LLM_BACKEND=agent --renderer powerpoint-com --on-missing placeholder` 顺序试跑，未伪造或修改任何 `*_response_*.json`，也未在 agent 后端下静默回退关键词主题选择。因 R8-3 新增了 Stage `B` 必填字段（`page_role`、`headline`、`groups`、`speaker_notes`），既有答案在 Stage `B` 校验处停下并输出更新后的请求文件：

| 题号 | 退出码 | 停止阶段 | 原因与现象 | 已提交的请求与日志文件 |
| --- | --- | --- | --- | --- |
| `T36` | `3` | Stage `B` | `A` 通过；既有 `B_response_1.json` 缺少 `page_role` / `headline` / `speaker_notes`，系统写回修正请求 `B_request_2.json` 并按协议退出 `3` 等待答复 | [B_request_1.json](./T36/B_request_1.json), [B_request_2.json](./T36/B_request_2.json), [run.log.tail.txt](./T36/run.log.tail.txt) |
| `T12` | `1` | Stage `B` | `A` 通过；目录内原有两份旧答案（`B_response_1.json` 与 `B_response_2.json`）均缺少 R8-3 新字段，二次重试校验仍缺 21 项必填字段（`LLMOutputError`），已保存新版 Prompt 与校验报错请求 `B_request_1.json` / `B_request_2.json` | [B_request_1.json](./T12/B_request_1.json), [B_request_2.json](./T12/B_request_2.json), [run.log.tail.txt](./T12/run.log.tail.txt) |
| `T26` | `3` | Stage `B` | `A` 通过；既有 `B_response_1.json` 缺少 R8-3 新字段，已写入 `B_request_2.json` 并退出 `3` 等待答复 | [B_request_1.json](./T26/B_request_1.json), [B_request_2.json](./T26/B_request_2.json), [run.log.tail.txt](./T26/run.log.tail.txt) |
| `T02` | `3` | Stage `B` | `A` 通过；既有 `B_response_1.json` 缺少 R8-3 新字段，已写入 `B_request_2.json` 并退出 `3` 等待答复 | [B_request_1.json](./T02/B_request_1.json), [B_request_2.json](./T02/B_request_2.json), [run.log.tail.txt](./T02/run.log.tail.txt) |

> [!NOTE]
> 请宝特补齐四题的新版 Stage `B` 答案（`T36`/`T26`/`T02` 写入 `B_response_2.json` 或替换新版 `B_response_1.json`；`T12` 需替换掉旧的 `B_response_1.json` 与 `B_response_2.json`）。待 `--resume` 过 `B -> C -> D -> E` 后，R8-2 新增的 Stage `TH` 主题选择也会生成 `TH_request_1.json` 暂停一次等待宝特填写 `TH_response_1.json`（`{"theme_id": "...", "rationale": "..."}`），届时再完成 `F -> G -> H` 全链路渲染与 `gates` / `contact_sheet.png` / `before_after.png` 产出。
