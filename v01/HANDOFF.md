# HANDOFF.md — 给编码 Agent（Antigravity / Gemini）的任务书（第 3 版：2026-11-20）

> 读者：接手写代码的 Agent。本文自包含：读完即可开工，不需要别的上下文。
> 仓库：<https://github.com/lumine-0721/ai-acadmic-ppt-sys>，本项目目录 `v01/`。
> **代码基线（重要，第 3 版）**：你上一轮的成果在分支 `feature/v01-office-renderer`（头提交 `7ceb945`，已评审，见第 4.1 节）。**下一轮继续在这个分支上做（或从它拉 `feature/v01-office-renderer-2`）**，做第 5 节任务（**先做 N2，再做 N0**，然后 N1 / N3–N8）；Windows 实机测试清单在第 5B 节，由用户在真机上跑——**N2 合入之前不要重跑 5B**（上一轮冒烟已因 B8 挂死）。本文件所在的 `feature/v01-pipeline` 只放任务书/调研/冒烟答案，**不要往上面推代码**，也不要把你的分支合进去（合并由用户决定）。
> 分工：**你负责实现**；宝特（Grok Bot，代表用户 qing he）**定方向、出基准试卷和评分、做评审**。你不改基准试卷，也不自己给自己评分定稿。
> 配套文档：`v01/docs/renderer-office-research.md`（Office 自动化路线调研 + 融合决定，**先读**）。
> **环境已确认（用户实测，2026-11-20）：Microsoft 365 桌面版 PowerPoint，Version 16.0，Build 20257.20196（Current Channel），不是 Office 2021。策略不变：只用 Office 2021 / Office 16 基线也有的 API，保证到处能跑。**
> **第 3 版相对第 2 版的变化**：第 4.1 节是对 R0–R7（`7ceb945`）的评审结果和新 bug 清单（B8–B17，带文件:行）；第 5 节是新任务 N0–N8；第 5B 节是 Windows 真机测试清单；原 R0–R7 任务书移到 5A 归档。**最新（2026-11-21）：先看第 5 节开头「第 3 轮复核」，修完那两条再跑 5B。** **最要紧的事先做 N2：** 上一轮真实 Windows 冒烟因 B8（`GetActiveObject` 附着到用户已打开的 PowerPoint，且无超时）挂死，必须先隔离实例并加上看门狗，然后才是 N0（全量 `pytest` 在没装 Office 的机器上是红的）。python-pptx 仍在默认生成路径里（N7，须等 5B 通过）。环境是 Microsoft 365 而不是 2021（已确认，B12/N1 只剩文档与 doctor 的整改）。

---
## 1. 项目目标与现状

**目标。** AI PPT Sys V0.1：用户一句话 → 一份**可检查、可编辑的原生 PPTX**。中间是一条线性链 A→H，每一站把结果以 JSON 契约落盘到 `runs/<run_id>/`，任何一站都能单独打开检查，没有多 Agent 编排、没有 Deck IR、没有旧的 handoff 文件。

| 阶段 | 作用 | 状态（`4550cc1`） |
| --- | --- | --- |
| A | 一句话 → `production_plan.json`（含 retrieval_brief） | ✅ |
| B | 检索 + 相关性过滤 + 暂定页面规划 + 结构化 `asset_needs` | ✅ |
| C | `asset_requirements.json` | ✅ |
| D | `asset_registry.json` + `assets/` + **跨需求去重 `shared_with`** + 确定性生成 `user_input_requests.json` | ✅（见第 4 节 B3、B7） |
| E | `slide_asset_mapping.json`（只映射真实素材；VLM 相关度可选，缺失时 `not_assessed`） | ✅ |
| F | `page_design.json`（英寸坐标、容量估算、占位）+ Pillow 示意预览 | ✅（见 B2、B6） |
| G | python-pptx 生成 `deck.pptx` | ⚠️ **将被 Office 渲染器取代**（第 5 节），见 B4、B5 |
| H | LibreOffice 渲染 + 6 类 QA + 有限修复 | ⚠️ **将改为以 PowerPoint 本体渲染/测量**（第 5 节），见 B6 |
| 评分 | PPTEval + 独立确定性硬门槛 | ✅ 框架；硬门槛有被骗过的缺口（B4） |

`run_report.json.overall`：`complete` 仅当 A–H 都 `done` 且无 open/placeholder；有占位是 `complete_with_placeholders`；`--on-missing wait` 暂停是 `needs_input`（退出码 4）。

**关键提交（分支 `feature/v01-h-qa`，你上一轮）：** `30f1b7f` D 去重 · `d65422b` 用户补充请求机制 · `1b5ae90` E · `6e8eb26` F · `e7e8532` G · `4550cc1` H（及每项对应的调研一页）。

### 安装、测试、跑 CLI
```bash
git clone https://github.com/lumine-0721/ai-acadmic-ppt-sys.git && cd ai-acadmic-ppt-sys
git checkout -b feature/v01-office-renderer origin/feature/v01-h-qa && cd v01
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,rank]"          # rank = fastembed 相关性打分（可选）
# 干净环境跑全量测试（必须先清掉所有 PPTV01_*；一定要看 pytest 自己的退出码，不要接 | tail）
for v in $(env | grep -o '^PPTV01_[A-Z_]*'); do unset $v; done
pytest ; echo "PYTEST_EXIT=$?"            # 必须 0
pptv01 run "帮我介绍一下城市热岛效应。" --out runs/ --run-id T02
pptv01 gates runs/T02
python -m pptv01.contracts       # 改了契约后重新导出 contracts/*.schema.json
```
**Windows（PowerShell）等价写法**（本轮 COM 部分只能在 Windows 上验收，见第 5 节「Windows 上怎么测」）：
```powershell
py -3.11 -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -e ".[dev,rank,windows]"          # windows extra = pywin32（本轮新增）
Get-ChildItem Env:PPTV01_* | Remove-Item       # 清环境
pytest; "PYTEST_EXIT=$LASTEXITCODE"            # 必须 0（非 Windows/无 PowerPoint 的用例应 skip 并写明原因）
pytest -m windows_com; "PYTEST_EXIT=$LASTEXITCODE"
```
Python ≥ 3.11。完整说明见 `v01/README.md`、`v01/docs/V0.1-技术方案.md`、`v01/docs/V0.1-基准测试规范.md`。

### 环境变量（密钥只能来自环境变量，不得进代码/日志/提交）
| 变量 | 说明 |
| --- | --- |
| `PPTV01_LLM_BASE_URL` / `_API_KEY` / `_MODEL` | OpenAI 兼容接口；三者缺一 `pptv01 run` 退出码 2 |
| `PPTV01_LLM_BACKEND=agent` + `PPTV01_AGENT_RESPONSES_DIR` | 没有 API 时由"人/Agent"充当模型：写 `<stage>_response_<n>.json`，`--resume` 继续；`run_report` 记录 `agent:宝特 (manual, not an API model)`。**这是诚实标注的真人作答，不是 mock，结果必须按此标注** |
| `PPTV01_RESEARCH` | `auto`（默认 wikipedia+crossref）/`none`/`wikipedia,crossref,searxng`；`PPTV01_RESEARCH_SEARXNG_URL`、`_CONTACT`、`_MAX_HITS/_MAX_QUERIES/_TIMEOUT_S` |
| `PPTV01_RELEVANCE` (`auto`/`structural`/`off`)、`_SCORER`、`_THRESHOLD` | B/D 的相关性过滤；默认 fastembed@0.30 |
| `PPTV01_ASSETS` (`auto`/`none`)、`PPTV01_OPENALEX_API_KEY/_EMAIL`、`PPTV01_COMMONS_THUMB_WIDTH`、`PPTV01_ASSETS_TIMEOUT_S` | D 阶段 |
| `PPTV01_GRADER_BACKEND` (`api`/`agent`) + `_BASE_URL/_API_KEY/_MODEL/_VISION_MODEL` 或 `_EXCHANGE_DIR` | PPTEval 评分后端；未配置则报错，绝不编分 |

### 代码与契约位置
```
v01/src/pptv01/   contracts.py（全部 Pydantic 契约）runner.py cli.py llm.py textcheck.py numerals.py
                  a_plan/ b_slide_plan/ c_asset_requirements/ d_retrieve/ retrieval/（含 relevance.py）
                  e_mapping/ f_layout/ g_pptx/ h_qa/   ← E–H 已有实现；本轮 G/H 的渲染部分要换成 Office 渲染器（第 5 节）
                  grading/（ppteval.py、gates.py、ppteval_prompts/）
v01/contracts/*.schema.json   从 Pydantic 导出的 JSON Schema（11 个；契约一改必须重新导出，否则测试失败）
v01/fixtures/                 手写的合法契约样例（仅供单测）
v01/tests/                    单测；fake/replay 只允许存在于 tests/ 下（tests/replay/*.json 是真实录制的 API 响应）
v01/benchmark/v1.0/           冻结的 40 题试卷与分层（只读）
v01/docs/                     技术方案、基准规范、grading.md、stage-d-research.md、relevance-benchmark.md、smoke-v3-results.md
```
`contracts.py` 里已经有 `SlideAssetMapping`（E）、`PageDesign`（F）、`QAReport`（H）的草案模型，你可以改，但要同步 schema、fixture、测试，并在汇报（第 6 节；不开 PR）里写清为什么改。

---

## 2. 硬规则（违反任何一条，宝特会直接打回）

