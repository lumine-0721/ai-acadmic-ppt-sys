# Smoke 8 完整验收汇总 (`feature/v01-office-renderer`, commit `d551e31`)

`PYTEST_ALL_EXIT=0` — `634 passed, 1 skipped in 431.32s (0:07:11)`（清环境 Windows 全量测试日志见 [pytest_all.txt](./pytest_all.txt)；唯一跳过项为 `tests\test_stage_h.py:92: LibreOffice (soffice) and pdftoppm not installed in environment`）。离线门禁测试 `pytest -o addopts="" -m "not windows_com and not vision_model" -q` 通过（`626 passed, 1 skipped, 8 deselected in 42.24s`，退出码 `0`）；无 `[vision]` 可选依赖隔离检查（子进程拦截 `rapidocr_onnxruntime` 与 `fastembed` 导入）同样通过（`626 passed, 1 skipped, 8 deselected in 29.01s`，退出码 `0`）。

## 1. 环境检查与八套主题骨架图

- **环境体检**（[doctor.txt](./doctor.txt)）：`overall: READY`（退出码 `0`），`Windows 11 (10.0.26200)`、`Python 3.13.6`、`pywin32 312`、`Microsoft PowerPoint 16.0 (Build 20625)`（`Office 2021·LTSC` / `HomeStudent2021Retail` `16.0.20625.20108` x64）、`Microsoft Excel 16.0 (Build 20625.0)`、`cjk_fonts` 与 8 套主题字体全部就绪、`python-pptx 1.0.2`；可选的 `soffice`/`pdftoppm` 未安装。
- **八套主题结构骨架对比图**（[theme_skeletons.png](./theme_skeletons.png)，`1980x2524` PNG）：以完整 `T12` 挑选内容页 `S002`（`hero-statement`）布局，经 `PowerPointComRenderer` 真实 COM 构建并导出 PNG 拼接而成；灰度结构聚类数为 **8**（满足灰度可辨识至少 4 种骨架要求），人工确认无遮挡、无溢出、无截断。

## 2. Smoke 8 运行说明与四题结果

- **运行说明**：已从 `origin/feature/v01-pipeline`（提交 `d8ee2e8`）拉取宝特更新后的 `B_response` 与 `TH_response` 到工作区（未暂存、未提交）。按字面先不带 `--resume` 从零试跑 `T36` 时，Stage B 重做在线检索导致 `[R019]` 编号漂移，校验拒绝 `Hallucination (artificial intelligence)`（`RUN_EXIT=3`）；按 `smoke/README.md` 与 `HANDOFF.md` 约定，清空 `runs\smoke8` 后在全新 `runs\smoke8\<id>` 空目录仅放入冻结 `research_brief.json` 配合 `--resume` 从头重跑（`A -> B -> C -> D -> E -> TH -> F -> G -> H` 全部 8 站 100% 从头执行，无任何旧阶段复用），渲染器 `--renderer powerpoint-com --on-missing placeholder`。其中 `T26` 首次在默认 `180s` 预算下于 `A006`/`A008` 下载超时处耗尽预算（`A008`/`A011`/`A012` 未尝试即标记 missing 导致 Stage E 匹配失败），`T26` 与 `T02` 设置 `PPTV01_ASSETS_BUDGET_S=360`（且 `T02` 在 `A012` 遇到一次代理 `SSL: UNEXPECTED_EOF_WHILE_READING` 后全新目录重跑一次通过），全程未编写或修改任何 `*_response_*.json`，未静默回退关键词主题选择。

| 题号 | 退出码 (`RUN` / `GATES`) | 页数 | `theme_id` | `theme_method` | 主题选择理由原文 (`theme_rationale`) | `fill_rate` 门槛原文 | `no_text_only_visuals` 门槛原文 | 讲者备注核验 (`deck.pptx`) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `T36` | `0` / `0` | 5 | `tech-indigo` | `agent` | 题目是面向普通听众的人工智能介绍，tech-indigo 的技术感靛蓝配色和清爽调性最贴合人工智能这一科技主题；它的中等密度也能配合“尽量少用文字”的极简要求。 | `pass \| all 5 non-exempt slide(s) pass fill_rate (max_void=1.45in < 1.60in)` | `pass \| 0 image visual(s) checked cleanly (max OCR area ratio <= 0.12)` | `5/5` 非空口播讲稿（无 `Speaker/planning summary:` / `Layout notes:`） |
| `T12` | `0` / `0` | 7 | `warm-espresso` | `agent` | 这是一份咖啡店创业计划，warm-espresso 的奶油与浓缩咖啡色调、温暖亲和的气质与咖啡店的产品和空间氛围一致，适合向合作方讲述门店故事。 | `pass \| all 7 non-exempt slide(s) pass fill_rate (max_void=1.03in < 1.60in)` | `pass \| 2 image visual(s) checked cleanly (max OCR area ratio <= 0.12)` | `7/7` 非空口播讲稿（无旧备注前缀） |
| `T26` | `0` / `0` | 8 | `architectural-stone` | `agent` | 题目是世界著名建筑，architectural-stone 的石灰岩与陶土配色、庄重的博物馆气质与古代纪念性建筑到现代建筑的图片最协调，不会抢建筑照片的视觉。 | `pass \| all 8 non-exempt slide(s) pass fill_rate (max_void=1.03in < 1.60in)` | `pass \| 8 image visual(s) checked cleanly (max OCR area ratio <= 0.12)` | `8/8` 非空口播讲稿（无旧备注前缀） |
| `T02` | `0` / `0` | 7 | `eco-canopy` | `agent` | 城市热岛属于城市气候与环境科学话题，缓解思路落在绿地、水体和屋顶绿化上，eco-canopy 的森林绿配色和偏科学的调性与这些内容最契合。 | `pass \| all 7 non-exempt slide(s) pass fill_rate (max_void=1.03in < 1.60in)` | `pass \| 3 image visual(s) checked cleanly (max OCR area ratio <= 0.12)` | `7/7` 非空口播讲稿（无旧备注前缀） |

