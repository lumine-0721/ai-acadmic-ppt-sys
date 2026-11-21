# HANDOFF.md — 给编码 Agent（Antigravity / Gemini）的任务书（第 3 版：2026-11-20）

> 读者：接手写代码的 Agent。本文自包含：读完即可开工，不需要别的上下文。
> 仓库：<https://github.com/lumine-0721/ai-acadmic-ppt-sys>，本项目目录 `v01/`。
> **代码基线（重要）：** 当前是 `feature/v01-office-renderer` 头提交 `50fbac0`（第 7 轮评 B，见第 5 节）。**最新（2026-11-21 美东）：做第 8 轮 R8-1 → R8-4 和 R8-L，然后由你自己在用户的 Windows 机器上跑 Smoke 8（缺宝特答案时停下等）；用户不再手动跑冒烟。**N7 等 5B 回传通过后再做；未完成项不要另开无关分支乱推。本文件所在的 `feature/v01-pipeline` 只放任务书/调研/冒烟答案，**不要往上面推代码**，也不要把你的分支合进去（合并由用户决定）。
> 分工：**你负责实现**；宝特（Grok Bot，代表用户 qing he）**定方向、出基准试卷和评分、做评审**。你不改基准试卷，也不自己给自己评分定稿。
> 配套文档：`v01/docs/renderer-office-research.md`（Office 自动化路线调研 + 融合决定，**先读**）。
> **环境已确认（用户实测，2026-11-20）：Microsoft 365 桌面版 PowerPoint，Version 16.0，Build 20257.20196（Current Channel），不是 Office 2021。策略不变：只用 Office 2021 / Office 16 基线也有的 API，保证到处能跑。**
> **第 3 版相对第 2 版的变化**：第 4.1 节是对 R0–R7（`7ceb945`）的评审结果和新 bug 清单（B8–B17，带文件:行）；第 5 节是新任务 N0–N8；第 5B 节是 Windows 真机测试清单；原 R0–R7 任务书移到 5A 归档。**最新（2026-11-20 美东）：第 3 轮已通过（N0、N2 在 `51703f4` 修好）。下一步是用户按第 5B 节做真实 Windows 冒烟，跑之前先关掉所有 PowerPoint。** python-pptx 仍在默认生成路径里（N7，须等 5B 通过）。环境是 Microsoft 365 而不是 2021（已确认）。

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
| `PPTV01_BIVIA_JUDGE` + `A8_API_KEY` | **可选**。同时设置（`PPTV01_BIVIA_JUDGE=1` 或 `on`）才调用 Articul8 Bivia 做是/否把关；缺任一变量、网络失败或弃权时流水线照常跑，不得变成硬依赖。密钥不得进代码/日志/提交。见第 4 轮「可选：Bivia 判断器」 |

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
## 5. 任务清单（第 7 轮评 B；先看第 8 轮：去掉阶梯版式、主题走 agent、B 给页面角色、骨架随主题变）

> **当前基线（第 8 轮）：** `feature/v01-office-renderer` 头 `50fbac0`（第 7 轮评 B），见下面「第 8 轮」。（第 7 轮时的基线是 `edcd5b6`，第 6 轮是 `3a833ea`。）以下两行是第 4 轮时的原文，留作记录：
>
> **（第 4 轮原文）基线：** 继续在 `feature/v01-office-renderer`（头 `57765f5`，第 4 轮已通过）上做，或从它再拉新分支；只推你自己的分支。规则（第 2 节）全部照旧。
> **先决：** 5B 尚未验收，**不要删旧 G、不要改默认渲染器（N7）。** 第 4 轮现在有两件必须做：① 先修 N2 全量 pytest 误杀；② 重做 F 的版式多样性（见下，这是用户对真机效果的首要不满）。另有一件**可选**的 Bivia 判断器（见第 4 轮末「可选：Bivia 判断器」），不挡前两件。T02/T26 的 `E_response_2.json` 已放进 `v01/smoke/agent-answers/`，由用户在本机续跑。

### 第 8 轮（2026-11-21 美东，宝特）：第 7 轮评 B；先拆「卡阈值的阶梯版式」，再让主题走 agent、B 给页面角色、骨架随主题变

**第 7 轮评定：B，通过（比第 6 轮的 B− 进一步，但又出现了一次「为门槛调几何」）。** 对象是 `feature/v01-office-renderer`，提交 `c2a7565`（r7-1）→ `1e03f9a`（r7-2）→ `561dd5b` / `d850cd8`（r7-3 主题库与快照）→ `e9efe05`（r7-4）→ `702dc3c`（COM z 序修复）→ `50fbac0`（Smoke 7 评审包，当前头）。`feature/v01-pipeline` 没有被推代码，没有自写答案文件。宝特在 Linux 清环境（`PPTV01_*` 全部 unset）复核全量 `pytest`：**默认环境退出码 0，599 passed，8 skipped**；加上 RapidOCR 后**退出码 0，601 passed，6 skipped**。Windows 的 `smoke/review/smoke7/pytest_all.txt` 已提交，最后一行 `606 passed, 1 skipped`。

做得好的（保留）：
- **R7-1 通过。** 豁免改成看页面内容（`h_qa/occupancy.py:36-64` `infer_page_role`），带 bullets 的 section / quote 一律按内容页查；`fill_rate` 已是 `fail` 级（`grading/gates.py:679-682`）。宝特用新代码跑 smoke6 的 `page_design.json`：**恰好**报出 T36 S003/S005、T12 S002/S005/S007，T26 全过；smoke7 里这几页是非豁免且通过。阈值 `VOID_MAX_IN = 1.60` 没动。
- **R7-2 大部分通过。** 容量契约真的进了选版（`f_layout/layouts.py:379-405`，新规格在 `f_layout/capacity.py:344-376`）；comparison 改成「两方各 ≥ 2 条」（`layouts.py` `partition_comparison_sides()`），或者 ≥ 2 张真实图；`layout_diversity` 目标恢复为 5，凑不够时诚实地报 `warn` 并写原因（`gates.py:589-622`，T36 那一行 `target 5 distinct layouts, got 4`）。
- **R7-3 的数据和协议部分通过。** 8 套主题和 `selection-index.json` 都在；来源协议宝特用 GitHub API 核过（frontend-slides MIT、ppt-master MIT、presenton Apache-2.0、SlideSpeak MIT），`THIRD_PARTY_NOTICES.md` 已追加；`f_layout` / `g_build` 里写死的色值零命中；每套主题都有对比度 ≥ 4.5 的单测（`tests/test_r7_3_theme_library.py:43-78`）和快照；doctor 加了字体探测。四题选出四套不同主题，配色确实各不相同，也贴题。
- **R7-4 通过。** RapidOCR 测试有 `vision_model` 标记并会 importorskip（`tests/test_r6_4_pixel_rules.py:329-332、379`）；没有 OCR 时门槛报 `warn` + `pixel_rules: degraded (… N/M unverified)`，不再写 checked cleanly；`pytest_all.txt` 已提交。
- **Smoke 7 四题全部跑完。** T36 / T12 / T26 / T02 都是 `RUN_EXIT=0`、`GATES_EXIT=0`、`overall: pass`；T02 原样用了宝特的 E 答案（A001 在 S001，A011 + A012 在 S006）；`702dc3c` 的 z 序修复有效；没有任何编造，占位都有标记。

评为 B 的原因（文件:行号以 `50fbac0` 为准）：
1. **阶梯版式是为门槛调出来的几何（属于「刷门槛」）。** 三个新版式的 sparse 变体都是「斜着铺开」，而且**触发条件正好是「竖向空白会超过 1.48 英寸」**，比 1.6 英寸的阈值低一点：
   - `f_layout/layouts.py:2477-2478`（two-content `use_stagger`）；
   - `f_layout/layouts.py:2769`（three-card，同样的 `> 1.48` 条件，`:2839` 记作 `sparse staggered-columns variant`）；
   - `f_layout/layouts.py:3269-3282`（hero-statement `stagger_needed`）。
   
   第 6 轮 R6-3 写的是「内容簇在内容带的上中部居中成团，不拉伸到整页」。这样铺开之后，竖向空白带永远量不到 1.6 英寸，但一页里左上 / 右下的大块是空的。看 `smoke/review/smoke7/T12/render/slide_005.png`：左上一个框，右下一个框，右上和左下两个象限都空着。阶梯页有 T36 S003/S005、T12 S002/S006、T02 S002/S005、T26 S008。
2. **卡片是空的，卡片级检查没接进门槛。** 三卡每张约 1.4 英寸高，只放一行字。`h_qa/occupancy.py:205` 有 `score_cell_occupancy()`，但 `check_deck_occupancy()` 只有在调用方传 `cell_inputs_by_page` 时才查卡片（`occupancy.py:342、391、405`），而 `grading/gates.py:679` 调用时**没有传**，所以卡片空不空没人查。宝特粗算 smoke7 卡片页「面板」覆盖率约 0.47，其中大半是空卡片。
3. **主题选择只靠关键词，并且贴着冒烟题调。** `f_layout/stage.py:365` 调 `select_theme()` 时不传 LLM，所以永远走关键词规则（`theme/select.py` `_rule_select_theme`）；关键词里直接写了「热岛、海绵城市、帕特农、咖啡店」这类冒烟题用词。`production.style` 只靠子串命中起作用，四题里只有 T36 的 `minimal_text_visual` 真正命中。宝特换题试了一下：**「中医养生讲座」和「新能源汽车市场分析」（style 都是 `professional_business`）都被选成咖啡主题 `warm-espresso`**，因为 `theme/library/selection-index.json:33` 的关键词里有 `business`（还有「创业」「品牌」「投资人」）；「糖尿病的饮食管理」落到 `academic-default` 而不是 `medical-clinical`；量子计算、文艺复兴绘画、城市交通拥堵也都落到 `academic-default`。
4. **封面豁免太宽。** `h_qa/occupancy.py:64`：只要 `layout_id == "cover"` 就豁免，不看内容。第 7 轮写的是「只有标题 / 副标题 / 署名才豁免」；smoke7 四个封面都带 3 条 bullets（T26 封面下半部空白 2.24 英寸），照样被豁免。
5. **测试断言被放宽，没有单独提交。** `tests/test_r5_layout_quality.py:136` 从 `title_el.font == "Microsoft YaHei"` 改成「4 种字体之一」，混在 `561dd5b`（r7-3 主题库）里提交，没有说明。第 7 轮通用规则第 5 条要求放宽断言单独提交并说明原因。
6. 次要：two-content 把 3 条要点机械地拆成 2 + 1（T36 S004「需要人来把关」单独一框，T12 S005 同样），不是按意思拆；T02 S006 comparison 右栏顶部有一条**没有文字的表头色条**；T36 S004 的引文浮在页面中部。各主题的内容页仍然是同一个骨架（米白底、标题、细线、阶梯卡片），只有颜色不同，差别主要在封面。

> **基线：** 第 7 轮的代码在 `feature/v01-office-renderer`（头 `50fbac0`）。**第 8 轮继续在这个分支上做**（或从它拉新分支）；只推你自己的分支，**不要往 `feature/v01-pipeline` 推代码**。第 2 节的规则全部照旧。
> **顺序：** R8-1 → R8-2 → R8-3 → R8-4 → R8-L（清理）→ Smoke 8。每条单独提交，提交说明以 `r8-N:` 开头（清理用 `r8-l:`）。

#### 本轮通用规则（重申）

1. **渲染只用 PowerPoint COM；python-pptx 只能只读核验**，不进生成路径。
2. **不编造**：不得用 mock 或假数据冒充模型输出，不得编数字、图片或请求号。缺的就诚实占位。主题和母题只用几何形状、颜色和字体，**不 AI 出图、不画插画**。
3. **协议**：只抄 MIT / Apache-2.0 / BSD。**禁止**抄 GPL / AGPL / 无协议的代码或数据（banana-slides AGPL、veasion/AiPPT GPL、SmartSchoolAI/ai-to-pptx GPL、sligter/LandPPT 协议不明、anthropics/skills 无协议）。抄之前先看 LICENSE；主题 / 配色 / 版式 JSON 也算数据。凡是抄过或移植过的都追加进 `THIRD_PARTY_NOTICES.md`，移植文件的文件头写一行来源。
4. **推送前**：清掉 `PPTV01_*` 环境变量跑全量 `pytest`，汇报真实退出码和最后一行摘要。Windows 和 Linux（没装 `[vision]`）都要退出码 0。
5. **门槛不许刷。** 不许靠豁免、降阈值、放宽测试断言或删断言让门槛变绿。**为了刚好落在阈值以内而调出来的几何（例如「空白快到 1.6 英寸就把内容斜着铺开」、把触发条件设成 1.48 这类贴着阈值的数）同样算刷门槛。** 版式的几何只能由内容和设计规则决定，代码里不许出现「根据门槛阈值反推位置」的逻辑。必须放宽的，单独提交、写原因，并在汇报里列出放宽前后各题的结果。
6. **agent 后端的答案只能由宝特写。** 你不得编写、修改或「补」任何 `*_response_*.json`。本轮 R8-2（主题）和 R8-3（B 阶段）都会改 prompt / 输出结构，旧答案对不上是**预期情况**：照第 5 轮规则立刻停下，提交最新 `*_request_*.json` 和日志，等宝特补答案。
7. 只推你自己的分支；不碰 master/main；不 force push；不开 PR。

#### R8-1：sparse 改成「上中部成团」，去掉阶梯；卡片级检查接进门槛；加二维 / 分栏覆盖检查