1. **分支**：本轮从 `feature/v01-h-qa` 拉 `feature/v01-office-renderer`（可再细分子分支），**只推你自己的分支**。**绝不推 `master`/`main`，绝不 force push，不开 PR，不改仓库设置，不改全局 git 配置。**
2. **测试**：每次推送前，在**清环境**（unset 全部 `PPTV01_*`）下跑全量 `pytest`，**看 pytest 自己的退出码是 0**（不是 `| tail` 的退出码）。新增行为必须带测试；契约改动必须同步 `contracts/*.schema.json`、fixtures。
3. **不造假**：不得把假数据当成真实模型输出或真实外部结果。fake client / HTTP replay 只能在 `tests/` 下；包代码里**不许有 mock 回退**（缺配置就明确报错）。用 agent 后端跑出的结果必须标注 `agent:宝特 (manual, not an API model)`。
4. **契约**：所有落盘 JSON 都是 Pydantic v2 模型，`schema_version = "0.1"`，带 `run_id`，ID 为 `S###`/`A###`，路径一律相对路径。不要悄悄放宽已有校验；要放宽先说明理由。
5. **先搜后写（GitHub-search-first）**：每实现一个阶段/组件之前，先搜成熟方案并优先"融合"而不是重造；在 `v01/docs/` 下为该阶段写**一页**《现成方案调研 + 融合决定》（候选、许可证、取舍、为什么融/不融；可参考 `docs/stage-d-research.md` 的格式），**先于实现提交**。引入第三方代码/提示词要保留许可证与署名（参考 `v01/NOTICE.md`）。
6. **冻结区**：不要改仓库根目录的旧代码（`apc/`、`seaa/`、`aprd/`、`orchestrator/` 等），也**不要改 `v01/benchmark/v1.0/` 的任务与分层**。新代码只放 `v01/`（可以**只读地复制/改写**旧代码里的片段，如 `aprd` 的 COM 导出脚本，并登记 `NOTICE.md`；不要修改旧目录）。
7. **运行产物**：`v01/runs/` 已被 gitignore，不要提交运行产物、密钥、下载的素材；`orchestrator/.tmp-tests/` 不是你的，别碰。
8. **诚实状态**：做不到的就记成 `missing` / `not_run` / `needs_user_input` 并写原因；不要让流程显示"成功"。
9. **本轮新增：渲染层不得用 python-pptx 写 PPTX。** 用户决定：生成/渲染走 Office 自动化（PowerPoint COM 为主）。python-pptx 最多作为**只读校验器**留在 `[verify]` extra 里（硬门槛/测试里"独立于生成器"地读 `.pptx`），代码里明确标注 read-only；旧 G 的删除要在 COM 渲染器通过 Smoke 4 之后、用**单独一个提交**完成（方便回退）。
10. **本轮新增：好改的就大改，复用优先（copy reuse first）。** 现有代码里不好用的部分（例如 F 的整块圆角大卡片样式、G 的文本框式布局）不必为"保留"而保留；每个新组件先搜成熟开源实现并**复制/改写**（保留许可证与署名，登记 `NOTICE.md`），自研要写明理由。调研已为你做了第一轮：`docs/renderer-office-research.md` 第 2、6 节列出了可复制的来源（pptlive MIT、ykuwai/ppt-mcp MIT、自家 `aprd` COM 导出脚本）；**无许可证的仓库（如 Ayushmaniar/powerpoint-mcp）只许看思路，不许复制代码。**
11. **本轮新增：COM 代码不得动用户已打开的演示文稿。** 只操作自己创建/打开的 `Presentation`，只关自己的；`DisplayAlerts=False`、`AutomationSecurity` 设为禁用宏；所有 COM 调用有超时；失败要留下明确错误，不要吞异常后说成功。包代码里**不许有"没装 PowerPoint 就假装渲染成功"的回退**——缺环境就明确报错（`RendererUnavailableError`）。

## 3. 新产品规则（用户定的，优先级高于一切"看起来完整"）

> **"一键 PPT 不是一键胡咧咧 PPT。"**

图表、数据图、统计数字、表格、图标，**如果找不到真实、可标来源的素材，就不许编造**。流水线必须把它们**显式暴露**成一份"需要用户补充"的清单，并二选一：

- **等待**用户提供材料/数据（`--on-missing wait`，退出码 4），或
- 用**明确标注的占位**渲染该页元素（`--on-missing placeholder`，非交互/基准运行的默认）。

**图表/示意图只允许由两类输入生成：**
(1) 用户提供的数据或结构；(2) 有来源的数据或结构——每个节点、边、数值都能追溯到 `source_refs`（研究笔记 `R###`、登记的素材 `A###`）或用户材料 `U###`。模型凭记忆写出来的数字、因果箭头、"典型流程"一律不算来源，不得画进图里。

### 契约新增：`user_input_requests.json`

新增 Pydantic 模型 `UserInputRequests(_RunFile)`（`contracts.py` + 导出 schema + fixture + 测试），**由代码（不是 LLM）在 D 结束时根据 `asset_registry` 里的 `missing` 与 C 的需求类型确定性生成**，后续阶段读取：

```jsonc
{
  "schema_version": "0.1", "run_id": "...",
  "requests": [{
    "request_id": "U001",                     // ^U\d{3}$
    "kind": "diagram|chart|statistic|table|icon|image|other",
    "asset_id": "A003",                       // 对应 asset_registry 里 missing 的素材，可空
    "page_ids": ["S002"],
    "need": "并列画出气象站测气温与卫星测地表温度两种方式及其差异",   // 要什么
    "why": "D 未找到可标来源的现成图；不允许凭空绘制",               // 为什么要用户给
    "format_wanted": "二选一：①结构化 JSON {nodes:[{id,label,source}],edges:[{from,to,label,source}]}；②PNG/SVG ≥1280px，注明来源与许可",
    "acceptance": ["每个节点/边有来源", "许可明确"],                // 沿用 C 的 acceptance
    "blocking": true,                         // 该页没它是否无法成立
    "fallback": "placeholder|omit_element|wait",
    "status": "open|provided|declined|placeholder_rendered",
    "provided": { "file": "inputs/U001.json", "sha256": "...", "source": "...", "license": "...", "note": "" }  // status=provided 时必填
  }],
  "summary": "5 open (3 blocking)"
}
```
行为要求：
- 新增 CLI：`pptv01 requests <run_dir>`（列清单）和 `pptv01 provide <run_dir> U001 --file path [--source ... --license ...]`（校验类型/哈希/许可后落到 `runs/<id>/inputs/`，更新 status，**不覆盖**原始 missing 记录）。`pptv01 run ... --resume` 后 E–H 使用用户材料。
- `run_report.json` 新增：`user_input_requests`（open 数量与路径）和 `overall` 新取值：`needs_input`（等待中）、`complete_with_placeholders`（渲染完成但存在占位）。**不得**在存在 open 请求时显示 `complete`。
- **占位的样子**：PPTX 里是带虚线框的形状，文字固定为 `待补充：<need>（U001）`，元素名前缀 `PLACEHOLDER_U001`，同时写入页面备注。禁止用任意配图、AI 生成图或随便一张"差不多"的图顶替。
- **硬门槛新增**（`grading/gates.py`，与 PPTEval 分开）：`no_fabricated_visuals`（deck 里每个图/表/图标元素必须绑定 `asset_id` 或 `U###` provided，否则失败）；`placeholders_marked`（所有 missing/open 项在 PPTX 中都是带标记的占位）。
- 图表数据必须带出处字段（数据集 URL/DOI 或用户文件），PPTX 原生图表里的数据能在 `inputs/` 或 `assets/` 找到对应来源。

---

## 4. 评审结果

### 4.1 第 2 轮评审（对象：`feature/v01-office-renderer`，头提交 `7ceb945`，2026-11-20，宝特）

**评审对象：** 分支 `origin/feature/v01-office-renderer`，在 `4550cc1` 之后共 9 个提交（作者 Antigravity (Gemini)）：

| 提交 | 内容 | 对应任务 |
| --- | --- | --- |
| `b47fc8e` | 修 B1/B2/B3 | R0 |
| `fa0c5ff` `338ef22` | Stage G/H 调研页；DeckOps 契约、`DeckRenderer` 协议、`g_build/compile_ops.py` | R1 |
| `ad20309` | PowerPoint COM 渲染器（`renderers/powerpoint_com/*`，含 fake COM 与 Windows 实机测试） | R2 |
| `94e9016` | COM 导出 PNG/PDF、`BoundHeight` 实测、几何 lint、PageDesign 修复循环 | R3 |
| `16b6b56` | 硬门槛加固（sha256 校验、形状白名单、COM 校验） | R4 |
| `c3dcaa9` | F 容量校准、去空卡片、统一 `crop_fill` 槽位、B6 QA 规则 | R5 |
| `3148956` | D 图片相关度核验（艺术插画过滤、同名异地过滤、可选 VLM） | R6 |
| `7ceb945` | `pptv01 doctor`、`--renderer`、`docs/windows-testing.md` | R7 |

**测试（评审机 Linux，清环境 `PPTV01_*` 全 unset，`pytest -o addopts="" -rs`，看真实退出码）：**
**2 failed，471 passed，4 skipped，pytest 退出码 1（不是 0）。** 失败的两条都是**漏了跳过保护**的 Windows 实机测试：`tests/test_r5_layout_quality.py:193`（`test_r5_windows_com_redesigned_deck_clean_qa_and_gates`）、`tests/test_r7_doctor.py:77`（`test_r7_windows_real_doctor_all_required_green`）。它们带了 `@pytest.mark.windows_com` 标记，但没有"非 Windows / 没装 PowerPoint 就 skip"的守卫（别的 windows_com 测试有）。Antigravity 在 Windows 上只跑了 `-m "not windows_com"` 与 `-m windows_com`，所以没发现"全量 `pytest` 在没有 Office 的机器上是红的"。这违反第 2 节规则 2。**Windows 侧的真实 Office 结果我这边无法复核，必须看第 5B 节的回传。**

**Smoke 4 编译层（Linux，`--renderer compile-only`，agent 后端；A–C/E 手写答案标注 `agent:宝特 (manual, not an API model)`）：** T02/T12/T26/T36 都能从 `page_design.json` 编出 `deck_ops.json`（T12 为 72 条操作），每个图都带 provenance 绑定；`--on-missing placeholder` 下占位框用虚线并标 `PLACEHOLDER_U###`。B1–B3、B4 的多数路径已修好（见下）。真实 PowerPoint 渲染图我没有，**R5 的"版式是否真的好看"没有任何可信证据，等 5B 回传。**

#### 逐项验收（R0–R7）