- **主题与斜错位检查**：4 题共覆盖 4 套不同主题（`tech-indigo`、`warm-espresso`、`architectural-stone`、`eco-canopy`），`theme_method` 均为 `agent`；所有 `page_design.json` 的 `notes` 中 `staggered` 出现次数均为 `0`。
- **讲者备注实测原文**：见 [speaker_notes_check.txt](./speaker_notes_check.txt)（使用 `python-pptx` 只读打开全部 4 个 `deck.pptx`，共 `27/27` 页含非空口播讲稿，全部已移除 `Speaker/planning summary:` 与 `Layout notes:`）。
- **视觉检查与 Smoke 7 → Smoke 8 对比结论**（人工查看全部 4 组 [T36 contact_sheet](./T36/contact_sheet.png) / [before_after](./T36/before_after.png)、[T12 contact_sheet](./T12/contact_sheet.png) / [before_after](./T12/before_after.png)、[T26 contact_sheet](./T26/contact_sheet.png) / [before_after](./T26/before_after.png)、[T02 contact_sheet](./T02/contact_sheet.png) / [before_after](./T02/before_after.png)）：
  - `T36`、`T12`、`T26`、`T02` 在 Smoke 7 中的斜向交错卡片（如 `T36 S003/S004/S005`、`T12 S002/S005/S006`、`T26 S008`、`T02 S002/S003/S005`）在 Smoke 8 中已全部消除并收敛为规整的 `single-column` / `hero-statement`；
  - `T02 S006`（`comparison`）中此前孤立悬浮在文字上方的绿色色条，现稳妥紧贴两张实景对比图上方；
  - `T36 S004`、`T02 S002/S003/S004` 此前悬停在中部的文献引文，现统一锚定在内容区底端、占位框上方；
  - 四套主题在内容页均体现出可分辨的骨架特征（左侧竖栏 `tech-indigo`、浅咖啡顶栏+角标+柔填卡片 `warm-espresso`、对角直角框+居中短横线标题 `architectural-stone`、双分色底 `eco-canopy`），全页中文与图片无重叠或截断。

## 3. 各题产物索引

- `T36`: [deck.pptx](./T36/deck.pptx) | [contact_sheet.png](./T36/contact_sheet.png) | [before_after.png](./T36/before_after.png) | [gates.log](./T36/gates.log) | [page_design.json](./T36/page_design.json) | [gate_report.json](./T36/gate_report.json) | [qa_report.json](./T36/qa_report.json) | [asset_registry.json](./T36/asset_registry.json) | [run.log.tail.txt](./T36/run.log.tail.txt) | `render/` (5 PNG + `deck.pdf`)
- `T12`: [deck.pptx](./T12/deck.pptx) | [contact_sheet.png](./T12/contact_sheet.png) | [before_after.png](./T12/before_after.png) | [gates.log](./T12/gates.log) | [page_design.json](./T12/page_design.json) | [gate_report.json](./T12/gate_report.json) | [qa_report.json](./T12/qa_report.json) | [asset_registry.json](./T12/asset_registry.json) | [run.log.tail.txt](./T12/run.log.tail.txt) | `render/` (7 PNG + `deck.pdf`)
- `T26`: [deck.pptx](./T26/deck.pptx) | [contact_sheet.png](./T26/contact_sheet.png) | [before_after.png](./T26/before_after.png) | [gates.log](./T26/gates.log) | [page_design.json](./T26/page_design.json) | [gate_report.json](./T26/gate_report.json) | [qa_report.json](./T26/qa_report.json) | [asset_registry.json](./T26/asset_registry.json) | [run.log.tail.txt](./T26/run.log.tail.txt) | `render/` (8 PNG + `deck.pdf`)
- `T02`: [deck.pptx](./T02/deck.pptx) | [contact_sheet.png](./T02/contact_sheet.png) | [before_after.png](./T02/before_after.png) | [gates.log](./T02/gates.log) | [page_design.json](./T02/page_design.json) | [gate_report.json](./T02/gate_report.json) | [qa_report.json](./T02/qa_report.json) | [asset_registry.json](./T02/asset_registry.json) | [run.log.tail.txt](./T02/run.log.tail.txt) | `render/` (7 PNG + `deck.pdf`)