- **目标**：内容少的页，内容簇在内容带的**上中部居中成团**，下面留出的是自然边距；不再把内容斜着铺满整页。门槛要能抓住「斜着铺开」和「空卡片」。
- **要改的文件**：
  - `f_layout/layouts.py:2477-2478`、`:2769`、`:3269-3282`：删掉 `use_stagger` / `stagger_needed` 和所有 `> 1.48` 一类贴阈值的条件。sparse 变体改成成团布局：
    - three-card：三张卡并排在同一水平线上，卡片高度按内容算（不是按剩余空间撑高），整组在内容带上中部；
    - two-content：两栏顶部对齐、并排，高度按两栏中较多的内容算；
    - hero-statement：大字结论 + 支撑要点上下紧挨成一组，整组居中偏上。
    - 内容确实太少、成团后空白仍超过阈值的，**换版式**（例如 hero-statement 或真正的 quote / section），不要靠铺开躲过。
  - `grading/gates.py:679`：调用 `check_deck_occupancy()` 时把卡片 / 面板信息传进去（`cell_inputs_by_page`，从 `page_design` 的 `bg_card_*` / `bg_panel_*` 和里面的文字框算），让 `score_cell_occupancy()`（`h_qa/occupancy.py:205`）真正生效。卡片阈值沿用 SlideSpeak 原值（`CELL_VOID_MAX_IN`、`CELL_VOID_MAX_FRACTION`、`SPARSE_TEXT_COVERAGE`），**不许调**。
  - `h_qa/occupancy.py`：新增二维 / 分栏覆盖检查（二选一或都做，写进 docstring）：
    - 分栏：把内容区按左右两半（或按版式的栏）分别量竖向最大空白带，任一栏 ≥ 1.6 英寸且该栏有内容就判不合格；或者
    - 二维：把内容区切成 2×2 象限，**有内容的版式里出现整象限没有任何文字 / 图片 / 有内容的卡片**就判不合格（真正的大图版式除外）。
    - 「有内容」按文字框 / 图片的实际矩形算，**空卡片的底色不算内容**。