| 任务 | 结论 | 说明 |
| --- | --- | --- |
| **R0** 还账 B1/B2/B3 | ✅ 通过（含新增缺陷 B13） | B1：`provide` 之后 `--resume` 会作废并重算 E–H，流程图真的进了 deck（复现通过，`runner.py` 里 `_can_reuse` + `input_hashes`）；B2：幻灯片不再带 `content_summary`；B3：请求文案不再写"自绘"，且 C 校验会拒绝"否则自绘并说明"。副作用：旧的 C 答案要重写（我已改）。缺陷见 B13（否定句误杀）。 |
| **R1** 契约/协议/编译层 | ✅ 通过 | `DeckOps` 契约 + schema + fixture；`DeckRenderer` 协议；`compile_ops` 纯 Python、确定性（两次编译字节一致）；流程图节点/边全部编出（B5 在编译层已解决，行数多时换行不截断）。待办：`h_qa/stage.py:90` 用 `co_varnames` 探测参数（见 B11）。 |
| **R2** COM 执行器 | ⚠️ 静态看合理，**只有 fake COM 验证过**，会话安全有 bug | fake COM 测试 471 里通过；真实 COM 行为我无法验证。好处：线程内只 `CoInitialize` 一次、Excel 图表工作簿 `finally` 关闭、`AddChart2` 先开最小化窗口、`SetSourceData` 用字符串区域。缺陷：**B8**（连到用户已运行的 PowerPoint，且可能 `Quit` 它；没有 COM 调用超时）。 |
| **R3** H 用 PowerPoint 渲染+实测 | ⚠️ 部分通过 | 导出 PNG、PDF（`SaveAs(…,32)`）、`TextFrame2.BoundHeight` 实测、几何 lint、改 `page_design` 后重编译重建重导出——结构齐。缺陷：**B10**（修复循环里重新编译漏掉 `template_path`，改过一轮后模板会丢）、**B11**。真实修复效果未验证。 |
| **R4** 硬门槛加固 | ✅ 基本通过，有漏洞 B9 | sha256 对 blob 校验、改名图片/手绘椭圆/连接线/换 blob 的 B4 复现全被门槛抓住。**但白名单正则 `_ALLOWED_STRUCTURAL_RE` 太宽**：名为 `card_*`/`accent_*` 的手绘形状可以通过（B9，我已复现）。 |
| **R5** 版式质量 | ⚠️ 代码层改了，**效果未证实** | 去掉空卡片、统一裁切槽位、B6 QA 规则（空卡片/图过小）有测试；T02 新的 F 设计图比之前大。真实视觉必须在 PowerPoint 渲染图上由人眼+PPTEval 验证。`docs/stage-f-layout-calibration.md` 里的容量校准写的是"Office 2021"，实际是在 Microsoft 365 Build 20257.20196 上测的，措辞要改（见 B12、N1）。 |
| **R6** D 图片相关度 | ⚠️ 部分通过，有过拟合和"错图被选中" | 艺术插画过滤、美国辖区过滤、不可评估时诚实记 `not_assessed`+`needs_review`。缺陷：**B14**（同名异地规则 `_HOMONYM_CONFLICTS` 只硬编码了 Pekin 和 Nashville/Parthenon 两个冒烟例子，换一个例子就不灵，属于对基准的拟合）、**B15**（T02 里北京的图被选成纽约 ISS 卫星照，相似度刚过 0.30 阈值，没有"候选与目标实体不符"的通用判据）。 |
| **R7** doctor + Windows 协议 | ✅ 通过（待实机输出） | `pptv01 doctor [--json]` 区分 required/optional，缺项退出码 1；`docs/windows-testing.md` 步骤齐全。缺陷：**B12**（doctor 不判断 Office 版本类型；文档写"Office 2021 / Office 365"含糊）。 |

#### 其他检查

- **python-pptx 是否已退出生成路径：❌ 没有。** `pyproject.toml:13` 仍是核心依赖；`--renderer` 默认是 `legacy`（`cli.py:25`），旧 `g_pptx` 仍是默认生成路径；`h_qa/repair.py:9-11` 在非 COM 路径仍用 python-pptx **写** PPTX；`grading/gates.py:14` 在模块顶部 `from pptx import …`（只读，OK，但若把 python-pptx 挪到 `[verify]` 就必须改成惰性导入+明确报错）。COM 路径本身没有 python-pptx 写入。
- **Microsoft 365 专属 API：** 未发现 Designer/Copilot/动态数组/新版图表专属调用；`AddChart2`（Office 2013+）、`TextFrame2`、`Export`、`SaveAs(…,32)` 在 Office 2021 / Office 16 基线里都有。**策略不变：只用 Office 2021 基线也有的 API**；目标机器已确认是 Microsoft 365（见 B12），所以"在 2021 上也能跑"只是**设计约束，没有在真 2021 上验证过**。
- **假图：** 未发现。图表要么来自用户 `provided` 的数据，要么是 `missing` → 占位框；没有自绘图表代码。
- **复用/署名：** `NOTICE.md` 登记了 pptlive（MIT）、ykuwai/ppt-mcp（MIT）、aprd 导出脚本、ppt-master `slot_capacity`；无许可证的 Ayushmaniar/powerpoint-mcp 未复制。✅
- **B1–B7 复现结论：** B1 ✅ 已修；B2 ✅；B3 ✅；B4 ✅（改名、手绘椭圆、连接线、换 blob 都被抓；但见 B9 新漏洞）；B5 ✅（编译层）；B6 ⚠️（有规则，视觉未验证）；B7 ⚠️（Pekin 被拒，但换成了纽约的图，见 B15）。

#### Bug 清单（文件:行，均在 `7ceb945` 复现或静态确认）

- **B8（高）`renderers/powerpoint_com/session.py:64-68`（`_connect_powerpoint`）与 `:264`（`__exit__` 里的 `app.Quit()`）**：文档字符串和模块头都写"`DispatchEx` 独立实例"，代码却先 `GetActiveObject` 连上**用户正在用的 PowerPoint**，只有没运行时才 `Dispatch`。另外 `Quit` 的条件是"我们开始时没有已打开的演示文稿"，用户开着一个空的 PowerPoint 也会被我们关掉。违反规则 11（不动用户已打开的内容）。且整个会话**没有 COM 调用超时/看门狗**，PowerPoint 弹模态框时会永远挂住。修法：始终 `DispatchEx` 新实例并记下 PID，只 `Quit`/终止自己启动的 PID；加看门狗（超时则终止自己启动的 PID，报明确错误）；加 fake COM 测试覆盖"用户已开着 PowerPoint"。
- **B9（高）`grading/gates.py:49-52`（`_ALLOWED_STRUCTURAL_RE`）与 `:152`（`.match(name)`）**：白名单是前缀正则，`card_*`、`accent_*` 等前缀下的任何手绘形状（例如名为 `card_fake_chart` 的椭圆+折线）都会通过 `no_fabricated_visuals`。修法：白名单改成**精确集合**——只允许 `deck_ops.json`/`page_design.json` 里声明过的结构元素名；并按形状类型约束（结构元素只能是矩形/文本框/直线，不能是椭圆、自由曲线、带点列表的形状；连接线只允许 `DIAGRAM_*` 且两端必须绑在图节点上）；加 `card_fake_*` 手绘图的回归测试。
- **B10（中）`h_qa/stage.py:138`**：修复循环里 `compile_deck_ops(slide_plan, current_design, reg, uir, run_dir)` 没传 `template_path`（对照 `runner.py:485` 有传），修复一轮后重建的 deck 会丢模板。修法：传同一个 `tpl_rel`；加测试（带模板、触发一次修复后，deck 的母版/版式仍来自模板）。
- **B11（中）`h_qa/stage.py:90`**：`"page_ids" in deck_renderer.measure.__code__.co_varnames` 这种内省 hack。协议（`renderers/protocol.py`）应该统一成 `measure(pptx, page_ids=None)`，所有实现与 fake 都要实现。
- **B12（已解决：用户已确认，不再是开放问题）机器的 Office 版本**：用户实测 PowerPoint 是 **Microsoft 365 桌面版 PowerPoint（Version 16.0，Build 20257.20196，Current Channel）**，**不是 Office 2021**。（此前汇报里的 "Build 20527" 是抄错的数字，以 20257.20196 为准。）**决定：策略不变——只用 Office 2021 / Office 16 基线里也存在的 API，保证换到 2021 或 LTSC 机器也能跑；不使用 Microsoft 365 专属功能（Designer、Copilot、云端图标/字体库、动态数组图表、Office.js 1.8+ 等）。** 剩余的整改（N1）：（a）`docs/stage-f-layout-calibration.md`、`windows-testing.md`、测试 docstring 里写死的"Office 2021 实测"改成"在 Microsoft 365 Build 20257.20196 上实测；仅使用 Office 2021 基线 API，2021 上未实测"；（b）`doctor` 读注册表 `HKLM\SOFTWARE\Microsoft\Office\ClickToRun\Configuration` 的 `ProductReleaseIds`/`VersionToReport`/`UpdateChannel`，如实输出"产品类型：Microsoft 365 / Office 2021·LTSC / 其他"，**不得因为不是 2021 就报错**。
- **B13（低）`c_asset_requirements/validate.py:26,101`（`SELF_DRAW_RE`）**：对"不允许凭空绘制"这类**否定**说法也报错（误杀）。修法：先剥掉否定前缀（"不/不得/禁止/不允许/严禁 + 自绘/凭空绘制"）再匹配，加正反测试。
- **B14（中）`d_retrieve/image_rules.py:76-90`（`_HOMONYM_CONFLICTS`）**：只硬编码 `pekin`、`nashville|tennessee|arkansas|centennial park` 两条，且 `_US_JURISDICTION_RE` 里也混了 `tazewell county|nashville|centennial park`——这是照着冒烟样本拟合的。盲测集换题就不灵。修法：改成**通用**判据——候选的标题/描述/分类里出现的地名限定词，与需求目标实体所在地（用 Wikidata 的 `P17/P131/instance of` 或需求里的地点文字）不一致就判冲突；删除按题硬编码的条目（或只留在测试夹具里）。
- **B15（中）`d_retrieve/image_rules.py`（选择阶段）**：目标是"北京"的图，最后选中了"ISS-39 New York City.jpg"。原因：被拒掉一张后，退而求其次选了相似度刚过 0.30 的另一张，**没有检查候选是否真的在讲目标实体**。修法：若需求有明确地点/实体目标，候选的元数据（标题/描述/分类/Wikidata 标签）必须命中目标（含别名/译名）；不命中就算 `missing` 并生成用户补充请求，而不是选一张"相关"的图。
- **B16（低）`pyproject.toml:13` / `cli.py:25` / `h_qa/repair.py:9-11,47,164`**：见上"python-pptx 是否已退出生成路径"。
- **B17（低）测试：** `tests/test_r5_layout_quality.py:193`、`tests/test_r7_doctor.py:77` 缺跳过保护（见上）。修法：在 `tests/conftest.py` 里统一给 `windows_com` 标记加自动跳过（非 Windows、`pywin32` 缺失或 `PowerPoint.Application` 起不来时 skip 并写明原因），再逐个删掉测试里零散的守卫；并在汇报里同时给 `pytest`（全量）与 `pytest -m windows_com` 两个退出码。

#### 评审通过的做法（保留）

编译层纯 Python 且确定性；DeckOps 带 provenance 绑定；`input_hashes` 级联失效；`doctor` 的 required/optional 区分；fake COM 的存在让执行器逻辑能在 Linux 上测；`windows-testing.md` 里"不要把 pytest 接管道"的提醒；R6 在无法评估时诚实写 `not_assessed`。

### 4.2 第 1 轮评审（对象：`feature/v01-h-qa` `4550cc1`，已归档；B1–B7 已在 4.1 复核）

**评审对象：** 分支 `feature/v01-h-qa`，提交 `30f1b7f … 4550cc1`（11 个提交，作者 Antigravity (Gemini)，均晚于 `dc9cd22`）。

**测试：** 清环境（`PPTV01_*` 全部 unset）全量 `pytest`：**442 passed，0 skipped，pytest 自身退出码 0**。soffice/pdftoppm 在评审机上可用，所以 H 的渲染测试是真跑的。

**Smoke 4（T02/T12/T26/T36，agent 后端；A–C 沿用旧的手写答案，E 由宝特手写并标注 `agent:宝特 (manual, not an API model)`）：** 四题都 A→H 全 `done`，`overall=complete_with_placeholders`，`pptv01 gates` 全部 pass，QA 报告 0 issue。T02 7 页（5 个占位）、T12 7 页（9 个占位）、T26 8 页（5 个占位、7 张真实图）、T36 ≤5 页（5 个占位，符合"不超过 5 页"）。另手动验证：`--on-missing wait` 退出码 **4**、`overall=needs_input`、E–H 为 `skipped`；`provide` 拒绝空来源和损坏 PNG；`provide` 一个 5 节点流程图后图能进 deck（但见 B1、B5）。

#### 逐项验收（第 1 轮）

| 任务 | 结论 | 说明 |
| --- | --- | --- |
| (a) D 去重 | ✅ 通过 | T26 的 A001/A002 不再同一文件；候选审计里有 `demoted: already used by A001`；`shared_with` 契约字段 + 校验 + 测试齐。局限：去重只按文件/哈希，**不识别"同一建筑的另一张照片"**（T26 的 A002 仍是帕特农），验收标准字面上满足。 |
| (b) 用户补充请求 | ✅ 通过，有 2 个缺陷 | 契约/schema/fixture/测试齐；D 后确定性生成清单；`requests`/`provide` CLI；`wait` 退出码 4；`overall` 新取值；两条新硬门槛。缺陷：**B1**（resume 复用旧 E–H，provide 的材料不生效）、**B3**（请求文案仍写"稍后自绘"）。 |
| (c) E 映射 | ✅ 通过 | 只映射 acquired/provided；`unused_assets` 带原因；视觉模型缺失时 `relevance: not_assessed`（诚实）；missing 不被映射。 |
| (d) F 版式 | ⚠️ 基本通过，有缺陷 | 几何/容量/少字/页数约束/占位元素都有；T36 没超 5 页。缺陷：**B2**（把规划说明文字当正文印到页面上）、**B6**（版式质量：图小留白大、大圆角卡片）。 |
| (e) G 生成 | ⚠️ 部分通过 | 原生文本/形状/图片、备注署名、`pptx_opens`/`native_content`/`attribution_present` 是真检查；缺陷：**B4**（硬门槛可被改名骗过）、**B5**（流程图不画边、静默截断节点）。**该阶段将被 Office 渲染器取代，B5 不必在旧 G 里修，而要在新渲染器里验收。** |
| (f) H 渲染+QA | ⚠️ 部分通过 | LibreOffice→PDF→PNG 真跑、缺工具明确报错、有修复循环和复验记录、有测试；缺陷：**B6**（QA 对"看得出来的丑"报 0 issue）。 |

#### Bug 清单（第 1 轮，`4550cc1`）

- **B1 `runner.py:306/338/371/398`**：`--resume` 只要 `slide_asset_mapping.json`/`page_design.json`/`deck.pptx`/`qa_report.json` 存在就直接复用，**不比较上游输入是否变了**。复现：T36 跑完后 `pptv01 provide … U002 --file flow.json` 再 `--resume`，deck 里 U002 仍是 `PLACEHOLDER_U002`（要手工删 E–H 产物并重答 E 才生效）。任务书写的是"`--resume` 后 E–H 使用用户材料"，**未满足**。修法：在 `StageRecord` 里记录上游产物的 sha256，resume 时不一致就作废该站及下游；`provide` 成功后明确标记 E–H 为 stale。
- **B2 `f_layout/stage.py:501`**：`body_text = page.content_summary…` 被渲染成页面正文，而 `content_summary` 是 B 的**页面规划说明**（如"用一个简洁的图示说明概念，文字只保留最核心的一句"、"用开场提出观看建筑的几个角度，并交代后面怎么组织"），不是给听众看的文案。T26/T36/T02 的每页都印出了这种"导演提示"。修法：不渲染 `content_summary`；它进讲者备注或设计注释；正文只用 `key_points`。加测试：任何页面文本不得包含规划提示句式（至少断言文本集合 ⊆ `key_points ∪ title ∪ 署名/来源`）。
- **B3 `d_retrieve/stage.py:507-508`（`_MISSING_WHY`）及 C 的 `acceptance`（"若取用现成图须许可明确；否则自绘并说明"）**：写进 `user_input_requests.json.why` 的是 "diagram: nothing to retrieve; to be drawn natively in a later stage (F/G)" ——与产品规则（**不许自绘无来源的图**）矛盾，会诱导后续阶段"补画"。修法：`why` 改成"D 未找到可标来源的现成图；不允许凭空绘制，请用户提供结构化数据或图"；C 的 acceptance 去掉"否则自绘"。
- **B4 `grading/gates.py:92-105, 224`、`h_qa/inspect.py:207`**：`no_fabricated_visuals`/`attribution_present`/QA 的绑定检查**只看形状名字的正则**（`ASSET_A###…`）。复现：往 deck 里塞一张 A011 的图并命名为 `ASSET_A001__fake`，`pptv01 gates` 判 pass。另外 `is_visual` 只认 PICTURE/chart/table，**用普通矩形/连接线手画的"示意图"完全不在检查范围内**。修法：① 图片绑定要**用图片 blob 的 sha256 与 `asset_registry`/`inputs/` 里记录的 sha256 比对**；② 形状名用**白名单**（`bg_*`/`card_*`/`accent_*`/`title`/`bullets`/`summary`/`footer`/`PLACEHOLDER_U###`/已绑定的 `ASSET_/INPUT_/CHART_/TABLE_/DIAGRAM_`），出现白名单外的 AutoShape/Group/Freeform/Connector 就判 `unknown element`；③ 加"故意改名骗过""故意手画示意图"两个失败用例。
- **B5 `g_pptx/stage.py:243-282`（`_add_diagram_json_element`）**：① `edges` 只收集来源文字，**从不画连线**（T36 复现：5 个节点、2 条边的用户流程图只画出 3 个方框，没有箭头）；② `if nx + box_w > …: break`（行 260）**静默丢掉放不下的节点**（5 个节点只显示 3 个）；③ G 阶段不再校验节点/边的 `source`（只在 `provide` 时校验过一次）；④ `docs/stage-g-research.md` 声称用"原生连接线"，实际没有。**新渲染器里要用真连接线并对溢出报错或换行/换页，不许截断。**
- **B6 `h_qa/inspect.py`（全文件；PNG 只在 261-278 行查"是否空白/不可读"）**：QA 只做 PPTX XML 上的几何/估算，渲染出来的 PNG 基本没被分析。结果：T26 第 1 页是一张**空的大圆角卡片**、T36 的文字区大半空白、图片被缩成小图居中留白、各页图片大小不一致——全都报 "0 issue"。`font_or_ratio`/`text_overflow` 的依据是 `estimate_slot_capacity` 估算而不是真实排版。修法见第 5 节 R3/R5：用 PowerPoint 实测尺寸 + 可选视觉模型审图 + "留白/填充率"规则。
- **B7（D，已知弱点，评审新发现的实例）**：T02 的 A009 `File:Pekin park 20231019 0017.jpg` 是**美国伊利诺伊州 Pekin 的公园**（图里有美国国旗），不是北京（第 1 版任务书"北京公园照"的说法有误，已更正）；A011 `A Solarpunk tower.png` 是**插画**，不是真实城市场景；D 对这两张都判 `acquired`，只因为我在手写 E 答案时看图才发现并放进 `unused_assets`。D 仍然**没有任何看图核验**。（修法见 R6。）
- **B8（次要）** `README`/任务书里没有说明 `pptv01 provide` 之后需要 `--resume` 且要重答 E（B1 修好后应自动）；另外 `runs/*/user_input_requests.json` 的 `status` 在渲染后变成 `placeholder_rendered`，`provide` 能把它再改成 `provided`——行为合理，但请在 README 写明状态机。

#### 评审通过的做法（第 1 轮）
契约 + schema + fixture 同步机制；`user_input_requests` 由代码而非 LLM 生成；`no_fabricated_visuals`/`placeholders_marked` 两条硬门槛的**设计**（实现要加固，见 B4）；E 的 `not_assessed` 诚实标注；`--on-missing wait|placeholder`；占位的外观与命名；署名检查；每站调研一页。

---
## 5. 第 3 轮任务清单（先做 N2，再做 N0，然后 N1 / N3–N8；每项一个小批次）