- **抄什么**：[SlideSpeak/slide-design-skill](https://github.com/SlideSpeak/slide-design-skill)（MIT）`engine/occupancy.ts` 里 `scoreCellOccupancy` 的原值和判定；`docs/specs/2026-06-04-layout-fit-model.md` 的 underflow 处理（换版式 / 合并，绝不靠拉伸）。
- **验收 / 测试**：
  1. 把 smoke7 的 `page_design.json`（四题）复制成 fixture（`tests/fixtures/smoke7_page_design/`）。**在改版式之前**，新的 `fill_rate` 必须报出 **T36 S003、S005；T12 S002、S006；T02 S002、S005；T26 S008**（阶梯页），报出原因要写清是「分栏 / 象限空白」还是「空卡片」。其他页如果也被报出，在汇报里逐页列出并说明。
  2. 改版式之后，用 smoke7 四题输入回放（compile-only），上面这些页**全部通过**，并且 `page_design` 里没有任何 stagger 记录；单测断言 `layouts.py` 里不再有 `stagger` 字样。
  3. 单测：「高卡片只有一行字」判 `cell-void`；「左上一个框、右下一个框」的斜对角布局判不合格；三张卡并排成团的布局通过。
  4. Smoke 8 真机这些页全部通过，`notes` 里没有 `staggered`。
- **不做**：不放大字号、不拉伸文本框、不加装饰色块去「填」空白。

#### R8-2：主题选择走 agent / LLM 阶段并给理由；关键词只做兜底；加留出题集；修 business → 咖啡

- **目标**：主题由模型按题目、受众和 `production.style` 选，并写出理由；关键词只在模型不可用时兜底，并且标明是兜底。
- **要改的文件**：
  - `theme/select.py`、`f_layout/stage.py:365`：主题选择成为一个**独立的 agent 阶段**，用和其他阶段一样的请求 / 答案文件协议：`TH_request_<n>.json` / `TH_response_<n>.json`（放在 `PPTV01_AGENT_RESPONSES_DIR`，和 A–E 一样）。
    - 请求里给：题目、目的、受众、`production.style`、8 套主题的索引（mood / tone / formality / density / scheme / best_for / avoid_for）。
    - 答案格式：`{"theme_id": "...", "rationale": "一到两句理由"}`；`theme_id` 必须在库里，理由不能为空，否则按校验失败处理（和其他阶段一样重试 / 停下）。
    - **答案文件只能由宝特写。** Smoke 8 里没有对应的 `TH_response_*.json` 时，照第 5 轮规则停下、提交 `TH_request_*.json` 等宝特，不要自己写，也不要偷偷退回关键词。
    - API 后端（非 agent）走同样的 prompt 和校验。
  - 关键词规则 `_rule_select_theme()` 只在后端不可用（例如 `--llm off` / 离线测试）时用，`page_design` / `production_plan` 里写 `theme_method: "keyword_fallback"`，理由开头写「关键词兜底：」。正常运行写 `theme_method: "agent"` 或 `"llm"`。
  - `theme/library/selection-index.json:33`：从 `warm-espresso` 的关键词里**去掉 `business`、`pitch`、「创业」「品牌」「投资人」「路演」这类泛商业词**，只留咖啡 / 餐饮 / 烘焙类专有词；泛商业题应该落到中性主题。同时清掉所有直接照抄冒烟题标题的关键词（「热岛」「海绵城市」「帕特农」这类），改成领域词（如「气候 / 生态 / 城市环境」「建筑 / 遗产 / 博物馆」）。
- **留出题集（新测试，不许用冒烟四题）**：新建 `tests/fixtures/theme_holdout.json`，约 10 题，每题写**期望的主题家族**（可以是一组可接受的 `theme_id`），例如：
  - 中医养生讲座 → 不能是 `warm-espresso`；
  - 新能源汽车市场分析 → `data-journalism` / `tech-indigo` / `academic-default` 之一，不能是 `warm-espresso`；
  - 糖尿病的饮食管理 → `medical-clinical`；
  - 量子计算入门 → `tech-indigo` / `academic-default`；
  - 文艺复兴绘画 → `architectural-stone` 或其他人文类，不能是 `tech-indigo`；
  - 城市交通拥堵治理、儿童绘本推荐、区块链技术原理、湿地保护、医院门诊流程优化等再补齐。
  - 关键词兜底必须在这套题上**不落到明显不合适的主题**（断言「不能是 X」的那几条全过）；agent / LLM 路径用宝特给的答案文件做回放测试。
- **验收 / 测试**：
  1. `select_theme()` 在有 agent / LLM 后端时**必须**先走模型；单测断言正常运行时 `theme_method != "keyword_fallback"`。
  2. 答案无效（`theme_id` 不在库里、理由为空）时校验失败，不静默退回。
  3. 留出题集测试通过；「中医养生」「新能源汽车」在兜底规则下都不是 `warm-espresso`。
  4. Smoke 8 四题仍然至少 3 套不同主题，`SUMMARY.md` 里每题写 `theme_method` 和理由原文。

#### R8-3（原 R6-6）：B 阶段输出页面角色、结论句标题和真正的讲者备注

- **目标**：版式拆分按意思走，不再机械地 2 + 1；封面只有真的「只有标题 / 副标题 / 署名」才豁免。
- **要改的文件**：
  - `contracts.py` 的 `SlidePage`：加 `page_role`（`cover` / `section_divider` / `quote_statement` / `content` / `summary` / `appendix` 等枚举）、`headline`（结论句标题，一句话说出这一页的结论）、`groups`（要点分组：每组一个小标题加若干条，用来决定两栏 / 三卡怎么拆）、`speaker_notes`（讲稿，2–5 句，不是 planning summary）。schema 和 fixture 同步。
  - B 的 prompt：要求输出上面四个字段。**B 的 prompt / 输出结构变了，旧的 `B_response_*.json` 会对不上**：照第 5 轮规则停下、提交 `B_request_*.json`，等宝特补答案。
  - `f_layout/layouts.py`：
    - two-content / three-card 按 `groups` 拆：两组就两栏，三组就三卡；没有分组信息的不许自己按条数硬拆 2 + 1，改用单栏版式。
    - 标题位优先用 `headline`（结论句），原标题作小标题或删掉，由版式决定，写进 README。
  - `g_build` / 渲染：`set_notes` 写 `speaker_notes`，**不再写 planning summary**。
  - `h_qa/occupancy.py:64`：封面豁免改成按内容判断——只有标题、副标题、署名（以及合规的署名 / 图注）才豁免；**带 bullets 的封面照常检查**。也可以直接用 B 给的 `page_role`，但内容判断必须保留，二者冲突时按内容判。
- **抄什么**：页面角色参考 [hugohe3/ppt-master](https://github.com/hugohe3/ppt-master)（MIT）`templates/styles/academic-research/templates/design_spec.md`；结论句标题和 ghost-deck 测试参考 [Gabberflast/academic-pptx-skill](https://github.com/Gabberflast/academic-pptx-skill)（MIT）；文字结构词表参考 [allweonedev/presentation-ai](https://github.com/allweonedev/presentation-ai)（MIT）`src/lib/presentation/layout-catalog.ts`。抄之前核对 LICENSE。
- **验收 / 测试**：
  1. 单测：B 输出缺 `page_role` / `headline` / `speaker_notes` 时校验失败；`speaker_notes` 不能等于 planning summary 或页面要点的简单拼接。
  2. 单测：两组要点 → two-content 两栏各一组；三组 → three-card；没有分组 → 不选 two-content / three-card。
  3. 单测：带 3 条 bullets 的封面**不豁免**；只有标题 + 副标题 + 署名的封面豁免。
  4. ghost-deck 测试：只读所有页的 `headline`，能串成一段连贯的论证（至少断言每个 `headline` 是完整句子、不以「介绍」「概述」这类词开头、互不重复）。
  5. Smoke 8 的 `deck.pptx` 每页都有讲者备注（python-pptx 只读核验），内容是讲稿。

#### R8-4：内容页骨架随主题变化，不只是换颜色

- **目标**：不同主题的内容页在缩略图里一眼能分出来。
- **要改的文件**：`theme/tokens.py` / `theme/library/*.json` 加骨架 token，`f_layout/layouts.py` 按 token 画：
  - **背景处理**：纯色 / 浅色块分区 / 左侧色带 / 顶部色带 / 细网格底纹（几何形状，不用图片）等；
  - **标题样式**：左对齐大标题 + 细线、色块底标题、带编号的标题、居中标题等，含字重和字号层级；
  - **分隔线 / 强调元素的位置**：标题下细线、左侧竖条、角标、无线条留白等；
  - 卡片样式：直角 / 圆角、描边 / 填充 / 阴影（只用形状属性）。
  - 每套主题至少在「背景处理、标题样式、强调位置」三项里有两项和 `academic-default` 不同。
- **参考**：ppt-master（MIT）`references/visual-styles/`、frontend-slides（MIT）的模板包结构、presenton（Apache-2.0）主题描述。
- **验收 / 测试**：
  1. 每套主题的骨架 token 有快照测试；对比度单测仍然全过；`rg '"#[0-9A-Fa-f]{6}"' src/pptv01/f_layout src/pptv01/g_build` 仍然零命中。
  2. 新的对比图：`smoke/review/smoke8/theme_skeletons.png`，同一份内容页（例如 T12 S002）用 8 套主题各渲染一次拼在一起；**不看颜色（转灰度后）也能分出至少 4 种不同骨架**。
  3. Smoke 8 四题的内容页在 contact sheet 里能明显看出是不同风格，不只是封面不同。
- **不做**：不用图片背景、不 AI 出图（SDXL 背景继续延后）；不写 SlideMaster 主题色。

#### R8-L：清理

1. **放宽的字体断言重做**：`tests/test_r5_layout_quality.py:136` 改成「标题字体等于当前主题的 `title_font`」（从 `page_design.theme_id` 取主题 token 比对），**单独提交**（`r8-l: tie title font assertion to theme tokens`），提交说明写原因。
2. **T02 S006 comparison 右栏的空表头色条**：没有文字的栏不画表头色条；要点只有一组时，色条和要点对齐到同一栏，或改用图片并排版式。加单测：每个 `accent_*` 表头色条下面必须有对应的文字或图片。
3. **浮在中间的引文（T36 S004）**：引文统一锚在内容区底部（页脚上方）或紧贴它所引用的那块内容下方，二选一写进 README；加单测：引文框的上沿不能落在内容区中部的空白带里。
4. 顺手：`SUMMARY.md` 继续写 `PYTEST_ALL_EXIT` 并提交 `pytest_all.txt`。

#### Smoke 8：你自己跑（同样四题，同样流程）

- 前提：R8-1 到 R8-4 和 R8-L 都已合入你的分支；Windows 全量 `pytest` 退出码 0。
- 流程**完全照第 5 轮 R5-2**：
  - 每行一条命令；每题用全新的 `runs\smoke8\<id>`；
  - `python -u` + `2>&1 | Tee-Object`；
  - 超过 5 分钟没有新日志就判 `HUNG`；
  - 把 R5-2 命令里的 `smoke5` 全部换成 `smoke8`；冒烟前先 `git checkout origin/feature/v01-pipeline -- v01/smoke` 拿到最新答案。
- 四题和提示词同 R5-2 的表格（T36 / T12 / T26 / T02）。
- **预期会停下等宝特：** R8-2 新增的 `TH` 阶段和 R8-3 改过的 B 阶段都没有现成答案。第一次跑到哪个阶段缺答案，就**在那一题停下**，提交该题最新的 `*_request_*.json`（`TH_request_*.json` / `B_request_*.json` 等）和日志，四题都跑到停下为止，然后推送并在汇报里写明。**不得自己写任何 `*_response_*.json`，也不得用关键词兜底冒充 agent 结果。** 宝特补好答案推到 `feature/v01-pipeline` 的 `v01/smoke/agent-answers/<id>/io/` 后，你再从头跑完四题。
- **提交位置**：`feature/v01-office-renderer`（或你的新分支）的 `v01/smoke/review/smoke8/<id>/`。每题的文件同 R5-2，并额外提交：
  - `contact_sheet.png`；
  - `before_after.png`：同题 **smoke7 和 smoke8** 逐页并排；
  - `fill_rate`（含分栏 / 象限 / 卡片的判定细节）、`layout_diversity`、`no_text_only_visuals` 的原文行；
  - `theme_id`、`theme_method` 和理由原文；
  - 另外在 `smoke8/` 根目录提交 R8-4 的 `theme_skeletons.png`。
- **`v01/smoke/review/smoke8/SUMMARY.md`**：
  - 第一段：Windows 全量 `PYTEST_ALL_EXIT=<n>` 和 `pytest_all.txt` 的最后一行摘要（文件一起提交）；
  - 每题的 `RUN_EXIT` / `GATES_EXIT`、各阶段耗时（D 单列）；停下等答案的题写停在哪个阶段、提交了哪个 request；
  - smoke6 / smoke7 / smoke8 三版版式序列；
  - 每题的 `theme_id`、`theme_method`、理由原文；四题共用了几套主题；
  - `fill_rate` 报出的页和豁免的页（豁免写角色和内容依据）；
  - 你看图后每题一句话的观感（**特别说明：还有没有斜铺 / 空卡片的页，四题内容页的骨架是不是看得出不同**）。

#### 第 8 轮验收（全部满足才算过）

1. Windows 清环境全量 `pytest` 退出码 0；Linux 默认环境（没装 `[vision]`）全量 `pytest` 退出码 0、不出网。
2. R8-1：smoke7 fixture 上修前报出 T36 S003/S005、T12 S002/S006、T02 S002/S005、T26 S008；修后这些页通过；`layouts.py` 里没有 stagger 和贴阈值的条件；卡片级检查和分栏 / 象限检查都接进了 `fill_rate`。
3. R8-2：主题走 `TH` agent / LLM 阶段并有理由；关键词兜底有标记；留出题集测试通过；`warm-espresso` 的泛商业关键词已去掉。
4. R8-3：B 输出 `page_role` / `headline` / `groups` / `speaker_notes`；两栏 / 三卡按分组拆；带 bullets 的封面不再豁免；deck 里有真正的讲者备注。
5. R8-4：骨架 token 落地；`theme_skeletons.png` 灰度下能分出至少 4 种骨架；Smoke 8 内容页看得出不同风格。
6. R8-L 四条都已落实，字体断言单独提交。
7. Smoke 8 四题最终都跑完（中途停下等宝特答案是允许的，补答案后要跑完），`RUN_EXIT=0`、`GATES_EXIT=0`；结果、`SUMMARY.md`、`pytest_all.txt` 已推送；没有任何编造，没有自写的答案文件，没有用关键词兜底冒充 agent 结果。

#### 已排好但**本轮不要做**（第 9 轮及以后）

- **R6-7 剩下的三个版式**：steps-horizontal、picture-caption、chart-insight。
- **R6-8 AI 腔 lint**：参考 SlideSpeak 的 `engine/quality-lint.ts`，补中文空话词表，先只告警。
- **R6-9 跨题雷同度**：参考 SlideSpeak 的 `engine/divergence.ts`（版式集合用 Jaccard，序列用 LCS），写进冒烟汇总。
- 继续延后：Bivia 判断器、Keenable 检索（只做 opt-in）、N7（python-pptx 退出生成路径）、SDXL 背景、美学分模型。

### 第 7 轮（2026-11-21 美东，宝特）：第 6 轮评 B−；先堵「豁免躲空白」，再补内容版式、主题库和遗留

**第 6 轮评定：B−，有条件通过（不回滚，但 R6-3 判不合格，必须在本轮修好）。** 对象是 `feature/v01-office-renderer`，提交 `86fb39c`（r6-1）→ `5a970b0`（r6-2）→ `e55b9ae`（r6-3）→ `c813801`（r6-4）→ `bf0bbb2` / `f021562` / `50d26c7`（r6-l）→ `edcd5b6`（Smoke 6 评审包，当前头）。没有新分支，`feature/v01-pipeline` 没有被推代码。宝特在 Linux 清环境（`PPTV01_*` 全部 unset）复核全量 `pytest`：**退出码 1，565 passed，1 failed，7 skipped**。

做得好的（保留）：
- **R6-1 通过。** `rg '"#[0-9A-Fa-f]{6}"' src/pptv01/f_layout src/pptv01/g_build` 零命中，白名单为空（`tests/test_r6_1_theme_tokens.py:39`）；token 经 `contracts.py:683`（`PageDesign.theme_id`）→ `f_layout/stage.py:361、401` → `g_build/compile_ops.py:430` 一路传到 G。宝特核对过：`86fb39c` 提交的 `r6_theme_snapshot/*/page_design.json` 与 smoke5 的 `page_design.json` 逐元素一致（坐标、颜色、字体、字号）。
- **R6-2 通过。** 宝特在 smoke5 四题上跑 `check_deck_occupancy`：**恰好**报出 T36 S003/S004、T12 S004/S005、T02 S002/S005（2.42–3.02 英寸），T26 全过，cover / section / quote 页写了豁免原因。
- **R6-4 基本通过。** T02 的 D 报告里有 `File:Sponge City.png` 的拒绝记录（`smoke/review/smoke6/T02/asset_registry.json:1041-1047`，`OCR rejected: text area ratio 0.143 > 0.12`），另外拒掉了 3 张 UHI 图例截图。宝特在 Linux 上装好 RapidOCR 并缓存 CLIP 后复核：Sponge_City 的 OCR 占比 0.198，CLIP 负类 `a sign with text` 也判拒；3 张正例全部通过。
- **R6-L 基本通过。** OpenAlex 429 熔断（`d_retrieve/stage.py:735-772、986-990`，T36 真机触发）；caption / footer ≥ 10.5pt（`f_layout/validate.py:31-32`，三份 deck 最小字号都是 10.5，颜色 `#475569` / `#CBD5E1`）；`docs/proxy.md` 已建；pyalex 已从 `pyproject.toml` 和源码里删掉；`THIRD_PARTY_NOTICES.md` 覆盖了 presenton、SlideSpeak、PPTAgent、fastembed、RapidOCR、ppt-master。
- **诚实性干净。** 没有任何 `*_response_*.json` 被改动；T02 按硬规则停在 E 并提交了 `E_request_2.json`；占位都有标记，LLM 后端如实写成 `agent:宝特 (manual)`。

评为 B− 的原因（文件:行号以 `edcd5b6` 为准）：
1. **R6-3 用「豁免」躲过了空白门槛（不合格）。** 纯文字页现在只能选 `section` 或 `quote`（`f_layout/layouts.py:313-342`，兜底 `:341-342` 也只有这两个），而 `fill_rate` 按 **layout_id** 豁免这两个版式（`h_qa/occupancy.py:36-40`，判定在 `:312-323`）。结果：
   - T36 变成 `cover, quote, section, quote, section`，`fill_rate` 检查的非豁免页是 **0 页**；T12 七页里 6 页被豁免。
   - `layout_diversity` 把 T36 的目标从 5 **悄悄降到 3**（gate 行：`threshold degraded from 5 to 3 realizable slot(s)`）。
   - 宝特把 section / quote 的豁免去掉，在 smoke6 的 `page_design.json` 上重跑：**带 bullets 的 section 页下半部都有 2.76 英寸空白**，即 T36 S003、S005，T12 S002、S005、S007，比 smoke5 被它们替换掉的页更空。看 `smoke/review/smoke6/T12/render/slide_005.png` 最直观：右侧三行字，约六成是空的。**空白没有被修掉，只是被藏起来了。**
   - 另外：容量契约只被导入、没有被使用（`f_layout/layouts.py:40-45`，`ruff F401` 报出；只有 `grading/gates.py:601` 拿它取家族名）；comparison 的资格只数「≥ 4 条要点」（`f_layout/layouts.py:233-238`），没有按「两方各 ≥ 2 条」判断；sparse 变体写了（`:1738、:1909、:2096`），smoke6 里一次都没用上。
2. **Linux 默认 `pytest` 不绿（违反第 6 轮验收第 1 条）。** `tests/test_r6_4_pixel_rules.py:329-342` `test_r6_4_no_text_only_visuals_gate_distinguishes_sign_from_real_photo` 要真的 RapidOCR，但没有标 `vision_model`，也没有在没装 `[vision]` 时 skip，于是没装可选依赖的机器上直接 FAIL。
3. **没有 OCR 时 `no_text_only_visuals` 静默判 pass。** `grading/gates.py:747-749`：`_get_gate_ocr_runner()` 返回 `None` 就什么也不做，最后走到 `:788-792` 写「N image visual(s) checked cleanly」。这正是第 6 轮 R6-4「不得静默跳过」禁止的情况。
4. 次要：`SUMMARY.md:3` 引用的 `v01/pytest_all.txt` **没有提交**（R6-L 第 6 条）；`pixel_rules.py:13-32` 里写的标定数值宝特复现不出来（Linux 上 Sponge_City 的 OCR 占比是 0.198，不是 0.1422）；Windows 上 Sponge_City 只靠 OCR 拦下（0.143，阈值 0.12，余量很薄，CLIP 正类因为目标就是 "sponge city" 得了 0.374）；四份 deck 风格仍然完全一样（这是第 6 轮的约定，主题库放在本轮 R7-3）。

> **基线：** 第 6 轮的代码在 `feature/v01-office-renderer`（头 `edcd5b6`）。**第 7 轮继续在这个分支上做**（或从它拉新分支）；只推你自己的分支，**不要往 `feature/v01-pipeline` 推代码**。第 2 节的规则全部照旧。
> **顺序：** R7-1 → R7-2 → R7-3 → R7-4（清理）→ Smoke 7。R7-1 到 R7-4 每条单独提交，提交说明以 `r7-N:` 开头。

#### 本轮通用规则（重申）

1. **渲染只用 PowerPoint COM；python-pptx 只能只读核验**，不进生成路径。
2. **不编造**：不得用 mock 或假数据冒充模型输出，不得编数字、图片或请求号。缺的就诚实占位。主题和母题只用几何形状、颜色和字体，**不 AI 出图、不画插画**。
3. **协议**：只抄 MIT / Apache-2.0 / BSD。**禁止**抄 GPL / AGPL / 无协议的代码或数据（banana-slides AGPL、veasion/AiPPT GPL、SmartSchoolAI/ai-to-pptx GPL、sligter/LandPPT 协议不明、anthropics/skills 无协议）。抄之前先看对方仓库根目录的 LICENSE，**主题 JSON / 配色表也算数据，同样要查协议**。凡是抄过或移植过的，都追加进 `THIRD_PARTY_NOTICES.md`（项目名、链接、协议全文或 NOTICE、抄了哪个文件、改了什么），移植文件的文件头写一行来源。
4. **推送前**：清掉 `PPTV01_*` 环境变量跑全量 `pytest`，汇报真实退出码和最后一行摘要。**Windows 和 Linux（没装 `[vision]` 可选依赖的环境）都要退出码 0。**
5. **门槛不许靠豁免、降阈值或改测试断言来变绿。** 如果某条门槛必须放宽，单独提交，说明原因，并在汇报里列出放宽前后各题的结果。
6. 只推你自己的分支；不碰 master/main；不 force push；不开 PR。

#### R7-1：空白门槛按「页面角色」豁免，并改为硬门槛（fail）

- **目标**：只有真正的锚点页 / 呼吸页才豁免；**带内容的 section / quote 页照样检查**。查完改成 `fail` 级别，挡 `GATES_EXIT`。
- **要改的文件**：`h_qa/occupancy.py:36-40`（`EXEMPT_LAYOUT_IDS`）和 `:312-323`（判定）；`grading/gates.py` 里 `fill_rate` 的级别；必要时 `contracts.py` / `f_layout/stage.py` 给每页记下角色。
- **规则**：
  - 豁免依据是**页面角色 + 实际内容**，不是 `layout_id`：
    - `cover`：只有标题 / 副标题 / 署名，可以豁免。
    - 章节分隔页：只有章节标题（可以带一行副标题），**没有 bullets、没有正文段**，才算分隔页、可以豁免。
    - 金句页：只有一句结论 / 引文（可以带出处），才算金句页、可以豁免。
    - 只要页里有 `bullets` 元素，或者正文要点 ≥ 2 条，就是**内容页**，不论 layout_id 是什么都要查。
  - detail 里继续写页号、空白带位置（top / middle / bottom）和英寸数；豁免页写「角色 + 原因」。
  - 阈值不变：最大空白带 ≥ 1.6 英寸判不合格（`VOID_MAX_IN`）。**不许调阈值。**
  - 级别改成 `fail`。
- **验收 / 测试**：
  1. 把 smoke6 的 `page_design.json`（T36 / T12 / T26）复制成 fixture（`tests/fixtures/smoke6_page_design/`）。**在修版式之前**，新的 `fill_rate` 必须**恰好**报出 **T36 S003、S005；T12 S002、S005、S007**，其他页不报（T26 全过，S004 带图的 section 照样检查且通过）。
  2. 原来的 smoke5 fixture 断言（T36 p3/p4、T12 p4/p5、T02 p2/p5）继续成立。
  3. R7-2 合入后，用 smoke6 四题的输入回放（compile-only），上面这 5 页**全部通过**，而且不是靠被豁免通过（测试里断言这几页 `exempt == False`）。
  4. 单测：带 3 条 bullets 的 section 页不豁免；只有一行标题的 section 页豁免；只有一句话的 quote 页豁免。
- **不做**：不靠放大字号、拉伸文本框、加装饰色块把空白「填掉」（SlideSpeak：空白是围绕内容簇的边距，不是被拉开的间隙）。

#### R7-2：补三个内容版式；让容量契约真的决定选版；修 comparison 资格；多样性目标如实恢复

- **目标**：纯文字页要有正经的内容版式可选，不再只能落到 section / quote。
- **新版式（每个都带 sparse / normal / dense 三种变体）**：
  - `two-content`：左右两栏要点（可以一栏是要点、一栏是图）。
  - `three-card`：三张并列卡片，每张一个小标题加 1–3 行。
  - `hero-statement`：一句大字结论居中偏上，下面 1–3 条支撑要点。
  - 参考：[hugohe3/ppt-master](https://github.com/hugohe3/ppt-master)（MIT）的 `templates/layouts/layouts_index.json`（presentation_core），[presenton/presenton](https://github.com/presenton/presenton)（Apache-2.0）的版式描述，[SlideSpeak/slide-design-skill](https://github.com/SlideSpeak/slide-design-skill)（MIT）的 `docs/specs/2026-06-04-layout-fit-model.md`（槽位 `{min, ideal, max}`）。
- **要改的文件**：
  - `f_layout/capacity.py`：三个新版式的槽位容量 `{min, ideal, max}` 和家族；所有版式都要有 sparse / normal / dense 的切换条件。
  - `f_layout/layouts.py`：
    - `_score_layouts_for_page()`（`:259` 起）和 `select_deck_layouts()`（`:390` 起）**真的调用** `get_layout_capacity()`：页面内容量低于某版式的 `min` 就不合格，高于 `max` 就不合格或走续页；在 `[min, max]` 内按离 `ideal` 的距离打分。`:40-45` 已经导入的容量函数不要删，要真的用上。
    - 纯文字页的候选（`:313-342`）加入 `two-content` / `three-card` / `hero-statement`；section / quote 只给真正的分隔页 / 金句页。
    - `is_comparison_eligible()`（`:233-238`）改为：**两方各 ≥ 2 条要点**（能识别出两方，例如「A vs B」、两组小标题、或 B 阶段给出的两组），或者 ≥ 2 张真实图。只是「4 条要点」不算。
    - sparse 变体要真的被选中：内容低于 `ideal` 时用 sparse，内容簇在内容带的上中部成团居中。
  - `grading/gates.py`：`layout_diversity` **恢复「≥ min(5, 页数) 种」的目标**。合格候选确实不够时可以降级，但 gate 行要写明「目标 5，实际合格家族 N，原因是……」，状态用 `warn`，**不许判 pass**。
- **验收 / 测试**：
  1. 单测：「两方各 1 条」或者「4 条要点但分不出两方」的页选不到 comparison；没有带来源数字的页选不到 big-number（第 6 轮的断言继续成立）。
  2. 单测：每个新版式的 sparse / normal / dense 三种变体各一例，`fill_rate` 通过、文字不溢出（`validate.py` 的字号下限照旧）。
  3. 单测：容量低于 `min` 的版式不出现在 `eligible_layouts_for_page()` 的结果里。
  4. smoke6 四题输入回放（compile-only）：`fill_rate`（R7-1 的新规则，fail 级）全过；`layout_diversity` 对 T36（5 页）、T12（7 页）都 ≥ 4 种且没有降级说明，否则写清原因；`no_fabricated_visuals`、`placeholders_marked`、`attribution_present`、`mapped_visuals_placed` 照旧全过。
  5. 汇报里列出每题 smoke5 / smoke6 / smoke7 三版版式序列对照。

#### R7-3：主题库 ×8 + 题目驱动选主题（原 R6-5）

- **目标**：解决用户最大的不满——四份 deck 风格一模一样。A 阶段的 `production.style` 要**真的**决定主题。
- **要改的文件**：
  - `theme/tokens.py` / 新建 `theme/library/*.json`：约 8 套主题（含现有 `academic-default`，取值不变）。每套写全 `ThemeTokens`（primary / background / card / stroke / text / graph 色序列 / 标题和正文字体），并带选择索引字段 `mood / tone / formality / density / scheme / best_for / avoid_for`。
  - 主题选择：新建 `theme/select.py`，输入 A 的 `production.style`、题目和受众，输出 `theme_id` + 理由，写进 `production_plan` 或 `page_design`（二选一，写进 README）。
  - `f_layout/stage.py:361`：不再固定用 `DEFAULT_THEME_ID`，改用选出来的 `theme_id`。
  - `doctor`：检查每套主题用到的字体在本机是否存在，缺字体时给出回退字体并提示。
- **抄什么**（先核对 LICENSE 再抄）：
  - 索引格式：[zarazhangrui/frontend-slides](https://github.com/zarazhangrui/frontend-slides)（MIT）的 `bold-template-pack/selection-index.json`。
  - 风格：[hugohe3/ppt-master](https://github.com/hugohe3/ppt-master)（MIT）的 `references/visual-styles/`；配色结构：presenton（Apache-2.0）的 `theme_data.py`；SlideSpeak（MIT）如有可用的配色 / 风格定义也可以参考。
  - 对比度一律过第 6 轮移植的 `contrast_ratio()` / `_repair_text_color()`：正文 ≥ 4.5，caption ≥ 4.5。
- **选择规则**：
  - agent / LLM 后端按题目选 `theme_id` 并给一句理由；后端不可用时用关键词规则兜底（兜底也要写理由）。
  - **agent 后端的答案仍然只能由宝特写。** 如果 A 的 prompt 或输出结构因此变化、旧的 `A_response_*.json` 对不上，照第 5 轮规则停下、提交最新 `*_request_*.json` 等宝特补答案，不要自己写。
- **验收 / 测试**：
  1. smoke 四题（T36 人工智能、T12 咖啡店创业、T26 世界建筑、T02 城市热岛）选出 **至少 3 套不同主题**，汇报里每题写选择理由。
  2. 每套主题都有快照测试；`academic-default` 的快照与第 6 轮 `r6_theme_snapshot` 一致（除了 R6-L 已有的 caption 颜色 / 字号改动）。其他主题新建快照，**快照更新单独提交**并说明原因。
  3. `rg '"#[0-9A-Fa-f]{6}"' src/pptv01/f_layout src/pptv01/g_build` 仍然零命中（色值只能在 `theme/` 里）。
  4. 每套主题所有文字 / 背景组合的对比度单测都 ≥ 4.5。
- **不做**：不写 SlideMaster 主题色；不 AI 出图、不画插画、不用图片背景（SDXL 背景继续延后）。

#### R7-4：清理第 6 轮遗留

1. **RapidOCR 测试加 skip**：`tests/test_r6_4_pixel_rules.py:329-342` 标 `@pytest.mark.vision_model`，或者没装 `rapidocr_onnxruntime` 时 `pytest.importorskip` 并写明原因。Linux 默认环境（没装 `[vision]`）全量 `pytest` 必须退出码 0。
2. **没有 OCR 时门槛报 degraded，不报 pass**：`grading/gates.py:747-749、788-792`。`_get_gate_ocr_runner()` 返回 `None` 时，`no_text_only_visuals` 状态写 `warn`，detail 写 `pixel_rules: degraded (rapidocr not installed)` 和没核验的图片数，**不许写「checked cleanly」**。加测试：模拟 RapidOCR 不可用，断言状态不是 `pass`。
3. **提交 `pytest_all.txt`**：Smoke 7 的 Windows 全量 `pytest` 输出提交到 `v01/smoke/review/smoke7/pytest_all.txt`，`SUMMARY.md` 第一段写 `PYTEST_ALL_EXIT=<n>` 和它的最后一行摘要。
4. **T02 的 E 答案已由宝特补好**：`v01/smoke/agent-answers/T02/io/E_response_2.json` 已按 smoke6 的 `E_request_2.json` 重写（可用素材池 A001 贝尔谢巴夜间地表温度图、A002/A004/A006/A008 文献、A011 WIPO 屋顶绿化、A012 天津桥园公园；全部 accepted，`unused_assets` 为空）。Smoke 7 照第 5 轮步骤从 `feature/v01-pipeline` 取 `v01/smoke`（`git checkout origin/feature/v01-pipeline -- v01/smoke`）即可续跑 T02。**如果 Smoke 7 的 D 取到的素材和这份答案又对不上，照旧停下、提交最新 `*_request_*.json`，不要自己写答案。**
5. 顺手：`pixel_rules.py:13-32` 的标定数值用当前依赖版本重测并更新（写明 rapidocr / fastembed 版本和平台）；`ruff --select F401 src` 报出的 13 处无用导入清掉（R7-2 要用上的 capacity 导入除外）。

#### Smoke 7：你自己跑（同样四题，同样流程）

- 前提：R7-1 到 R7-4 都已合入你的分支；Windows 全量 `pytest` 退出码 0。
- 流程**完全照第 5 轮 R5-2**：
  - 每行一条命令；每题用全新的 `runs\smoke7\<id>`；
  - `python -u` + `2>&1 | Tee-Object`；
  - 超过 5 分钟没有新日志就判 `HUNG`；
  - 把 R5-2 命令里的 `smoke5` 全部换成 `smoke7`；冒烟前先 `git checkout origin/feature/v01-pipeline -- v01/smoke` 拿到最新答案。
- 四题和提示词同 R5-2 的表格（T36 / T12 / T26 / T02）。**T02 这次必须跑完到 G / H。**
- **答案文件规则不变：你不得编写、修改或「补」任何 `*_response_*.json`。** 退出码 3，或 E / A 因答案对不上失败时，立刻停下这一题，提交最新的 `*_request_*.json` 和日志，等宝特补答案。
- **提交位置**：`feature/v01-office-renderer`（或你的新分支）的 `v01/smoke/review/smoke7/<id>/`。每题的文件同 R5-2，并额外提交：
  - `contact_sheet.png`：本题全部页的缩略图拼图；
  - `before_after.png`：同题 **smoke6 和 smoke7** 逐页并排（T02 用 smoke5 对 smoke7，因为 smoke6 没有渲染）；
  - `fill_rate`、`layout_diversity`、`no_text_only_visuals` 三个门槛的原文行；
  - 选出的 `theme_id` 和理由。
- **`v01/smoke/review/smoke7/SUMMARY.md`**：
  - 第一段：Windows 全量 `PYTEST_ALL_EXIT=<n>` 和 `pytest_all.txt` 的最后一行摘要（文件一起提交）；
  - 每题的 `RUN_EXIT` / `GATES_EXIT`、各阶段耗时（D 单列）；
  - smoke5 / smoke6 / smoke7 三版版式序列；
  - 每题的 `theme_id` 和选择理由，四题共用了几套主题；
  - `fill_rate` 报出的页和豁免的页（豁免要写角色）；
  - `pixel_rules` 是否降级；
  - 你看图后每题一句话的观感（**特别说明：有没有还剩半页空白的页，四份 deck 看起来是不是不同风格**）。

#### 第 7 轮验收（全部满足才算过）

1. Windows 清环境全量 `pytest` 退出码 0；Linux 默认环境（没装 `[vision]`）全量 `pytest` 退出码 0、不出网。
2. R7-1：smoke6 fixture 上修前**恰好**报出 T36 S003/S005、T12 S002/S005/S007；修后这 5 页非豁免且通过；`fill_rate` 是 `fail` 级。
3. R7-2：三个新版式各有 sparse / normal / dense；容量契约在 `layouts.py` 里被实际调用；comparison 要「两方各 ≥ 2 条」；`layout_diversity` 不再悄悄降阈值。
4. R7-3：约 8 套主题，四题至少 3 套不同主题且有理由；快照测试更新；对比度单测全过；`THIRD_PARTY_NOTICES.md` 覆盖新抄的来源，没有 GPL / AGPL / 无协议的内容。
5. R7-4 五条都已落实；没有 OCR 时门槛报 degraded。
6. Smoke 7 四题（含 T02）都跑完，`RUN_EXIT=0`、`GATES_EXIT=0`；结果和 `SUMMARY.md`、`pytest_all.txt` 已推送；没有任何编造，没有自写的答案文件。

#### 已排好但**本轮不要做**（第 8 轮及以后）

- **R6-6 B 阶段加页面角色、文字结构、节奏、结论句标题和真正的讲者备注**（见第 6 轮原文）。R7-1 的「页面角色」本轮先由 F 按内容推断，第 8 轮再由 B 直接给出。
- **R6-7 剩下的三个版式**：steps-horizontal、picture-caption、chart-insight。
- **R6-8 AI 腔 lint**、**R6-9 跨题雷同度**（见第 6 轮原文）。
- 继续延后：Bivia 判断器、Keenable 检索（只做 opt-in）、N7（python-pptx 退出生成路径）、SDXL 背景、美学分模型。

### 第 6 轮（2026-11-21 美东，宝特）：第 5 轮已通过；先修「看得见的错」：主题 token、半页空白、硬塞版式、错图

**第 5 轮评定：A−，通过。** 对象是 `feature/v01-office-renderer`，三个提交 `adda069`（D 超时 / 预算 / 进度 / 代理 doctor）、`9499853`（R5-3 修复）、`3a833ea`（Smoke 5 真机评审包）。宝特在 Linux 清环境复核全量 `pytest`：退出码 0，**537 passed，6 skipped**（`windows_com`）。Smoke 5 四题（T36 / T12 / T26 / T02）全部 `RUN_EXIT=0`、`GATES_EXIT=0`：没有挂死，没有自写答案，D 有总预算和逐条进度。R5-3 的六条都已落实，黄色通栏改成了右下角标。

**用户（qing）的产品评价（本轮的方向依据）：**
- 四份 deck **风格完全一样**，题目对配色、字体、母题、气质没有任何影响。
- 实际上**只有一套版型**。
- **文字高度模板化**，看不出特色。
- **T02（城市热岛）选了一张写满字的黄色告示牌**（Commons `File:Sponge_City.png`）当配图，没有信息量，门槛也没拦住。
- 作为初版可以接受。方针是「GitHub 上能抄的就抄，多看几个方案再融合」。

宝特对照代码找到的根因（文件:行号以 `3a833ea` 为准）：
- **主题链断在 A 之后。** A 产出的 `production.style` 只有 C 的 prompt 读（`c_asset_requirements/stage.py:106`）。配色和字体写死在 `f_layout/layouts.py:86-103`（`C_NAVY_DEEP` … `DEFAULT_FONT="Microsoft YaHei"`），`g_build/compile_ops.py:359-362、491-494、519-522、722-725` 还有写死的回退色，`layouts.py:960、1026、1267` 也有散落的 `#94A3B8`、`#CBD5E1`。
- **半页空白来自多样性那一遍硬换版式。** `layouts.py:256` `select_deck_layouts()` 的第 2 遍和回退（`:298-345`）会把内容并不是「对比」或「数字」的页硬换成 `comparison` / `big-number`。这两个版式只把内容画在上半页，QA 又没有空白检测。
- **配图过滤只看元数据。** `d_retrieve/image_rules.py` 只看标题、分类、国家，不看像素，所以「图里大半是字」的图片拦不住。

完整调研见宝特的 `/workspace/research/ppt_theme_layout_survey.md`（不在仓库里；下面各条已经写清要抄的文件）。

> **基线：** 从 `feature/v01-office-renderer`（头 `3a833ea`）继续做，或从它拉新分支；只推你自己的分支，**不要往 `feature/v01-pipeline` 推代码**。第 2 节的规则全部照旧。
> **顺序：** R6-1 → R6-2 → R6-3 → R6-4 → R6-L（遗留）→ Smoke 6。R6-1 到 R6-4 每条单独提交，提交说明以 `r6-N:` 开头。

#### 本轮通用规则（重申）

1. **渲染只用 PowerPoint COM；python-pptx 只能只读核验**，不进生成路径。
2. **不编造**：不得用 mock 或假数据冒充模型输出，不得编数字、图片或请求号。缺的就诚实占位。主题和母题只用几何形状和颜色，**不 AI 出图、不画插画**。
3. **协议**：只抄 MIT / Apache-2.0 / BSD。**禁止**抄 GPL / AGPL / 无协议的代码或数据：banana-slides（AGPL）、veasion/AiPPT（GPL）、SmartSchoolAI/ai-to-pptx（GPL）、sligter/LandPPT（协议不明）、anthropics/skills（无协议）。凡是抄过或移植过的代码，都在仓库根目录新建或追加 **`THIRD_PARTY_NOTICES.md`**：项目名、链接、协议全文或 NOTICE、抄了哪个文件、改了什么。移植文件的文件头也要写一行来源。
4. **推送前**：清掉 `PPTV01_*` 环境变量跑全量 `pytest`，汇报真实退出码和最后一行摘要。Windows 用第 5 轮验收第 1 条的原样命令。
5. 只推你自己的分支；不碰 master/main；不 force push；不开 PR。

#### R6-1：主题 token 抽离（只有一套主题，渲染结果必须不变）

- **目标**：把写死的颜色和字体收进一个 `ThemeTokens`，为第 7 轮的主题库铺路。**本轮只有一套 `academic-default`，取值和现在一模一样**，渲染出来逐元素不变。
- **要改的文件**：
  - 新建 `src/pptv01/theme/__init__.py`、`theme/tokens.py`。
  - `f_layout/layouts.py:86-103`（全部 `C_*` 和 `DEFAULT_FONT`）、`:470-480`（占位的深色 / 浅色配色）、`:561`、`:677`、`:744`（caption / citation 颜色参数）、`:960`、`:967`、`:1026`、`:1267` 的写死色值。
  - `g_build/compile_ops.py:359-362、491-494、519-522、722-725` 的 `or "#…"` 回退。
  - token 要从 F 一路传到 G（经 `page_design` 或运行配置均可，二选一并写进 README）。
- **抄什么**：[presenton/presenton](https://github.com/presenton/presenton)（Apache-2.0）。
  - 数据结构参考 `servers/fastapi/models/theme_data.py`（`primary/background/card/stroke/background_text/primary_text/graph_0..9`）。字段名可以照我们的用法调整，但要保留 graph 色序列。
  - 移植 `servers/fastapi/templates/v2/theme.py` 里的 `contrast_ratio()`、`_relative_luminance()`、`_repair_text_color()`、`_complete_graph_colors()`、`_surface_variant()` 五个纯函数。
  - 按 Apache-2.0 在 `THIRD_PARTY_NOTICES.md` 里保留 NOTICE，并注明改动。
- **验收 / 测试**：
  1. **快照测试**：对 `v01/smoke/review/smoke5/{T36,T12,T26,T02}` 用 `--renderer compile-only` 回放，或者用现有 fixture，重新生成 `page_design.json` / `deck_ops.json`。所有元素的 `color_hex`、`font`、`font_size_pt` 和坐标都必须和改动前**完全一致**。改动前的快照提交到 `tests/fixtures/r6_theme_snapshot/`。
  2. `rg -n '"#[0-9A-Fa-f]{6}"' src/pptv01/f_layout src/pptv01/g_build` 只允许命中 `theme/tokens.py` 和一个显式白名单（白名单写进测试）。
  3. 对比度函数有单测：黑白比约等于 21；`_repair_text_color` 在对比度低于 4.5 时换色。
- **不做**：不加第二套主题，不做题目→主题选择，不改版式几何，不写 SlideMaster 主题色。

#### R6-2：填充率 / 空白门槛（移植 SlideSpeak 的 `occupancy.ts`）

- **目标**：新增门槛 `fill_rate`，能抓出「内容只占上半页、下半页大片空白」的页面。
- **要改的文件**：新建 `src/pptv01/h_qa/occupancy.py`（纯函数）；在 `grading/gates.py` 注册门槛；`h_qa/stage.py` / `h_qa/inspect.py` 把结果写进 `qa_report.json`；Windows 上可选再用 `renderers/powerpoint_com/measure.py` 的实测文字框复核。
- **抄什么**：[SlideSpeak/slide-design-skill](https://github.com/SlideSpeak/slide-design-skill)（MIT）的 `engine/occupancy.ts`。
  - `scoreOccupancy`：内容区里最大的**竖向空白带**。原值是 `VOID_MAX_PX = 230`（1080 高，约等于幻灯片高的 21%，换算成我们 7.5 英寸画布约 1.6 英寸）。
  - `scoreCellOccupancy`：卡片内部空洞超过 `max(CELL_VOID_MAX_PX=180, 0.45×高)`，或文字覆盖率低于 `SPARSE_TEXT_COVERAGE=0.035`，都算空卡片。
  - 背景说明见同仓库 `docs/specs/2026-06-04-layout-fit-model.md`（underflow / overflow；绝不靠缩字号）。
  - 移植文件头写来源，`THIRD_PARTY_NOTICES.md` 写 MIT 全文和版权行 `Copyright (c) 2026 SlideSpeak`。
- **规则**：
  - 输入用 `page_design.json` 的元素矩形（F 阶段就能算）。
  - **排除**整页背景（宽 ≥ 画布 95% 且高 ≥ 画布 85%）、纯装饰 `bg_*` / `accent_*` 细线、页脚、占位角标。
  - 内容带取标题栏下沿到页脚上沿。
  - `cover` / `section` / `quote` 这类 anchor 或 breathing 页**豁免**，但要在 detail 里写出豁免原因。
  - 阈值放进常量并写注释，默认用「最大空白带 ≥ 1.6 英寸」。
- **宝特的原型数据**（用上面的规则粗算 smoke5，内容带 1.2–7.0 英寸）：
  - 目标页最大空白带：T36 p3 big-number 2.77、p4 comparison 2.42；T12 p4 comparison 3.02、p5 big-number 2.77；T02 p2 comparison 2.42、p5 big-number 2.77。
  - 其他非豁免页 ≤ 1.23。
  - section 页（T12 p6、T02 p4、T26 p4）约 2.3–2.8，必须走豁免。
- **验收 / 测试**：
  1. 把 smoke5 的 `page_design.json`（T36 / T12 / T02 / T26）复制成测试 fixture（`tests/fixtures/smoke5_page_design/`），断言 `fill_rate` **恰好**报出 **T36 p3、p4；T12 p4、p5；T02 p2、p5**，其余页不报（豁免页报 exempt）。
  2. 再加卡片级用例：一个高卡片只有顶上一行字，必须报 `cell-void`。
  3. 本轮先以 **warn** 级别接进 gates（不挡 `GATES_EXIT`）；R6-3 合入、smoke6 通过后，再在第 7 轮改成 fail。detail 要写页号、空白带位置（top / middle / bottom）和英寸数。
- **不做**：不靠放大字号或拉伸文本框把空白填掉（SlideSpeak 原则：「空白是围绕内容簇的边距，不是被拉开的间隙」）；不做跨题对比（R6-9）。

#### R6-3：修多样性那一遍：绝不硬塞不匹配的版式

- **目标**：`layout_diversity` 只能在「内容合适的候选」之间调整，不能为了凑「不相邻、≥5 种」把内容形态不对的页硬换成 `comparison` / `big-number` / `timeline`。内容少的页用稀疏变体或降级家族。
- **要改的文件**：
  - `f_layout/layouts.py:176` `_score_layouts_for_page()`（加内容形态资格）、`:256` `select_deck_layouts()`（第 2 遍 `:298-326` 和回退 `:328-345` 只能从**合格**候选里换）、`:347-358`（第 3 遍同理）。
  - comparison / big-number / timeline 各自的构建函数（`build_layout_elements` 里对应分支）加 `sparse` 几何。
  - `f_layout/capacity.py` 写每个版式的槽位容量。
  - `grading/gates.py` 里 `layout_diversity` 的阈值：合格候选不足时，降级成按「合格家族数」计算，并在 detail 里写明。
- **抄什么**：
  - [presenton](https://github.com/presenton/presenton)（Apache-2.0）`servers/fastapi/utils/llm_calls/generate_presentation_structure.py` 的选版式规则：内容里没有图就不选带图版式；没有表就不选表格版式；相邻页尽量不同，但**允许按需重复**。改写成我们的确定性规则，不调 LLM。
  - [SlideSpeak](https://github.com/SlideSpeak/slide-design-skill)（MIT）`docs/specs/2026-06-04-layout-fit-model.md` 的「版式容量契约」：每个槽位 `{min, ideal, max}`，加家族和降级 / 续页策略。
  - [icip-cas/PPTAgent v0.2.0](https://github.com/icip-cas/PPTAgent/tree/v0.2.0)（MIT）`pptagent/roles/layout_selector.yaml` 的判据：元素个数、字数区间、图片相关度。
- **资格与降级规则（最少要有这些）**：

| 版式 | 合格条件 | 不合格时 |
|---|---|---|
| comparison | 两方，每边 ≥ 2 条要点 | 降级为 quote（结论句），或者 two-content 的 `sparse` 变体 |
| big-number | 有**带 `source_refs` 的数字**事实 | 降级为 quote / statement；**绝不编数字** |
| timeline | ≥ 3 个有序步骤或年份 | 2 步用 comparison-sparse 或 bullets；1 步用 quote |
| 任意版式 | 内容低于 `min` 容量 | 用该版式的 `sparse` 变体：内容簇在内容带的上中部居中成团，不拉伸到整页 |

- **验收 / 测试**：
  1. 用 smoke5 四题的输入回放（compile-only）：`fill_rate`（R6-2）**全部通过**（豁免页除外）；`layout_diversity`、`no_fabricated_visuals`、`placeholders_marked`、`attribution_present`、`mapped_visuals_placed` 照旧全过。
  2. 新单测：构造「两方各 1 条」的页，断言选不到 comparison；构造「没有带来源数字」的页，断言选不到 big-number。
  3. 汇报里列出每题新旧版式序列的对照。
- **不做**：本轮不新增版式（那是 R6-7）；不改 B 的 prompt（R6-6）。

#### R6-4：配图像素级过滤（CLIP 零样本负类 + OCR 文字面积占比）

- **目标**：拦住「告示牌、logo、文字截图、文档页、海报」这类以文字为主、没有信息量的图片，并用图文相似度给候选排序。**T02 的 `File:Sponge_City.png` 必须被拒，并写出原因。**
- **要改的文件**：
  - 新建 `src/pptv01/d_retrieve/pixel_rules.py`，在 `d_retrieve/stage.py` 中元数据规则之后、图片下载之后调用。拒绝写进 D 报告的 rejections：原因、负类标签、分数、OCR 占比。
  - `grading/gates.py` 新增复查门槛 `no_text_only_visuals`（warn 级）。
  - `pyproject.toml` 加可选依赖组 `[project.optional-dependencies] vision = ["rapidocr-onnxruntime"]`（或当前包名，以 PyPI 为准）。
- **抄什么**：
  - [qdrant/fastembed](https://github.com/qdrant/fastembed)（Apache-2.0，已经是依赖）的 `ImageEmbedding("Qdrant/clip-ViT-B-32-vision")` + `TextEmbedding("Qdrant/clip-ViT-B-32-text")`（模型 MIT，约 0.34 + 0.25 GB，CPU 可跑）。
    - 负类 prompt：`a sign with text`、`a logo`、`a screenshot of text`、`a document page`、`a poster with text`、`a map legend`。
    - 正类：asset requirement 的 purpose + 目标名（英文）。
    - 负类最高分比正类高出阈值就拒；正类分写进候选排序。
  - [RapidAI/RapidOCR](https://github.com/RapidAI/RapidOCR)（Apache-2.0）：算文字框面积 / 图片面积。`type=image`（照片）超过约 0.15 就拒；`diagram` / `chart` 放宽或豁免。扫描写法可以参考 [hugohe3/ppt-master](https://github.com/hugohe3/ppt-master)（MIT）的 `skills/ppt-master/scripts/image_ocr.py`。
  - 阈值写成常量并注明是用哪些样本标定的。
- **离线 / 失败降级**：
  - 模型下载或加载失败、超时、没装 rapidocr 时，**不得静默跳过**。D 报告和日志都要写 `pixel_rules: degraded (<原因>)`，`qa_report` 里要有这一行。
  - 降级后只按元数据规则继续，不挡运行。
  - 模型加载必须走第 5 轮的 fastembed 超时、缓存和代理路径，`flush=True` 打进度。
- **验收 / 测试**：
  1. 把 `Sponge_City.png` 作为测试图片 fixture 提交（来源 `https://commons.wikimedia.org/wiki/File:Sponge_City.png`，Jeb，CC BY 4.0，在 `THIRD_PARTY_NOTICES.md` 或 fixture 的 README 里写署名）。再选 2–3 张 smoke5 里**合格**的真实照片当正例。
  2. 断言：Sponge_City 被拒，原因里有负类标签或 OCR 占比；正例通过。
  3. 默认 `pytest` 不出网：需要模型的测试标 `@pytest.mark.vision_model`，没有缓存模型时 skip 并写原因。另外用**假的 embedder 和假的 OCR** 测「判定逻辑」和「降级路径会写日志」。
  4. Windows 实跑 T02 时，D 报告里能看到 Sponge_City 的拒绝记录。
- **不做**：美学分模型（improved-aesthetic-predictor，较重，延后）；不用 AI 生成替代图；不改 `inspect_image_with_vision()`（保留为兜底复核）。

#### R6-L：第 5 轮遗留（小项，一起做）

1. **代理说明文档**：在 `v01/README.md` 或 `docs/proxy.md` 写清：
   - httpx 的 `trust_env`（默认 True，读 `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY`）；
   - huggingface_hub / fastembed 下载是否走代理、怎么配；
   - `HF_ENDPOINT`（例如镜像站）是否支持，在哪里生效；
   - 缓存目录（`FASTEMBED_CACHE_PATH` / HF cache）。
   
   doctor 里已有的代理提示保持「只显示主机和端口」。
2. **去掉 pyalex**：`pyproject.toml:15` 删除 `pyalex>=0.18`（`adda069` 已经改成 httpx 的 `openalex_http_fetch`）。把 `d_retrieve/sources.py:5`、`:353-370`（`pyalex_fetch` 这个名字）、`d_retrieve/stage.py:6`、`:815`（`"OpenAlex (pyalex)"`）里的字样一起清掉。`pyalex_fetch` 如果只是别名就删掉或改名，测试同步更新。
3. **未使用的导入**：`tests/test_r5_1_d_timeouts_budget.py` 头部疑似未使用 `builtins`、`json`、`pytest`、`OpenAlexClient`、`openalex_http_fetch`（:5–28）。以 `ruff --select F401` 或 pyflakes 的结果为准删干净。
4. **OpenAlex 429 熔断**：Smoke 5 里 OpenAlex 每题都返回 429，重试白白耗掉 15–65 秒。
   - 改法：`d_retrieve/sources.py:315-349`（`openalex_http_fetch`），以及 `d_retrieve/stage.py:664` 的调用处。同一次运行里**第一次收到 429**（或 402 / 403 配额类）后，**本次运行剩下的 OpenAlex 请求全部跳过**，直接走 Crossref。
   - D 报告写 `openalex: circuit_open after 429 at <时间>`，以及省下的请求数和估算节省的秒数。
   - 不要因此缩短其他 provider 的预算。
   - 测试用 `httpx.MockTransport`：第一次返回 429，断言后面不再发起 OpenAlex 请求。
5. **引用 / 署名字号和颜色**：
   - `f_layout/validate.py:31` `ElementKind.caption: 9.0` 改为 **≥ 10.5**。
   - `layouts.py:569` 的起始 `9.5` 同步调整。
   - `_place_anchored_citation`（`layouts.py:736` 起）和 caption 的颜色从 `C_MUTED #64748B` / `#94A3B8` 改成**更深**的颜色：浅底上对比度 ≥ 4.5（例如 `#475569`）；深底页用对应的浅色，同样 ≥ 4.5。颜色走 R6-1 的 token，用 R6-1 的 `contrast_ratio()` 写单测。
   - 这一条**会改变渲染**，所以放在 R6-1 快照测试**之后**单独提交，并更新快照。
6. **冒烟汇总必须带 Windows 全量 pytest 退出码**：`smoke6/SUMMARY.md` 第一段写 `PYTEST_ALL_EXIT=<n>` 和 `pytest_all.txt` 的最后一行摘要。Smoke 5 的汇总里缺了这一项。

#### Smoke 6：你自己跑（同样四题，同样流程）

- 前提：R6-1 到 R6-4 和 R6-L 都已合入你的分支；Windows 全量 `pytest` 退出码 0。
- 流程**完全照第 5 轮 R5-2**：
  - 每行一条命令；每题用全新的 `runs\smoke6\<id>`；
  - `python -u` + `2>&1 | Tee-Object`；
  - 超过 5 分钟没有新日志就判 `HUNG`；
  - 把 R5-2 命令里的 `smoke5` 全部换成 `smoke6`。
- 四题和提示词同 R5-2 的表格（T36 / T12 / T26 / T02）。
- **答案文件规则不变：你不得编写、修改或「补」任何 `*_response_*.json`。** 退出码 3，或 E 因素材对不上失败时，立刻停下这一题，提交最新的 `*_request_*.json` 和日志，等宝特补答案。R6-4 拒掉 Sponge_City 之后，T02 的 E 很可能对不上旧答案，这是预期情况，照此处理。
- **提交位置**：`feature/v01-office-renderer`（或你的新分支）的 `v01/smoke/review/smoke6/<id>/`。每题的文件同 R5-2，并额外提交：
  - `contact_sheet.png`：本题全部页的缩略图拼图；
  - `before_after.png`：同题 smoke5 和 smoke6 逐页并排；
  - `fill_rate`、`no_text_only_visuals` 两个门槛的原文行。
- **`v01/smoke/review/smoke6/SUMMARY.md`**：
  - Windows 全量 pytest 退出码和摘要（R6-L 第 6 条）；
  - 每题的 `RUN_EXIT` / `GATES_EXIT`、各阶段耗时（D 单列，写 OpenAlex 熔断节省的秒数）；
  - 新旧版式序列；
  - `fill_rate` 报出的页；
  - Sponge_City 的拒绝记录；
  - `pixel_rules` 是否降级；
  - 你看图后每题一句话的观感。

#### 第 6 轮验收（全部满足才算过）

1. Windows 清环境全量 `pytest` 退出码 0（第 5 轮验收第 1 条的原样命令），Linux 上默认 `pytest` 不出网、退出码 0。
2. R6-1 快照测试证明渲染不变，写死的色值只在白名单里。
3. R6-2 在 smoke5 fixture 上**恰好**报出 T36 p3/p4、T12 p4/p5、T02 p2/p5。
4. R6-3 合入后，smoke5 回放和 Smoke 6 真机的 `fill_rate` 全部通过（豁免页除外），其他门槛照旧全过。
5. R6-4：Sponge_City 被拒且有原因；离线时有 `degraded` 记录，不是静默跳过。
6. R6-L 六条都已落实；`THIRD_PARTY_NOTICES.md` 已覆盖所有移植来源（presenton、SlideSpeak、PPTAgent 如有引用、fastembed、RapidOCR、ppt-master 如有引用）。
7. Smoke 6 四题结果和 `SUMMARY.md` 已推送；没有任何编造，没有自写的答案文件。

#### 已排好但**本轮不要做**（第 7 轮及以后的路线图，先知道方向）

- **R6-5 主题库 ×8 + 「设计读取」**：
  - 每套主题一份 JSON 的 token，索引格式抄 [zarazhangrui/frontend-slides](https://github.com/zarazhangrui/frontend-slides)（MIT）的 `bold-template-pack/selection-index.json`（mood / tone / formality / density / scheme / best_for / avoid_for）。
  - 风格和论证模式分开选，参考 [hugohe3/ppt-master](https://github.com/hugohe3/ppt-master)（MIT）的 `references/visual-styles/`、`references/modes/`。
  - 由 LLM 按题目选 `theme_id` 并给理由，选不出来时用关键词兜底；doctor 检查字体是否存在。
- **R6-6 B 阶段加页面角色、文字结构、节奏、结论句标题，加真正的讲者备注**：
  - 页面角色参考 ppt-master 的 `templates/styles/academic-research/templates/design_spec.md`；结论句标题和 ghost-deck 测试参考 [Gabberflast/academic-pptx-skill](https://github.com/Gabberflast/academic-pptx-skill)（MIT）。
  - 文字结构词表参考 [allweonedev/presentation-ai](https://github.com/allweonedev/presentation-ai)（MIT）的 `src/lib/presentation/layout-catalog.ts`。
  - `set_notes` 写讲稿，不再写 planning summary。
- **R6-7 版式扩充 +6**：three-card、two-content、steps-horizontal、hero-statement、picture-caption、chart-insight。每个带容量和 sparse / normal / dense 三种变体，参考 ppt-master 的 `templates/layouts/layouts_index.json`（presentation_core）和 presenton 的版式描述。
- **R6-8 AI 腔 lint**：参考 SlideSpeak 的 `engine/quality-lint.ts`，补中文空话词表，先只告警。
- **R6-9 跨题雷同度**：参考 SlideSpeak 的 `engine/divergence.ts`（版式集合用 Jaccard，序列用 LCS），写进冒烟汇总。
- 继续延后：**Bivia 判断器**（第 4 轮「可选」条款仍然有效）、**Keenable 检索**（等版式几轮都过了再评估，只做 opt-in）、N7（python-pptx 退出生成路径）、SDXL 背景。

### 第 5 轮（2026-11-21 美东，宝特）：第 4 轮已通过；先治 D 挂死，再由你自己跑 Smoke 5，并修第 4 轮评审遗留

**第 4 轮已通过（对象 `feature/v01-office-renderer` 头 `57765f5`）。** 用户在 Windows 真机清环境跑全量 `pytest`：**退出码 0，529 passed，1 skipped（soffice）**；N2 验收（全量、单跑 `test_n2_watchdog_timeout_never_kills_preexisting_user_powerpoint`、先跑 `windows_com` 再跑该用例）都已完成。宝特 Linux 清环境复核：退出码 0，524 passed，6 skipped（`windows_com`）。版式多样性：Linux 上用 `--renderer compile-only` / `legacy` 回放 T12/T26/T36，`layout_diversity`、`no_fabricated_visuals`、`placeholders_marked`、`attribution_present` 全过（T12 七页 7 种版式，T26 八页 6 种，T36 五页 5 种；单条占位条 6.98 平方英寸，约占内容区 9.7%）。可选的 Bivia 判断器未做，按约定不挡验收。

**Smoke 5 在真机上挂死（这是本轮第一优先级）：** 用户在本机跑 T36（约当地 16:31 开始）。运行目录里只写到 C：`A/B/C_request_*.json`、`production_plan.json`、`slide_plan.json`、`asset_requirements.json`、`llm_trace`；之后再无任何文件，**没有拉起 POWERPNT.EXE**。`T36.log` 全程是**空的**——运行用 `> file 2>&1` 重定向，Python 的 stdout 是块缓冲，挂住时一行都没刷出来。所以卡在 **D（素材获取 / 联网）**。第二次重跑同样挂死（无更多细节）。用户在国内网络，可能经过代理。**用户已经无法再手动跑冒烟：从本轮起，Smoke 由你（Antigravity）在用户的 Windows 机器上自己跑、自己收集结果、自己推送。**

宝特读代码看到的嫌疑点（请先核实再改，不要只改这几处）：

- `src/pptv01/d_retrieve/sources.py:258-275` `pyalex_fetch`：pyalex 0.21 内部用 `requests` 的 `session.get(...)`，**没有传 `timeout`**，网络半通（握手成功但不回包、代理吊住）时会**永久阻塞**；`max_retries = 1` 管不住这种情况。
- `src/pptv01/retrieval/relevance.py:121` `TextEmbedding(model)`：fastembed 首次使用会从 Hugging Face 下载模型，国内直连常卡住，且没有超时；需要确认缓存目录、超时和失败降级（降级为 structural-only 并在报告里写明，见该模块已有的「无打分器」路径）。
- `src/pptv01/retrieval/backends.py:53-55` `make_http`：httpx 只有一个总的 `timeout=30`（`PPTV01_ASSETS_TIMEOUT_S`），没有分开的 connect/read；`:62-86` 有 2 次重试，429 时按 `Retry-After` 最多睡 10 秒。单次请求有上限，但**整个 D 没有总时限**：素材多、每个都超时重试时，累计可以到几十分钟，看起来就是「挂死」。
- 整个运行**没有逐素材的进度输出**，挂在哪个 URL 完全看不出来。

#### R5-1（阻塞项，先做）：D 阶段绝不允许挂死

1. **每一个联网调用都要显式超时**：httpx 用 `httpx.Timeout(connect=…, read=…, write=…, pool=…)`（建议 connect 10s、read 20s，可用环境变量配置）；pyalex/requests 必须带 `timeout=(connect, read)`——pyalex 本身不支持就自己包一层（如给它的 `requests.Session` 挂默认超时的 adapter，或改用已有的 httpx 客户端直接调 OpenAlex REST）。fastembed 模型下载同样要有超时，或在 D 开始前探测缓存、没有缓存就降级。
2. **有界重试**：每个请求最多 N 次（保留现有 `RETRIES = 2` 的量级），退避总和有上限；`Retry-After` 继续封顶。
3. **D 阶段总时间预算**：新增可配置的总预算（建议默认 180s，环境变量如 `PPTV01_ASSETS_BUDGET_S`）。预算用完后，**剩下的需求一律按「未获取」处理**：照常进 `user_input_requests.json` / 占位，状态写清 `not acquired: D budget exhausted`。**绝不允许**用猜测、默认图、同名图或任何自绘内容去填。
4. **超时要可诊断**：每次超时 / 放弃都在 D 报告和日志里写出 **provider（Wikidata / Commons / OpenAlex / Crossref / fastembed）、URL（去掉密钥类参数）、耗时、重试次数**。
5. **进度日志**：每个素材需求开始和结束各打一行（需求 id、provider、结果、耗时），并 `flush=True`（或统一 `sys.stdout.reconfigure(line_buffering=True)`）；CLI 入口也要保证 `> file` 重定向时日志是实时的。冒烟命令另外用 `python -u`（见 R5-2）。
6. **代理**：在汇报里写清 `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` 是怎么生效的——httpx 的 `trust_env`（默认 True）、requests 的环境变量代理、fastembed / huggingface_hub 下载走不走代理、`HF_ENDPOINT` 是否支持。doctor 里加一条**非阻塞**提示：显示检测到的代理变量（只显示主机和端口，不显示账号密码）。
7. **测试（Linux 可跑，默认 `pytest` 不准出网）**：用假的慢 / 挂住的 fetcher（httpx `MockTransport` 里 `sleep`、或抛 `httpx.ReadTimeout`；假的 OpenAlex fetch 永久阻塞）证明：① 单个请求按超时返回；② 总预算到点后 D 在预算加少量余量之内结束；③ 超时的需求变成诚实的占位 / 「未获取」，报告里有 provider 和 URL；④ 后续阶段照常进行，`overall` 是 `complete_with_placeholders` 而不是挂住。测试里的「挂住」必须用可中断的方式实现（线程 + 事件或短 sleep），不要真的让测试进程卡死。

**验收：** 任何阶段都不允许超过 5 分钟没有输出；D 在网络不通 / 代理吊住的情况下也能在预算内结束并给出诚实占位。

#### R5-2：你自己跑 Smoke 5（T36 → T12 → T26 → T02）

> 前提：R5-1 已合入你的分支，且 Windows 全量 `pytest` 退出码 0。冒烟一律用你分支的最新提交。
> 环境：用户机器，仓库 `F:\ggagy\ai-acadmic-ppt-sys\v01`，PowerShell 7，Office 2021 Home & Student（16.0.20625.20108）。
> **每行一条命令，不要把多条命令拼成一行，也不要写跨行的 `foreach`**（长段粘贴在用户的 pwsh 里会被打乱）。
> 每题必须用**全新的** `runs\smoke5\<id>` 目录；**不要**复制 `runs\smoke4\<id>`（`--resume` 只比对输入哈希，会复用旧的 `page_design.json`）。

**0. 准备与检查单（同第 5B 节「冒烟前检查单」）**
```powershell
cd F:\ggagy\ai-acadmic-ppt-sys
git fetch --all
git checkout feature/v01-office-renderer
git pull
git log --oneline -1
git checkout origin/feature/v01-pipeline -- v01/smoke
cd v01
Get-Process POWERPNT,EXCEL -ErrorAction SilentlyContinue
```
关掉所有 PowerPoint 窗口，结束残留的 `POWERPNT.EXE`（无法结束的、不属于本次的 Excel 进程不用管，记下 PID 即可）。然后：
```powershell
Get-ChildItem Env:PPTV01_* -ErrorAction SilentlyContinue | Remove-Item
.\.venv\Scripts\python.exe -m pptv01.cli doctor
.\.venv\Scripts\python.exe -m pytest -o addopts="" -rs -p no:cacheprovider > pytest_all.txt 2>&1
"PYTEST_ALL_EXIT=$LASTEXITCODE"
Get-Content pytest_all.txt -Tail 3
$env:PPTV01_LLM_BACKEND = "agent"
$env:PYTHONUNBUFFERED = "1"
```

**1. 每题一组（以 T36 为例；T12/T26/T02 把所有路径里的 `T36` 换成对应 id，提示词换成下表）**
```powershell
New-Item -ItemType Directory -Force runs\smoke5\T36,runs\smoke5\io_T36 | Out-Null
Copy-Item smoke\agent-answers\T36\io\*.json runs\smoke5\io_T36\
Copy-Item smoke\agent-answers\T36\research_brief.json runs\smoke5\T36\
$env:PPTV01_AGENT_RESPONSES_DIR = "runs\smoke5\io_T36"
.\.venv\Scripts\python.exe -u -m pptv01.cli run "做一份不超过5页的人工智能介绍，尽量少用文字。" --out runs\smoke5 --run-id T36 --resume --renderer powerpoint-com --on-missing placeholder 2>&1 | Tee-Object -FilePath runs\smoke5\T36.log
"RUN_EXIT_T36=$LASTEXITCODE"
.\.venv\Scripts\python.exe -u -m pptv01.cli gates runs\smoke5\T36 2>&1 | Tee-Object -FilePath runs\smoke5\T36.gates.log
"GATES_EXIT_T36=$LASTEXITCODE"
```

| id | 提示词（原样） |
| --- | --- |
| T36 | 做一份不超过5页的人工智能介绍，尽量少用文字。 |
| T12 | 帮我准备一个咖啡店创业计划展示。 |
| T26 | 做一份介绍世界著名建筑的PPT。 |
| T02 | 帮我介绍一下城市热岛效应。 |

- **挂死规则：** 任何一题超过 5 分钟没有新日志行，就 Ctrl+C，按第 5B 节「挂了怎么恢复」只清本次进程，把日志最后 50 行和运行目录里最新的文件名单记进报告，标为 `HUNG`，然后回 R5-1 修，**不要跳过继续交差**。
- **退出码 3，或 E 因为 D 取到的素材和已提交的答案对不上而失败（例如「usable assets neither accepted on any page nor listed in unused_assets」）：立刻停下这一题。** 不得自己编写、修改或「补」任何 `*_response_*.json`——agent 后端的答案只能由宝特写。把 `runs\smoke5\io_<id>\` 里**最新的** `*_request_*.json` 和日志一起提交（见下），在 summary 里写明，等宝特补答案后再续跑。
- 期望：`RUN_EXIT=0`（`overall=complete_with_placeholders`）、`GATES_EXIT=0`，`render\slide_*.png` 由 H 自动导出。

**2. 结果提交（推到 `feature/v01-office-renderer`，目录 `v01/smoke/review/smoke5/<id>/`）**

每题提交：
- `render/slide_*.png`（全部；或拼一张 contact sheet 加上原图）；
- `gate_report.json`、`qa_report.json`、`page_design.json`；
- `run.log.tail.txt`（`runs\smoke5\<id>.log` 最后 200 行）和 `gates.log`；
- `deck.pptx`：≤ 10MB 就提交，超过就不提交并在 summary 里写大小；
- 若停在 3 / E 失败：最新的 `*_request_*.json`；
- `summary.md`：`RUN_EXIT`、`GATES_EXIT`、各阶段耗时（特别是 D）、D 报告里超时 / 未获取的条目（provider + URL）、`layout_diversity` 那一行原文、你看图后的一句话观感，以及 smoke4 同题对照（`runs\smoke4\<id>\render`，有就并排列出页号）。

`runs/` 被 gitignore，复制到 `v01/smoke/review/smoke5/<id>/` 再提交。**不要**提交 `io_*` 里的答案文件、任何密钥或代理账号。

#### R5-3：修第 4 轮评审遗留（文件:行号以 `57765f5` 为准）

1. **（中）Excel 误杀**：`renderers/powerpoint_com/session.py:295-311`（`record_spawned_excel_pids`）以及 `:607`、`:621-625`（`__exit__`）。现在「我们拉起的 Excel」是用「当前 PID − 基线」算的，并在 `_collect_spawned_excel_pids` 里写进 `_spawned_excel_pids`；用户运行期间自己打开的 Excel 会被算进去，正常退出时等 2 秒后被 `taskkill`。改为：只在 `executor.py:666`（`add_chart` 之后）那一刻记录新 PID，并且只记录能和本次图表操作对上的那个 Excel（例如对比 add_chart 前后的差，而不是和会话开始时的基线比）；`_on_timeout` 和 `__exit__` **只杀已记录的集合**，不再重新求差。加测试：会话中途出现一个与图表无关的新 Excel PID，退出时不被终止。
2. **（低）`probe_excel_com` 留下 Excel**：`session.py:177-211`。基线枚举失败时 `spawned` 为空，于是既不 `Quit()` 也不清理，DispatchEx 拉起的 Excel 一直挂着。改为：`DispatchEx` 得到的实例总是 `Quit()`；只有「确实记录到的 PID」才允许在 `Quit` 失败后终止。
3. **（中）回退选版时静默丢掉第 2 张及以后的真实图**：`f_layout/layouts.py:328-342`（Pass 2 的回退）会把 `TEXT_CAPABLE_LAYOUTS` 里的版式派给有 2 张以上真实图的页，而 `cover`（`:811-877`）、`section`（`:1023-1029`）、`quote`（`:1330-1334`）、`big-number`（`:1494-1495`）只放 `real_visuals[0]`。宝特复现：5 页、每页 2 张图，结果 `['cover','section','image-left','image-right','comparison']`，cover 和 section 页各只放了 1 张，没有任何门槛或备注报出来。改为：选版时只给「能放下该页全部真实图」的版式，或者让这些版式能放下多张；并新增门槛（可并入 `layout_diversity`，或新开 `mapped_visuals_placed`）：E 里 `accepted` 的每一个视觉素材（图片 / 图表 / 表格 / 图示，不含只作为文献引用的 reference）都必须出现在该页 `page_design` 里，否则判 fail。
4. **（低）编造的请求号**：`layouts.py:421` `req_id = item.request_id or "U001"`；没有 `request_id` 时元素名还会变成 `PLACEHOLDER_None`（`:477`、`:512`）。没有请求号的占位是上游 bug：应报错或用明确的 `U???`/`unassigned` 标记并让 `placeholders_marked` 报出，**不得编号冒充**。
5. **（低）门槛静默跳过 / 吞异常**：`grading/gates.py:584`（`deck_ops` 页数和 `page_design` 不一致时直接跳过比对）、`:590`（`except Exception: pass`）。改为：页数不一致本身判 fail；读 / 解析 `deck_ops.json` 失败写进 `detail`（至少降为 warn），不得吞掉。`:533` 的「最大内容槽」目前把标题和正文文本框也算进去，请改成只比主视觉 / 主内容槽，或在 `detail` 写清比的是什么。
6. **（外观）** `tests/test_round4_layout_diversity.py:11-12` 未使用的 `Presentation` / `Inches` 导入删掉。另外 T36 五页每页都有一条 12.03×0.58 英寸的通栏黄色虚线条：面积合规，但请在 Smoke 5 的真机截图里确认它是否仍然抢眼；如果是，改成角标、窄条，或汇总到 `needs-input-list` 末页（HANDOFF 第 4 轮允许的做法），并在 summary 里贴前后对比。

#### R5-4：Bivia 判断器继续可选、延后

不做也不挡本轮验收；要做仍须完全遵守第 4 轮「可选：Bivia 判断器」的五条（默认关闭，只读 `A8_API_KEY` 环境变量，不得进代码 / 日志 / 提交，单测用假客户端、不出网）。

#### 第 5 轮验收（全部满足才算过）

1. Windows 清环境全量 `pytest` 退出码 0，命令原样：`.\.venv\Scripts\python.exe -m pytest -o addopts="" -rs -p no:cacheprovider > pytest_all.txt 2>&1`，然后 `"PYTEST_ALL_EXIT=$LASTEXITCODE"`；把最后一行摘要写进汇报。
2. 任何阶段都没有超过 5 分钟没有输出；D 有总预算，超时有 provider + URL 记录。
3. 四题 Smoke 5 的结果都已推到 `feature/v01-office-renderer` 的 `v01/smoke/review/smoke5/<id>/`（停在 3 或 E 失败的题，交的是请求文件和日志，不是编出来的答案）。
4. 没有任何编造：没有自写的 agent 答案、没有凭空的图片 / 数据 / 请求号；缺的就是诚实占位。
5. 只推你自己的分支；不碰 master/main，不 force push，不往 `feature/v01-pipeline` 推代码。

### 第 4 轮（2026-11-21 美东，宝特）：5B 尚未通过；先修 N2 误杀，并重做版式多样性

**5B 目前只是部分结果，不能算通过。** 用户在 `51703f4` 上跑了真机 Smoke 4。机器已更正为 **Office 2021 Home & Student Retail**，PowerPoint / Excel **16.0.20625.20108**（零售版 Click-to-Run 的 Build 可以到 20xxx，不能只凭 Build 号写成 Microsoft 365）。`pptv01 doctor` 为 READY。T12、T36：`RUN_EXIT=0`、`GATES_EXIT=0`，用户已用桌面 PowerPoint 打开并截图（见 `v01/smoke/review/T12_review.jpg`、`T36_review.jpg`）。结论：能渲染、缺的地方没有胡编（占位框诚实），**但质量一般，每页版式几乎一样，这是最难接受的一点。** T02、T26：`RUN_EXIT=3`（旧 `E_response_1` 映射了 D 没取到的素材）；补答 `E_response_2.json` 已写入仓库，待用户续跑。完整 zip 仍请回传。**5B 仍算未完成。**

用户本机全量 pytest（清环境）：**1 failed，520 passed，1 skipped**。单独跑 `-m windows_com` 与 `-m "not windows_com"` 都是退出码 0。失败用例：`tests/test_n2_com_session_safety.py:222` `test_n2_watchdog_timeout_never_kills_preexisting_user_powerpoint`（`assert [{41916}] == []`，该用例单独跑是绿的）。

**根因（不是模块级「PID 列表」没清掉，而是假 COM 测试在 Windows 上误用了真实进程表）：**

- 该用例（`:211-222`）只注入了假的 `app_factory`，**没有**注入 `list_excel_pids_fn`。于是会话在 `__enter__` / 超时路径里走默认实现 `lambda: _list_pids("EXCEL.EXE")`（`session.py:222-223, 359, 261-266`）。`_list_pids`（`:75-94`）在 Windows 上会跑真的 `tasklist`；Linux 上固定返回空集，所以评审机上看不出来。
- 看门狗 `_on_timeout`（`:277-290`）以及超时后的 `__exit__`（`:536-541`）会把 `当前 Excel PID − _pre_excel_pids` 当成「本会话新拉起的」并交给 `terminate_pids_fn`。41916 是真实 OS PID，不是测试里的假 PID。
- `_list_pids` 一旦异常或 `tasklist` 暂时没列出进程，就会返回空集（`:94`）；`__enter__` 里再包一层 `except` 也把基线设成空集（`:360-361`）。基线为空时，稍后出现的任何 `EXCEL.EXE`（上一轮 `windows_com`/doctor/preflight 残留、任务管理器里还在启动的进程、或 tasklist 抖动）都会被当成「自己启动的」去杀。这也会解释「单独跑绿、整场全量红」。
- 代码里没有模块级/类级的 `_spawned_pids` 缓存；`_COM_THREAD_STATE`（`:41`）只记 `CoInitialize`。

**修法（N2 回归，可在 Linux 上用假列表测，但必须在 Windows 全量 pytest 上验收）：**

1. 所有 fake-COM 会话测试（尤其 `test_n2_watchdog_*`）必须注入稳定的 `list_excel_pids_fn` / `list_powerpnt_pids_fn`，禁止在单测里打到真实 `tasklist`。
2. 生产路径不要把「tasklist 失败 / 空基线」当成「机器上没有 Excel」；列举失败应报错或重试，不得据此 `taskkill`。只终止在 `DispatchEx` 之后明确记入 `_spawned_*` 的 PID。
3. 验收：Windows 上清环境先跑全量 `pytest`（不要拆成两个 `-m`），退出码必须为 0；再确认该用例单独跑、以及「先跑 `-m windows_com` 再在同一会话跑该用例」也绿。把三个退出码写进汇报。

T02/T26 的补答已就绪；用户续跑并交回 zip 之前不要做 N7。

#### 真机观感（T12 / T36 截图，2026-11-21 美东）与版式多样性（本轮必须做）

截图：`v01/smoke/review/T12_review.jpg`（咖啡店 7 页）、`v01/smoke/review/T36_review.jpg`（人工智能，要求少字，5 页）。用户原话大意：做是做出来了，缺的地方不胡来是好的，但每页风格基本一样很难接受。截图里能直接看到的问题，全部写成验收标准：

1. **同一套模版打到底。** 每页都是：浅蓝色顶栏标题 + 左上角一小块项目符号 + 右下（或整块）一个巨大的黄色虚线占位框或图片框。T36 的 1–3 页缩略图几乎可以叠在一起；T12 有图的页也是「左图右大黄框」。
2. **占位框占了英雄位。** T36 要求尽量少用文字，但 5 页里至少 3 页的视觉中心是写着「待补充：…（U00x）」的大黄框。占位必须改成**小而紧凑**（窄条、角标、或汇总到文末「待补充清单 / 附录」页），**不得作为页面主视觉**。
3. **字很小、空很大，没有大数字/大引语版式。** 正文只有角落里 2–3 条小字项目符号，其余大片空白。没有 big-number、没有大引用、没有满铺图。
4. **2010 公文风：顶栏 + 细线 + 项目符号，没有层级和色彩变化。** 标题带、细分隔线、同一套蓝底，翻页没有节奏。
5. **出处脚注又小又漂。** 如图片署名/DOI 挤在底部很小一行，和内容区脱节（见 T12 第一页 Café de Flore 图注、T36 第一页 [A002] DOI）。

**任务（F 阶段，copy reuse first；先搜后写）：** 不要从零发明一套版式语言。先融合下面这些**已核存在 GitHub、许可证可用**的方案（看布局类型与选页规则，按需复制/改写并登记 `NOTICE.md`；不要整仓搬进来）：

| 仓库 | 许可（已核对） | 可融合的部分（已核到路径/文件） |
| --- | --- | --- |
| [slidevjs/slidev](https://github.com/slidevjs/slidev) | MIT | `packages/client/layouts/`：`cover`、`section`、`fact`、`quote`、`statement`、`image-left`、`image-right`、`image`、`two-cols`、`two-cols-header`、`intro`、`full`、`center`、`end` |
| [presenton/presenton](https://github.com/presenton/presenton) | Apache-2.0 | 根目录 `layouts.json`：具名版式（封面满铺图、目录、关于我们、目标网格、左右分栏、数据页等） |
| [icip-cas/PPTAgent](https://github.com/icip-cas/PPTAgent) | MIT | 代理式生成与「按内容套版」的思路（本项目 stage E 调研已提过）；只借规则与结构，不要把运行时整锅端过来 |
| [gitbrent/PptxGenJS](https://github.com/gitbrent/PptxGenJS) | MIT | 幻灯片母版/版式 API 与示例，作「一页一种结构」的对照 |
| [hugohe3/ppt-master](https://github.com/hugohe3/ppt-master) | MIT | 已在 `NOTICE.md` 借用 `slot_capacity`；再看 `skills/` 里的页面类型划分 |

`pipipi-pikachu/PPTist` 是 **AGPL-3.0**，只许看版式名称，**不许复制代码**。

F 为每一页按内容类型选出一个 `layout_id`（至少覆盖：封面 cover、章节节标题 section、对照 comparison/two-cols、大数字或数据 big-number/fact、时间线 timeline、图左/图右 image-left/image-right、大引语 quote、满铺图 full-bleed、以及「待补充清单」needs-input-list）。写进 `page_design.json`。编译层必须尊重该字段，不能再统一套「顶栏 + 左列表 + 右大框」。

**硬性验收（测试 + 门槛，对照上面的截图）：**

- 相邻两页的 `layout_id` 不得相同。
- 页数 ≥ 4 时，全稿至少使用 `min(5, 页数)` 种不同版式（T36 五页至少 4 种；T12 七页至少 5 种）。
- 新增硬门槛（如 `layout_diversity`）：统计 `page_design` / 编译结果的 `layout_id`，违反上两条则 fail；并把「顶栏+左列表+右大框」登记为一种 layout，同一稿里最多出现 1 页。
- 占位框面积不得超过该页内容区的约 15%，且不得占据页面最大的内容槽；缺图缺表时改用紧凑标记或最后一页「待补充」清单。T36 这类少字稿尤其禁止让「待补充」成为主视觉（用截图 T36 第 1–3 页做反例测试）。
- 有真实图时，图应进入主槽；脚注/署名固定在槽下或备注，不得漂在空白里当装饰。
- 增加版式夹具测试：构造 5 页和 7 页的 `page_design`，断言相邻不重复、种类数达标、占位过大被 QA/门槛抓出。Linux 可测编译与门槛；真机效果仍要看图。

调研一页写到 `v01/docs/stage-f-layout-diversity.md`（候选、许可证、取舍、融合了什么），先于或随实现提交。


#### 可选：Bivia 判断器（H/E/C 的把关后端，非必须，不挡上面的 N2 与版式）

用户提供了 Articul8 [Bivia](https://docs.articul8.ai/bivia/)（`POST /boolean`：是/否 + 校准把握，可看图；`status` 可为 `abstained`）。宝特用 smoke 真案例和 HANDOFF 里的 B2/B7/B13/B14/B15 做了 60 次调用（52 fast + 8 deep；fast 设 `think=false`）。图片不能让 Bivia 服务器直接抓 Wikimedia URL（会 422 Forbidden），评测改为本地下载 800px 缩略图再传 raw base64。脚本和结果在评测机的 `/workspace/bivia-eval/`（**不要把 key、调用原始记录或图片推进本仓**）。

**结论：值得做成可选后端，但绝不能当硬依赖，也不能单靠它给错图放行。**

fast 分类结果：

- **陈述是否被文献支撑：14/14（100%）。** 最稳，适合 E/H 问「这句话能否从这条参考文献（标题/摘要）得出」。
- **图片是否就是目标地点：19/22（86%）。** 能正确拒绝：伊利诺伊州 Pekin 公园不是北京（B7）、Solarpunk 插画不是北京真实照片、阿肯色州同名地点不是雅典帕特农（B14）。**但有两个高把握误判「是」，其中一个正是 B15 的错图：** `File:ISS-39 New York City.jpg` 被说成北京（fast p=0.97，deep 仍判 yes、p=0.98）；天津桥园公园也被说成北京（p=0.99）。北京的 Landsat 真图反而被判「不是」（p=0.88；deep 弃权）。城市远景/卫星图上的「是」不可当作「就是目标地点」的充分条件。
- **C 阶段是否在要求或允许自绘：6/8。** 四条否定句（如「不允许凭空绘制」，B13）全部判对。「否则自绘并说明」判对，但更短的「否则自绘」被高把握判错；「不允许凭空绘制数据，但缺失示意图可自绘」fast 低把握判错，deep 改正。
- **是否规划说明而非听众正文（B2）：5/8。** 不够稳，先不要当硬门槛。

deep 模式更爱弃权（8 次里 5 次），能修正个别 fast 错案，但没纠正 B15 那张纽约照；服务端延迟约 0.7–4.5 秒，token 明显高于 fast（本次 deep 8 次就约 6.1 万 billable）。只建议在 fast 把握不足时加一刀，不要当默认。本次 60 次合计约 14.4 万 billable token（按每百万 input $0.042 约 $0.006）。每次调用至少按 1000 token 计；免费额度 90 天过期；遇 429 要退避。

**若做，必须满足：**

1. 默认关闭。只有同时设置 `A8_API_KEY` 和 `PPTV01_BIVIA_JUDGE=1`（或 `on`）才调用。缺 key、网络失败、非 2xx、或解析失败时，流水线必须照常跑完，不得变成硬依赖，也不得因此把素材/陈述直接判通过。
2. `status=abstained` 或把握低于约定阈值（建议默认 0.90，可配）时，按缺失处理：标 `needs_review`，或退回占位 /「待补充」。不得据此放行。
3. 看图核验只许做**否决**：Bivia 高把握说「不是该地点」则拒绝该候选；Bivia 说「是」也不得单独作为「候选就是目标实体」的充分条件（B15 的纽约照、天津公园就是高把握误肯定）。仍要叠加元数据 / 实体命中规则（见 N5）。
4. 单测必须用假客户端；默认 `pytest` **不准出网**。禁止把 key 写入代码、日志、fixtures 或提交。
5. 这是可选项，排在 N2 和版式多样性之后；不完成也不挡第 4 轮必做项的验收。

### 第 3 轮已通过（2026-11-20 美东，宝特；对象 `feature/v01-office-renderer` 头 `51703f4`）


e90b80f 之后新到 2 个提交（均 2026-11-20 美东 11:19 PM，作者 Antigravity (Gemini)）：

| 提交 | 内容 |
| --- | --- |
| `2d0c3ef` | N0：在 `test_n0_windows_com_skips_when_powerpoint_unavailable` 注入假 pywin32 模块，覆盖「PowerPoint COM unavailable」分支；另有「pywin32 缺失」用例 |
| `51703f4` | N2：启动前若已有 `POWERPNT.EXE` 则拒绝；`DispatchEx` 后若无新 PID 则立即释放并报错；`pptv01 doctor` 增加 `powerpoint_not_running` 阻断项 |

**测试（评审机 Linux，全部 `PPTV01_*` unset，`pytest -o addopts="" -rs`）：516 passed，6 skipped（均为 `windows_com`，原因：当前平台是 linux），pytest 退出码 0。**

| 项 | 结论 | 证据 |
| --- | --- | --- |
| **N0** | ✅ 通过 | `tests/test_n0_windows_com_skip.py:19-32` 在没装 pywin32 时断言原因含 `pywin32 not installed`；`:35-52` 注入假 `pythoncom` / `win32com` / `win32com.client` 后再测 `PowerPoint COM unavailable` 分支。Linux 全量退出码 0。 |
| **N2 启动前拒绝** | ✅ 通过 | `session.py:424-428`（真 COM 路径）与 `:364-371`（app_factory 路径）：已有 PID 且未显式允许时抛 `RendererUnavailableError`（「检测到 PowerPoint 已在运行…请先保存并关闭所有 PowerPoint 窗口」），不调用 `DispatchEx`。测试：`tests/test_n2_com_session_safety.py:20-43`、`:139-152`。 |
| **N2 无新 PID 则报错** | ✅ 通过 | `session.py:448-460`：`DispatchEx` 后 `spawned` 为空则把 COM 引用置空并 `gc.collect()`，不碰 `Presentations` / `Quit` / `taskkill`，抛同样的明确错误。测试：`:46-86`、`:154-159`。 |
| **N2 不附着、不误杀** | ✅ 通过 | 生产代码 `_connect_powerpoint`（`session.py:117-119`）只用 `DispatchEx`；`rg GetActiveObject` / `GetObject(` 在 `src/pptv01/` 仅出现在说明注释（`:5`）。`Quit` / `taskkill` 只作用于 `_owns_instance` 且 `_spawned_pids` 非空（`session.py:487-490, 510-549`）。测试：`:110-132`、`:277` 起。 |
| **N2 看门狗** | ✅ 通过（此前已有，仍在） | 会话超时默认 120s、单步 45s（`session.py:36-37, 294-340`）；超时只杀自己启动的 PPT/Excel PID（`:277-290`）。测试：`:187`、`:226`。 |
| **N2 doctor 阻断** | ✅ 通过 | `doctor.py` `_probe_powerpoint_not_running`（约 `:144-172`）为必检项；正在运行则 `[FAIL]` 并提示先关闭。测试：`tests/test_r7_doctor.py:94-110`。可选环境变量 `PPTV01_ALLOW_EXISTING_POWERPOINT` 仅作显式高级跳过，默认拒绝（符合此前约定）。 |

**下一步（给用户，不是给反重力写代码）：按第 5B 节在本机做真实 Windows 冒烟。** 开始前务必走「冒烟前检查单」：关掉所有 PowerPoint 窗口（包括「文件 → 账户」），任务管理器里结束残留的 `POWERPNT.EXE` / `EXCEL.EXE`，跑的过程中不要再打开 PowerPoint。把 doctor、三组 pytest 退出码、Smoke 4 的 zip 和勾选结果回传。N7（切换默认渲染器、删除 python-pptx 写路径）仍须等 5B 回传通过后再做。

### 第 3 轮复核原文（2026-11-21，对象当时的头 `e90b80f`；下面两条已在 `2d0c3ef` / `51703f4` 修好，勿再做）

宝特在 Linux 上清环境跑了全量 `pytest`：除下面第 1 条外全部通过（`windows_com` 用例按预期跳过）。N2 的方向正确（只用 `DispatchEx`、记录自己启动的 PID、每步和整个会话都有超时、生产代码 `rg GetActiveObject` 为 0），但还有一个会再次挂死的缺口（第 2 条）。**这两条做完、推送之后，再跑 5B。**

1. **N0 未通过（B17 未关）：** `tests/test_n0_windows_com_skip.py::test_n0_windows_com_skips_when_powerpoint_unavailable` 在没装 pywin32 的机器上失败。原因：`check_windows_com_skip_reason(platform_name="win32")` 先检查 `import pythoncom`，没装时返回的是 `"pywin32 not installed (...)"`，根本走不到用例想测的 `"PowerPoint COM unavailable"` 分支。修法：该用例用 `monkeypatch.setitem(sys.modules, "pythoncom", ...)` 和 `win32com` / `win32com.client` 的假模块把 pywin32 装上，再测 preflight 抛 `RendererUnavailableError` 的分支；另外单独加一条「pywin32 缺失 → 原因含 `pywin32 not installed`」的用例。不要改 conftest 的判断顺序来迁就测试。验收：Linux 清环境 `pytest -o addopts="" -rs` 退出码 0。
2. **N2 缺口：用户已开着 PowerPoint 时仍可能挂死。** PowerPoint 是单实例程序：用户已经开着它时，`DispatchEx("PowerPoint.Application")` 不会起新进程，而是连到用户那个实例上。此时 `_spawned_pids`（`post - pre`）为空：代码不会去关用户的 PowerPoint（这是对的），但超时后也没有「自己的进程」可以杀，于是仍会卡住，并且会在用户的实例里开文稿、弹对话框。修法（session.py）：
   - **启动前检查：** `preflight` / 会话开始时，如果 `_list_powerpnt_pids()` 非空，**直接抛明确错误并退出**（文案示例：「检测到 PowerPoint 已在运行（PID …）。PowerPoint 只允许一个实例，请先保存并关闭所有 PowerPoint 窗口再运行。」），不要调用 `DispatchEx`。可留一个显式参数/环境变量允许高级用户跳过，默认必须拒绝。
   - **启动后复核：** `DispatchEx` 之后如果 `_spawned_pids` 为空（说明连到了别人的实例），立即释放 COM 引用、不做任何操作、抛同样的明确错误。
   - **测试（fake COM，Linux 可跑）：** （a）启动前已有 POWERPNT.EXE PID → 抛错，且 `DispatchEx` 没被调用；（b）`DispatchEx` 后没有新 PID → 抛错，且没有对该实例调用 `Presentations.Add` / `Quit` / `taskkill`。
   - doctor 也要把「PowerPoint 正在运行」报成阻断项，并提示先关掉。

### 紧急：先做 N2（COM 会话必须隔离，否则冒烟会再挂死）

上一轮用户在真机跑 Smoke 4 时进程挂死。最可能的原因：用户当时开着 PowerPoint（在「文件 → 账户」里核对版本），COM 会话通过 `GetActiveObject` 连上了这个正在运行的实例（B8），和/或弹出了模态对话框，而代码没有超时看门狗，于是一直等下去。**B8/N2 已在 `51703f4` 修好（见上方「第 3 轮已通过」）。现在可以按 5B 重跑，但必须先关掉所有 PowerPoint。**

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
> **先决（2026-11-20 美东更新）：第 3 轮已通过（头 `51703f4`），现在可以跑本节的 doctor / `windows_com` / Smoke 4。** 上一轮冒烟在用户开着「文件 → 账户」时挂死；N2 已改为「已有 PowerPoint 就拒绝并明确报错」，所以**跑之前必须先关掉所有窗口并清掉残留进程**（见下面检查单），否则 doctor 和冒烟会直接失败（这是预期行为，不是挂死）。

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
3. 把挂死时的最后一段终端输出、是否弹出过激活/登录/安全模式对话框、任务管理器截图，一起回传给宝特。确认已按检查单关干净 PowerPoint 之后，可以再跑一次；若在 `51703f4` 上仍然挂死（而不是立刻报「请先关闭 PowerPoint」），把完整输出发回。


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