> **基线：** 继续在 `feature/v01-office-renderer`（头 `7ceb945`）上做，或从它再拉 `feature/v01-office-renderer-2`；只推你自己的分支。规则（第 2 节）全部照旧。
> **先决：** 5B 的 Windows 实机测试由用户在真机上跑；**宝特收到回传之前，不要删旧 G、不要改默认渲染器。** **N2 未合入之前，不要再跑 5B 的 doctor / `windows_com` / Smoke 4**（上一轮真实 Windows 冒烟已因 B8 挂死）。N0、N1、N3、N4 大部分 Linux 就能测，可与 N2 分开提交，但 **推送顺序以 N2 为先**。

### 第 3 轮复核（2026-11-21，宝特；对象 `feature/v01-office-renderer` 头 `e90b80f`）：N0–N6 已推，剩两处必须修

宝特在 Linux 上清环境跑了全量 `pytest`：除下面第 1 条外全部通过（`windows_com` 用例按预期跳过）。N2 的方向正确（只用 `DispatchEx`、记录自己启动的 PID、每步和整个会话都有超时、生产代码 `rg GetActiveObject` 为 0），但还有一个会再次挂死的缺口（第 2 条）。**这两条做完、推送之后，再跑 5B。**

1. **N0 未通过（B17 未关）：** `tests/test_n0_windows_com_skip.py::test_n0_windows_com_skips_when_powerpoint_unavailable` 在没装 pywin32 的机器上失败。原因：`check_windows_com_skip_reason(platform_name="win32")` 先检查 `import pythoncom`，没装时返回的是 `"pywin32 not installed (...)"`，根本走不到用例想测的 `"PowerPoint COM unavailable"` 分支。修法：该用例用 `monkeypatch.setitem(sys.modules, "pythoncom", ...)` 和 `win32com` / `win32com.client` 的假模块把 pywin32 装上，再测 preflight 抛 `RendererUnavailableError` 的分支；另外单独加一条「pywin32 缺失 → 原因含 `pywin32 not installed`」的用例。不要改 conftest 的判断顺序来迁就测试。验收：Linux 清环境 `pytest -o addopts="" -rs` 退出码 0。
2. **N2 缺口：用户已开着 PowerPoint 时仍可能挂死。** PowerPoint 是单实例程序：用户已经开着它时，`DispatchEx("PowerPoint.Application")` 不会起新进程，而是连到用户那个实例上。此时 `_spawned_pids`（`post - pre`）为空：代码不会去关用户的 PowerPoint（这是对的），但超时后也没有「自己的进程」可以杀，于是仍会卡住，并且会在用户的实例里开文稿、弹对话框。修法（session.py）：
   - **启动前检查：** `preflight` / 会话开始时，如果 `_list_powerpnt_pids()` 非空，**直接抛明确错误并退出**（文案示例：「检测到 PowerPoint 已在运行（PID …）。PowerPoint 只允许一个实例，请先保存并关闭所有 PowerPoint 窗口再运行。」），不要调用 `DispatchEx`。可留一个显式参数/环境变量允许高级用户跳过，默认必须拒绝。
   - **启动后复核：** `DispatchEx` 之后如果 `_spawned_pids` 为空（说明连到了别人的实例），立即释放 COM 引用、不做任何操作、抛同样的明确错误。
   - **测试（fake COM，Linux 可跑）：** （a）启动前已有 POWERPNT.EXE PID → 抛错，且 `DispatchEx` 没被调用；（b）`DispatchEx` 后没有新 PID → 抛错，且没有对该实例调用 `Presentations.Add` / `Quit` / `taskkill`。
   - doctor 也要把「PowerPoint 正在运行」报成阻断项，并提示先关掉。

### 紧急：先做 N2（COM 会话必须隔离，否则冒烟会再挂死）

上一轮用户在真机跑 Smoke 4 时进程挂死。最可能的原因：用户当时开着 PowerPoint（在「文件 → 账户」里核对版本），COM 会话通过 `GetActiveObject` 连上了这个正在运行的实例（B8），和/或弹出了模态对话框，而代码没有超时看门狗，于是一直等下去。**在修好 B8 并推送之前，不要重跑 5B。**

要求（修 B8，规则 11；这是本轮**第一项**任务）：

- **自己的独立实例：** 只用 `win32com.client.DispatchEx("PowerPoint.Application")`（或等价的「新进程」启动方式）拉起**新的 PowerPoint 进程**，记下自己启动的 PID。Excel 同理（图表路径需要时）。
- **绝不**调用 `GetActiveObject` / `GetObject`，也**不要**用可能附着到已有实例的普通 `Dispatch`。文档字符串里写 `DispatchEx`、代码里却 `GetActiveObject` 的写法必须删掉。
- **绝不** `Quit`、关闭、最小化或以任何方式操作**用户已运行的 PowerPoint 实例**或**用户已打开的演示文稿**。只操作、只关闭、只终止自己启动的那个 PID。
- 会话开始即设 `DisplayAlerts = False`（以及规则 11 已要求的禁用宏 `AutomationSecurity`）。失败不得吞异常后报成功。
- **超时看门狗：** 每个 COM 调用有超时，整个 `preflight` / `build` / `export` / `measure` 会话有总超时。到期必须**中止并抛出明确错误**（写明卡在哪一步、自己启动的 PID、已等待多久），**禁止挂死**。超时后只终止自己启动的 PID（含因此拉起的 Excel），不得动用户的进程。
- **清理：** `__exit__` / `finally` 只杀自己启动的 PID；即使用户中途 Ctrl+C，也只清理自己的进程。
- **测试（fake COM，Linux 可跑）：** （1）用户已开着 PowerPoint（有已打开文稿 / 只有空窗口）时，我们既不附着、也不 `Quit` 它；（2）模拟某个 COM 调用挂起，看门狗在时限内触发、抛明确错误、只终止「自己启动」的假 PID。Windows 实机回归放到 N2 合入之后的 5B，而不是现在。

验收：上述 fake COM 测试全绿；代码里用 `rg GetActiveObject` 应为 0 命中（测试夹具模拟「用户已有实例」的除外，且测试必须断言生产代码没有去附着）。

### N0 全量 `pytest` 在 Linux/没装 Office 的机器上必须绿（修 B17；N2 之后立刻做）
- 在 `tests/conftest.py` 给 `windows_com` 标记统一自动跳过：非 Windows、`pywin32` 缺失或起不来 `PowerPoint.Application` 时 skip，原因写进 skip 信息；删除各测试里零散重复的守卫。
- 验收：Linux 上清环境 `pytest -o addopts="" -rs` 退出码 0（宝特会复核，当前是 2 failed）；Windows 上 `pytest` 与 `pytest -m windows_com` 各给退出码。注意：`windows_com` 实机测试也必须遵守 N2 的隔离与超时，否则会再次挂死。

### N1 doctor 如实报告 Office 版本类型，并清掉文档里写死的"2021"（B12 已确认：Microsoft 365 Build 20257.20196）
- `pptv01 doctor` 读 `HKLM\SOFTWARE\Microsoft\Office\ClickToRun\Configuration`（`ProductReleaseIds`、`VersionToReport`、`UpdateChannel`、`Platform`），再加 `Application.Version/Build`，输出"产品类型：Microsoft 365 订阅 / Office 2021·LTSC / 其他 / 未知"；`--json` 里也有。
- 文档与测试 docstring 里不要再写死"Office 2021"：改成"在 Microsoft 365 Build 20257.20196 上实测；仅使用 Office 2021 基线 API"（`windows-testing.md` §1、`stage-f-layout-calibration.md`、`test_r*_com_windows.py`）。
- **已确认环境**：Microsoft 365 桌面版，Version 16.0，Build 20257.20196（Current Channel）。把"AddChart2 需要窗口"等 COM 结论统一标注"已在 Microsoft 365 Build 20257.20196 上验证；Office 2021 基线未实测"。**API 约束不变**：凡新用到的 COM 成员，先查它在 Office 2021/Office 16 基线里是否存在（微软文档的 "Applies to" / 引入版本），在代码注释里写出；不确定就不用，或写成可降级路径。

### N3 H 修复循环与协议整洁（修 B10、B11）
- 修复循环重新编译时传 `template_path`；加"带模板 + 修复一轮后仍用模板母版"测试。
- `DeckRenderer.measure` 协议统一为 `measure(pptx, page_ids=None)`，删掉 `co_varnames` 探测。

### N4 硬门槛：白名单改精确集合（修 B9）
- 结构元素名只允许 `deck_ops.json`/`page_design.json` 里声明过的精确名；并按形状类型约束（结构元素只能矩形/文本框/直线；连接线必须 `DIAGRAM_*` 且两端绑在节点上）。
- 回归测试：`card_fake_*`/`accent_*` 名的手绘椭圆+折线图必须被 `no_fabricated_visuals` 拦住；原有 B4 的四种绕过继续被拦。

### N5 D 图片相关度：去拟合、堵"错图被选中"（修 B14、B15）
- 删除按题硬编码的 `_HOMONYM_CONFLICTS`（可留在测试夹具里当回归样例），改成通用判据：候选元数据里的地名/实体限定词与需求目标不一致 → 冲突。
- 需求有明确地点/实体目标时，候选元数据（标题/描述/分类/Wikidata 标签，含别名与译名）必须命中目标，否则算 `missing` 并生成用户补充请求——**不要退而求其次选一张"差不多"的图**。
- 验收：T02 的"北京"不再选出纽约的图；**另用 3 个与冒烟样本无关的同名异地例子**（自己找，如 Springfield、Paris Texas、Cambridge 之类）证明规则通用。宝特用私有盲测集复核。

### N6 C 校验否定句（修 B13）
- `SELF_DRAW_RE` 前先剥掉否定前缀；正反例测试各 ≥5 条。

### N7 python-pptx 退出生成路径（**须等 5B 回传：Smoke 4 在 PowerPoint 上通过之后**，修 B16）
- 当前不要动。回传通过后：（1）默认渲染器改为 `powerpoint-com`（Windows）/ 其他系统明确报错并提示 `--renderer compile-only`；（2）`pyproject.toml` 把 `python-pptx` 从核心依赖移到 `[verify]`，`grading/gates.py` 改惰性导入，缺失时给清楚的报错；（3）**单独一个提交**删除旧 `g_pptx` 与 `h_qa/repair.py` 里的 python-pptx 写路径；（4）LibreOffice 路径降为 `--cross-render` 兼容性参考。

### N8 把 5B 里发现的问题修掉
- 5B 回传会带来真实 PowerPoint 的渲染图和 QA 数据；宝特评审后在这里补具体条目（文字溢出、连线粘合、图表可编辑、版式质量等）。

### 不要现在做
Office.js add-in、macOS/Linux 渲染、云端批量（用户已说"以后再用云服务器"）。

---
## 5B. Windows 实机测试清单（用户在真机上跑；Antigravity 可陪同）

> 目的：把"只在 fake COM 里验证过"的东西，在**真实 PowerPoint** 上验证，并把结果发回给宝特。按顺序做，**每一步的输出都复制/保存**。
> 约定：路径用你机器上的仓库路径（下面以 `F:\ggagy\ai-acadmic-ppt-sys\v01` 为例）；PowerShell 里**不要把 pytest 接管道**（会吞退出码）。贴中文命令时建议用 **PowerShell 7**，或先执行 `chcp 65001`。
> **先决（2026-11-20 更新）：必须先合入 N2（COM 隔离 + 超时看门狗），再跑本节的 doctor / `windows_com` / Smoke 4。** 上一轮冒烟在用户开着「文件 → 账户」核对版本时挂死，根因就是 B8（`GetActiveObject` 附着到已运行实例，且无超时）。N2 未到之前不要重跑。

### 冒烟前检查单（doctor、`windows_com`、Smoke 4 之前都要做；每一步都做一遍）

上一轮挂死就是因为核对版本时 PowerPoint 还开着。按下面做，再开始任何会调 COM 的命令：

1. **先关掉所有 PowerPoint 窗口**（包括「文件 → 账户」那一个，以及任何恢复文档/受保护视图窗口）。Excel 若不是你正在用的，也关掉。
2. 打开**任务管理器**，在「详细信息」里看有没有残留的 `POWERPNT.EXE`、`EXCEL.EXE`。有就结束这些进程（结束前可截一张图留档）。PowerShell 等价命令：
```powershell
Get-Process POWERPNT,EXCEL -ErrorAction SilentlyContinue |
  Select-Object Id, ProcessName, StartTime | Format-Table
# 确认没有你还要保留的窗口之后：
Get-Process POWERPNT,EXCEL -ErrorAction SilentlyContinue | Stop-Process -Force
```
3. **冒烟/测试跑起来之后，不要再去碰 PowerPoint**（不要打开、不要点「文件 → 账户」、不要手动改正在生成的 deck）。需要看版本时，只在步骤 1 做，看完立刻按上面 1–2 关干净，再进入 doctor / 测试 / Smoke 4。
4. 跑之前先记一下当前的 PID 列表（上面的 `Get-Process`），方便对照「哪些是本次跑出来的」。

### 挂了怎么恢复

若命令长时间没有任何输出（例如超过 2–3 分钟还停在 `preflight` / `build` / `export`），按下面做，**不要强制关机**：

1. 在跑测试的 PowerShell 里按 **Ctrl+C** 中断。
2. 再看任务管理器 / `Get-Process POWERPNT,EXCEL`：只结束**本次跑起来的** `POWERPNT.EXE` / `EXCEL.EXE`（对照跑之前记下的 PID；不要杀你后来自己打开、且确认属于你正在编辑的文稿的进程）。
```powershell
Get-Process POWERPNT,EXCEL -ErrorAction SilentlyContinue |
  Select-Object Id, ProcessName, StartTime | Format-Table
# 确认是本次残留后再结束：
Get-Process POWERPNT,EXCEL -ErrorAction SilentlyContinue | Stop-Process -Force
```
3. 把挂死时的最后一段终端输出、是否弹出过激活/登录/安全模式对话框、任务管理器截图，一起回传给宝特。**修好 N2 之前不要立刻重跑。**


**0. 拿到最新代码和冒烟答案（不要 commit 这些）**
```powershell
cd F:\ggagy\ai-acadmic-ppt-sys
git fetch --all
git checkout feature/v01-office-renderer
git pull
git log --oneline -3                       # 期望最上面是 7ceb945 或更新
git checkout origin/feature/v01-pipeline -- v01/smoke v01/HANDOFF.md   # 只取冒烟答案和任务书，工作区会变脏，别提交
cd v01
```

**1. 记录 Office 版本（环境已确认为 Microsoft 365 Build 20257.20196，这一步只是把机器上的原始输出留档）**
```powershell
# 1a 注册表里的产品类型（订阅版含 "O365"/"M365"，2021 含 "2021"）
Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Office\ClickToRun\Configuration" |
  Select-Object ProductReleaseIds, VersionToReport, UpdateChannel, Platform, CDNBaseUrl | Format-List
# 1b PowerPoint 可执行文件版本（路径若不同请自行找 POWERPNT.EXE）
$ppt = "C:\Program Files\Microsoft Office\root\Office16\POWERPNT.EXE"
if (Test-Path $ppt) { (Get-Item $ppt).VersionInfo | Format-List ProductVersion, FileVersion }
```
再打开 PowerPoint → **文件 → 账户**，截图（或抄下「产品信息」里那一行，如"Microsoft 365 应用版/ Office 2021 专业增强版"，以及"关于 PowerPoint"里的版本号）。**看完立刻关掉 PowerPoint，并按上面的「冒烟前检查单」确认任务管理器里没有 `POWERPNT.EXE` 残留，然后再做第 2 步。不要开着账户页跑 doctor / 测试 / 冒烟。**

**2. doctor**
```powershell
.\.venv\Scripts\python.exe -m pptv01.cli doctor
"DOCTOR_EXIT=$LASTEXITCODE"
.\.venv\Scripts\python.exe -m pptv01.cli doctor --json | Out-File -Encoding utf8 doctor.json
```
**回传：** 1a/1b 输出、账户页截图或文字、`doctor` 文本输出、`doctor.json`。

**3. 测试（清环境，分别看退出码）**
```powershell
Get-ChildItem Env:PPTV01_* -ErrorAction SilentlyContinue | Remove-Item
.\.venv\Scripts\python.exe -m pytest -o addopts="" -rs -p no:cacheprovider > pytest_all.txt 2>&1
"PYTEST_ALL_EXIT=$LASTEXITCODE"
```
```powershell
.\.venv\Scripts\python.exe -m pytest -m windows_com -o addopts="" -rs -v -p no:faulthandler > pytest_com.txt 2>&1
"PYTEST_COM_EXIT=$LASTEXITCODE"
.\.venv\Scripts\python.exe -m pytest -m "not windows_com" -o addopts="" -rs > pytest_nocom.txt 2>&1
"PYTEST_NOCOM_EXIT=$LASTEXITCODE"
```
**回传：** 三个 txt 文件和三个退出码；特别注意**最后一行摘要**（`N passed, N failed, N skipped`）。跑完后看任务管理器里有没有残留的 `POWERPNT.EXE`/`EXCEL.EXE`（有就记下数量，别手动关之前先截图）。

**4. Smoke 4：在真 PowerPoint 上跑 T02/T12/T26/T36**
> **先再走一遍「冒烟前检查单」**：所有 PowerPoint 窗口已关，任务管理器里没有残留 `POWERPNT.EXE`/`EXCEL.EXE`，跑的过程中不要去碰 PowerPoint。若挂死，按「挂了怎么恢复」处理。
> 用宝特手写的 A–C/E 答案（标注 `agent:宝特 (manual, not an API model)`）和冻结的 `research_brief.json` 回放。D 会联网取素材（Wikimedia/Wikidata/OpenAlex 等）；**如果你所在网络访问不了，会在 D 的报告里如实写出，把输出发回即可，别绕。**
```powershell
$env:PPTV01_LLM_BACKEND = "agent"
Remove-Item Env:PPTV01_RESEARCH -ErrorAction SilentlyContinue
$P = @{
  T02 = "帮我介绍一下城市热岛效应。"
  T12 = "帮我准备一个咖啡店创业计划展示。"
  T26 = "做一份介绍世界著名建筑的PPT。"
  T36 = "做一份不超过5页的人工智能介绍，尽量少用文字。"
}
foreach ($t in "T02","T12","T26","T36") {
  New-Item -ItemType Directory -Force "runs\smoke4\$t","runs\smoke4\io_$t" | Out-Null
  Copy-Item "smoke\agent-answers\$t\io\*.json" "runs\smoke4\io_$t\"
  Copy-Item "smoke\agent-answers\$t\research_brief.json" "runs\smoke4\$t\"
  $env:PPTV01_AGENT_RESPONSES_DIR = "runs\smoke4\io_$t"
  .\.venv\Scripts\python.exe -m pptv01.cli run $P[$t] --out runs\smoke4 --run-id $t --resume --renderer powerpoint-com --on-missing placeholder > "runs\smoke4\$t.log" 2>&1
  "RUN_EXIT_$t=$LASTEXITCODE"
  .\.venv\Scripts\python.exe -m pptv01.cli gates "runs\smoke4\$t" > "runs\smoke4\$t.gates.log" 2>&1
  "GATES_EXIT_$t=$LASTEXITCODE"
}
```
- 退出码 `3` 表示 agent 后端在等答案（D 取到的素材和我的答案对不上）：把 `runs\smoke4\io_Txx\` 里最新的 `*_request_*.json` 一起回传，**不要自己编答案**。
- 期望：`RUN_EXIT=0`（`overall=complete_with_placeholders`）、`GATES_EXIT=0`。

**5. 用肉眼检查 `deck.pptx`（每题至少看 3 页，截图）**
- [ ] 用**桌面 PowerPoint**直接双击打开，没有"修复/无法读取"提示；
- [ ] 每页标题是**标题占位符**（点选后左侧"大纲视图"里能看到标题文字）；
- [ ] 图片是**铺满槽位且不变形**（裁切而不是拉伸）；
- [ ] 占位框是**虚线**、写着 `PLACEHOLDER_U###`，没有任何"看起来像真图"的自绘图；
- [ ] 文字**没有超出文本框或页面**；中文字体正常（不是方块/缺字）；
- [ ] T26/T02 里的参考文献/图片署名在**备注**里能看到；
- [ ] 流程图（若有 `provide` 过的）：节点之间是**真连接线**，拖动节点时线跟着动；
- [ ] 图表（若有）：右键 → 编辑数据能打开 Excel；
- [ ] 版式观感：图是否够大、留白是否过多、是否仍然满屏圆角卡片——**写一句主观评价**。

**6. 打包回传（`runs/` 被 gitignore，别提交）**
```powershell
foreach ($t in "T02","T12","T26","T36") {
  Compress-Archive -Force -DestinationPath "smoke4-$t.zip" -Path `
    "runs\smoke4\$t\deck.pptx","runs\smoke4\$t\deck_dump.json","runs\smoke4\$t\qa_report.json", `
    "runs\smoke4\$t\gate_report.json","runs\smoke4\$t\run_report.json","runs\smoke4\$t\deck_ops.json", `
    "runs\smoke4\$t\page_design.json","runs\smoke4\$t\render","runs\smoke4\$t.log","runs\smoke4\$t.gates.log"
}
```
若某个文件不存在（例如 H 没跑到），`Compress-Archive` 会报错——把报错原文发回，并把已有文件手动打包。

**回传清单（给宝特/用户）：** ① 第 1 步（版本）的全部输出 + 账户页；② `doctor` 输出与 `doctor.json`；③ `pytest_all.txt`/`pytest_com.txt`/`pytest_nocom.txt` 与三个退出码；④ 四个 `smoke4-Txx.zip` 与 `RUN_EXIT_*`/`GATES_EXIT_*`；⑤ 第 5 步的勾选结果和 3–5 张你觉得最差的页面截图；⑥ 任何卡住、弹窗（激活/登录/安全模式）、残留 `POWERPNT.EXE`/`EXCEL.EXE` 进程的情况。

**宝特拿到后会做：** 用 PowerPoint 渲染图重评 PPTEval 的 Design 维度；对照 `qa_report.json` 的 `BoundHeight` 看 QA 是否抓得到"看得出来的丑"；核对 `deck_dump.json` 与 `gate_report.json`；决定是否执行 N7（默认渲染器切换、删 `g_pptx`）。

## 5A. 第 2 轮任务清单 R0–R7（**已完成并评审，见 4.1；以下为原文归档，勿重做**）

> 用户的原话：「不能用 py pptx，用我说的 Office COM / VBA / Office.js API 自动化；保留优质思路；确保后续优化 copy reuse 1st；好改的就使劲改」。
> **路线结论（理由与证据见 `docs/renderer-office-research.md`）：主路线 = Windows 上 Python + pywin32 驱动桌面 PowerPoint 的 COM。** VBA 只当对象模型参考；Office.js（PowerPointApi 到 1.10 仍无法创建/读写图表）只留给第二阶段"插入当前文档"，**本轮不做**。
> **架构要点：** F 只产出后端无关的 `page_design.json`；新增纯 Python 编译层把它编成确定性的 `deck_ops.json`；COM 执行器只负责把操作翻成 COM 调用。这样契约和编译层在 Linux/CI 上就能测，只有执行器需要 Windows。

### 已确认的决定（用户 2026-11-20 回复，取代原"待确认问题"）
1. **目标环境（2026-11-20 更新，用户实测）：Microsoft 365 桌面版 PowerPoint，Version 16.0，Build 20257.20196（Current Channel）。** 原先写的"Office 2021 永久授权版"不成立，已作废。Excel 仍须由 `pptv01 doctor` 实测确认（版本/Build、`Excel.Application` 能否创建）。**策略不变：只用 Office 2021 / Office 16 基线里也存在的 COM 对象模型**，保证在 2021 或 LTSC 上也能跑；**不得使用 Microsoft 365 专属特性**：不用 Office.js 的 PowerPointApi 1.8+ 新接口（本轮本来也不做 Office.js）、不用 M365 才有的功能（如 Designer、Copilot、云端字体/图标库、动态数组图表等）；（`AddChart2`、`AddConnector`、`TextFrame2.TextRange.BoundHeight`、`Presentation.Export`、`ExportAsFixedFormat` 等都是 Office 2013/2016 起就有的）。
2. **是真正的 Microsoft PowerPoint，不是 WPS**。不需要适配 `KWPP.Application`。
3. **Antigravity 就跑在用户同一台本机 Windows 上**，不是云服务器 → 它可以**直接测 COM**；R2/R3/R7 的真机验收由它在本机跑并汇报，宝特只在 Linux 侧复核产物（zip）。
4. **python-pptx 只读校验器可以接受**：放 `[verify]` extra，只读、永不写文件（规则 9 不变）。
5. **云端批量生成：以后再说，不在本轮范围。** 用户有云服务器（由云宝收集的免费额度机器，例如 Linux 的 Simular 盒子）：**Linux 云机不能跑 COM**；要云端批量就得 Windows VM + 交互式桌面会话（微软不支持无人值守自动化 Office，KB257757），届时再评估，**本轮不要为此写任何代码**。

**据此对下面任务的调整：**
- **R2**：`preflight()` 额外记录 `Application.Version == "16.0"` + Build；`AddChart2` 在真机上先做一个最小实测（建图→写数据→关 Excel→保存→重新打开），结果写进 `docs/stage-g-office-research.md`（注明测于 Microsoft 365 Build 20257.20196；2021 基线未实测）；若 Excel 缺失或 `AddChart2` 失败，**明确报错/把图表请求回退为用户补充请求或占位**，不得静默画假图。
- **R3**：`Presentation.Export` 的 PNG 尺寸与字体渲染以本机 PowerPoint（Microsoft 365）为准；`render_provenance` 写明 `powerpoint-com 16.0 (build …)`。
- **R7**：`pptv01 doctor` 必检项增加：`Application.Version` 为 16.0，Build 与产品类型（Microsoft 365 / Office 2021·LTSC / 其他）如实打印，**不因产品类型报错**、**Excel 存在且 `Excel.Application` 可创建**、中文字体（如微软雅黑/等线）是否安装、`NameFarEast` 设置能否生效。
- 所有验收里凡写"Windows"处，具体指**用户本机 Windows + Microsoft 365 桌面版 PowerPoint**；Linux 侧只跑不依赖 COM 的测试。

### R0 先还账：修评审里与渲染器无关的 bug（可在 Linux 上做）
- 修 **B1**（resume 失效）、**B2**（`content_summary` 泄漏）、**B3**（"稍后自绘"文案）。
- **验收：** 每个 bug 一个失败再转绿的测试；B1 的测试要走"provide → resume → deck/ops 里出现该输入的元素且不是占位"；B2 的测试断言页面可见文本 ⊆ `title ∪ key_points ∪ 署名/来源`；B3 的测试断言所有 `why`/`acceptance` 不含"自绘/drawn natively"。全量测试绿。

### R1 契约 + Renderer 协议 + 编译层（Linux 可测）
- 先写你自己的《调研一页》`docs/stage-g-office-research.md`（可大量引用宝特的 `renderer-office-research.md`，补上你实际核对过的许可证/commit，别照抄结论）。
- 新契约 `DeckOps(_RunFile)`（`contracts.py` + schema + fixture + 测试）：操作集至少含 `add_slide(layout)`、`set_placeholder_text`、`add_textbox`、`add_shape`、`add_picture(bind, crop)`、`add_table(bind)`、`add_chart(bind)`、`add_diagram(nodes, edges)`（编译成 `add_shape` + `add_connector`，**节点/边数量超出槽位就换行/换页或报错**）、`add_placeholder_box(U###)`、`set_notes`、`set_alt_text`、`set_name`。每个操作带 `element_id`、绑定（`A###`/`U###`）、`source_refs`；**没有绑定的视觉操作编译器直接拒绝**。
- `Renderer` 协议（见调研 3.1 节：`preflight / build / export / measure`），包里**只放协议和 PowerPoint 实现**；不放 mock 实现（测试用假 COM 只在 `tests/`）。
- 编译层 `g_build/compile_ops.py`：`page_design.json + registry + user_input_requests → deck_ops.json`，确定性；`design → ops` 黄金文件测试。
- **验收：** 契约/schema/fixture/测试；Smoke 4 在 Linux 上能走到产出 `deck_ops.json`（G 的编译子步骤 `done`，渲染子步骤在没有 Windows/PowerPoint 时 `not_run`，**绝不假装 deck 已生成**；`run_report.overall` 不得是 `complete`）；编译层对"未绑定视觉""超出槽位的流程图"的拒绝/换行有测试。

### R2 PowerPoint COM 执行器（Windows）
- 目录 `v01/src/pptv01/renderers/powerpoint_com/`。**复用来源（先复制改写再自研）**：自家 `aprd/.../reconstructor_verifier.py:468 render_and_measure_via_powerpoint_com`；`thomas-villani/pptlive`（MIT）；`ykuwai/ppt-mcp`（MIT，`charts.py`/`connectors.py`/`export.py`）。登记 `NOTICE.md`，锁 commit。
- 用 `win32com.client.DispatchEx("PowerPoint.Application")`（pywin32，放 `[windows]` extra）；从 `.potx` 模板建 deck（先用 PowerPoint 自带的空白模板，之后 R5 换成自己的）；**标题/正文用原生占位符**；中文设 `Font.Name` + `Font.NameFarEast`；图片 `AddPicture(LinkToFile=False, SaveWithDocument=True)` + 裁切填充；表格 `AddTable`；图表 `AddChart2` + `ChartData.Workbook` 写数据，`finally: wb.Close(False)`（Excel 泄漏防护）；流程图用 `AddConnector` + `BeginConnect/EndConnect`；备注写来源与许可（沿用现有署名/备注规范）；命名沿用 `ASSET_A###__…`/`INPUT_U###__…`/`PLACEHOLDER_U###`/`DIAGRAM_…`。
- `preflight()`：记录 `Application.Version/Build`、是否有 Excel、字体表；PowerPoint 不可用 → `RendererUnavailableError`（退出码另定并文档化），不得回退。
- 保存后**再用 COM 读回形状清单**写 `deck_dump.json`（名称、类型、位置、文字、绑定），作为 H 与硬门槛的第二证据。
- **验收：** ① 假 COM 单测（`tests/` 下，摘 pptlive `fake_powerpoint` 的思路）验证调用序列；② `@pytest.mark.windows_com` 真机用例：构建一个含占位、图片、表格、图表、5 节点带箭头流程图的小 deck，用**只读校验器**重新打开，页数/形状数/名称/节点与连接线数量都对；③ Smoke 4 在 Windows 上 A→G 出 `deck.pptx`（由 PowerPoint 保存）；④ 不动用户已打开的演示文稿的测试/说明；⑤ 5 节点流程图**5 个节点 4 条边全部可见**（B5 的回归）。

### R3 H 改用 PowerPoint 渲染 + 实测 QA（Windows）
- `export()`：`Presentation.Export(dir,"PNG",1920,1080)` → 重命名 `render/slide_001.png…`；`ExportAsFixedFormat(...,2)` 出 `render/deck.pdf`。
- `measure()`：每个文本形状读 `TextFrame2.TextRange.BoundHeight/BoundWidth`、`AutoSize`、实际字号、字体；与形状框比较 → `text_overflow` 用**测量值**判定；把"估算容量 vs 实测"逐页记录到 `qa_report.json`（给 R5 校准用）。
- 几何/间距/遮挡检查：摘 `pptlive/_linting_geometry.py`、`ykuwai/layout_check.py` 的**纯算术部分**，保持"读取"和"计算"分离以便在 Linux 单测。
- 缺字体检查；`render_provenance` 写明渲染器（`powerpoint-com` + 版本）。LibreOffice 渲染保留为**可选第二渲染器**（`--cross-render`），只做兼容性交叉检查，不再是主证据。
- 修复循环：**只改 `page_design.json`（或 ops 参数）→ 重新编译 → 重新 build → 复验**；上限和记录沿用现有。
- **验收：** Windows 上 Smoke 4 的 `render/slide_*.png` 由 PowerPoint 产出；故意构造文字溢出/越界/遮挡能被检出且有测量证据；PPTEval `pptv01 grade runs/<id> --slides-dir runs/<id>/render` 能走 `pptx_render` 证据级别（评分归宝特）。

### R4 硬门槛加固（Linux + Windows 都能跑）
- 修 **B4**：绑定按 blob **sha256** 校验；形状名**白名单**；手画示意图（无绑定 AutoShape/Group/Connector 组成的"图"）判失败；`attribution_present` 同步用 sha256 绑定。
- 对象：**真实 COM 产出的 pptx**（以及 `deck_dump.json`）。校验器用 `[verify]` extra（python-pptx 只读，或 `zipfile+lxml`），代码顶部写明 "read-only, never writes"。
- **验收：** 新增失败用例：改名骗过、手画示意图、占位缺备注、署名缺失；在 R2 的真机 deck 上全 pass。

### R5 版式与设计质量（"好改的就大改"）
- 推翻 F 里不好的默认样式：不再用"整页大圆角卡片 + 小图居中留白"。参考 PPTAgent 的**模板/版式驱动**思路（MIT）：用 `.potx` 的版式与占位符驱动布局；图片在槽位里**裁切填充**；同类页面统一图片尺寸与对齐；文字区按内容高度收缩，不留大块空白。
- 用 R3 记录的"估算 vs 实测"数据**校准** `f_layout/capacity.py`（可参考 ppt-master 的 `slot_capacity`），写出校准前后误差。
- 新增 QA 规则：留白/填充率（卡片内文字占比过低）、图片槽位填充率、同页元素对齐。
- **验收：** Smoke 4 的 PowerPoint 渲染图里不再出现空卡片/大片空白；QA 对故意做出的"空卡片页"能报警（B6 的回归）；宝特会看图 + PPTEval Design 评分。

### R6（D）图片与主题相关度核验
- 修 **B7**：在 D 或 E 做真实的相关度判断：有视觉模型（`PPTV01_E_VISION_MODEL`）就看图；没有就在 registry 标 `relevance: not_assessed`，**不得**当作通过。至少加一条无模型也能做的确定性规则：Commons 的分类/描述里出现与目标地名/主体冲突的字样（如 `Pekin` 对 `Beijing/Peking`）要降级并写原因；插画/渲染图（`Illustration`/AI-generated 分类）不得作为"真实场景"证据。
- **验收：** T02 的 A009 被降级或标 `not_assessed`+`needs_review`；用 `tests/replay` 的真实录制数据写测试。

### R7 Windows 实测协议与 `pptv01 doctor`
- 新增 `pptv01 doctor`：打印 Windows 版本、PowerPoint 版本/Build、是否有 Excel、Python 与 pywin32 版本、soffice/pdftoppm 是否可用、可用字体检查；任何必需项缺失给出明确修复提示。
- 文档 `docs/windows-testing.md`：从零到 Smoke 4 的 PowerShell 步骤（见第 1 节 Windows 命令），预期产物清单，常见错误（`pywin32_postinstall`、弹窗卡死、Excel 残留进程）。
- **验收：** 在用户的 Windows 机器上按文档走通一次，汇报里贴 `pptv01 doctor` 输出。

### 不要现在做（写进"未做"即可）
Office.js add-in（"插入当前文档"）、macOS/Linux 渲染、云端批量。用户若要再开。

### Windows 上怎么测（旧版，已被第 5B 节取代）
1. `pptv01 doctor` 全绿。
2. 清环境全量 `pytest`（PowerShell 写法见第 1 节），**看 `$LASTEXITCODE`**；`pytest -m windows_com` 单独跑，汇报里分别给出两个退出码和用例数。
3. Smoke 4：沿用 agent 后端的 A–C 答案（`PPTV01_AGENT_RESPONSES_DIR`），E 同样可由宝特手答；跑 `pptv01 run … --resume` 到 H，检查 `deck.pptx`（用 PowerPoint 打开能正常编辑、标题是占位符、流程图连线可拖动）、`render/slide_*.png`、`qa_report.json`、`pptv01 gates`。
4. 把 `runs/<id>/` 里的 `deck.pptx`、`render/`、`qa_report.json`、`deck_dump.json` 压成 zip 交给宝特（`runs/` 被 gitignore，不要提交），宝特在 Linux 侧做 PPTEval 与对照看图。

---
## 6. 已知弱点（接手时就要知道，别当作已解决）

1. **图片与主题的相关度没有被程序核验**（B7、R6）：D 只靠 Wikidata P18 / Commons 元数据 + 文本相关性。实跑里：T02 的 A009 是美国 Pekin 的公园（不是北京）、A011 是插画；T26 的佛光寺是屋檐局部、萨伏伊别墅是室内浴室。
2. **过滤误杀**：B 的 fastembed@0.30 在 T02 把相关的 "Green roof" 过滤掉了；基准样本很小（3 个任务、65 条笔记，标注由 agent 自己做、不独立，结构规则是在这批数据上拟合的）。bge-reranker 已评估，不优于 fastembed，默认关闭（`docs/relevance-benchmark.md`）。
3. **A/B/C 的答案是 agent 手写**（agent 后端），不是 API 模型的产出；换成真实模型后质量与失败模式未知。
4. **D 不画图也没有结构化数据源**：diagram/chart_data/statistic/table/icon 全部 `missing` → 用户补充请求（第 3 节）；目前没有任何用户提供的真实材料跑过完整链路（只有测试夹具）。
5. **去重只按文件/哈希**：不识别"同一建筑的另一张照片"（T26 的 A001/A002 都是帕特农）。
6. **PPTEval 的 Design 维度还没有用 PowerPoint 渲染图评过**（R3 之后才有可信证据）；LibreOffice 渲染图只作兼容性参考。
7. 检索只覆盖 Wikipedia/Crossref/OpenAlex/Commons；OpenAlex 匿名配额小，配额错误会如实记录，会让参考文献偏少。
8. **A/B/C/E 的答案仍是 agent 手写**，不是 API 模型的产出。
9. **QA 对"丑"没有感知**（B6）；版式质量靠 R5 重做。
10. **COM 只能在用户桌面 Windows + 桌面 PowerPoint 上跑**，不是云服务（微软 KB257757 不支持无人值守自动化 Office）；macOS/Linux 不可行。宝特的 Linux box 不能跑 COM，所有 COM 验收靠 Windows 实测。
11. **目标机器已确认（用户实测）：Microsoft 365 桌面版 PowerPoint（Version 16.0，Build 20257.20196，Current Channel）**，不是 Office 2021。**策略不变：只用 Office 2021 / Office 16 基线也有的 API**，保证在 2021 或 LTSC 上也能跑；但 2021 上没实测过。Antigravity 在同一台本机 Windows 上运行（见 5A「已确认的决定」）。R2–R5 的真实 PowerPoint 行为目前只有 fake COM 验证，**未经宝特复核**。云端批量（含 Windows VM 方案）**后做**。

---

## 7. 宝特如何评审，你要汇报什么

**评审节奏**
- **Smoke 4（T02、T12、T26、T36）**：每个重要提交都跑一遍，看产物、门槛、`run_report`；不通过就打回。
- **Regression 12（T02 T04 T06 T08 T12 T16 T26 T32 T36 T37 T39 T40）**：每完成一个开发批次（比如 (b) 完成、(c) 完成）跑一遍。
- **盲测集**：宝特私有，不进仓库，不告诉你题目内容；不要针对 `benchmark/v1.0` 的具体题做硬编码/拟合（宝特会用盲测集检查过拟合）。
- 评分（PPTEval、硬门槛、人工看图）由宝特执行并回你结论；你可以本地跑 `pptv01 gates` 自检，但不要把自评分当结果。

**每次汇报（发给宝特/用户）必须包含**
1. 分支名（`feature/v01-office-renderer`）与提交 hash 列表（一句话说明每个提交做了什么）；每个 R 任务对应哪些提交；
2. 清环境全量 `pytest` 的结果（命令、**pytest 自身退出码**、用例数、skip 数及原因；Windows 与 Linux 分别写）；R2/R3 另附 `pptv01 doctor` 输出；
3. 对应任务的验收清单逐条自检（做了/没做/做不到及原因）；
4. 新增的调研一页（路径）与引入的第三方代码及许可证；
5. 若改了契约：改了什么、为什么、schema/fixture/测试是否同步；
6. 你已知的新弱点或未完成项。

做不到就如实写"做不到 + 原因"，不要用假数据凑齐验收项。
