# HANDOFF.md — 给编码 Agent（Antigravity / Gemini）的任务书（第 2 版：2026-11-20）

> 读者：接手写代码的 Agent。本文自包含：读完即可开工，不需要别的上下文。
> 仓库：<https://github.com/lumine-0721/ai-acadmic-ppt-sys>，本项目目录 `v01/`。
> **代码基线（重要）**：你上一轮的成果在分支 `feature/v01-h-qa`（头提交 `4550cc1`，已评审）。**本轮请从它拉新分支 `feature/v01-office-renderer`**（`git fetch --all && git checkout -b feature/v01-office-renderer origin/feature/v01-h-qa`）。本文件所在的 `feature/v01-pipeline` 只放任务书/调研文档，**不要往上面推代码**，也不要把你的分支合进去（合并由用户决定）。
> 分工：**你负责实现**；宝特（Grok Bot，代表用户 qing he）**定方向、出基准试卷和评分、做评审**。你不改基准试卷，也不自己给自己评分定稿。
> 配套文档：`v01/docs/renderer-office-research.md`（Office 自动化路线调研 + 融合决定，**先读**）。
> **本版相对第 1 版的变化**：第 1 节状态表更新为 A–H 均已有实现；第 4 节是对你上一轮推送的**评审结果**（含 bug 清单，带文件:行）；第 5 节是**新的任务清单**——最大的变化是用户决定**渲染层不再用 python-pptx，改用 Office 自动化（PowerPoint COM 为主）**，同时保留契约/素材登记/映射/版式计划/硬门槛这些好思路。

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

## 4. 对你上一轮推送的评审结果（宝特，2026-11-20）

**评审对象：** 分支 `feature/v01-h-qa`，提交 `30f1b7f … 4550cc1`（11 个提交，作者 Antigravity (Gemini)，均晚于 `dc9cd22`）。

**测试：** 清环境（`PPTV01_*` 全部 unset）全量 `pytest`：**442 passed，0 skipped，pytest 自身退出码 0**。soffice/pdftoppm 在评审机上可用，所以 H 的渲染测试是真跑的。

**Smoke 4（T02/T12/T26/T36，agent 后端；A–C 沿用旧的手写答案，E 由宝特手写并标注 `agent:宝特 (manual, not an API model)`）：** 四题都 A→H 全 `done`，`overall=complete_with_placeholders`，`pptv01 gates` 全部 pass，QA 报告 0 issue。T02 7 页（5 个占位）、T12 7 页（9 个占位）、T26 8 页（5 个占位、7 张真实图）、T36 ≤5 页（5 个占位，符合"不超过 5 页"）。另手动验证：`--on-missing wait` 退出码 **4**、`overall=needs_input`、E–H 为 `skipped`；`provide` 拒绝空来源和损坏 PNG；`provide` 一个 5 节点流程图后图能进 deck（但见 B1、B5）。

### 逐项验收

| 任务 | 结论 | 说明 |
| --- | --- | --- |
| (a) D 去重 | ✅ 通过 | T26 的 A001/A002 不再同一文件；候选审计里有 `demoted: already used by A001`；`shared_with` 契约字段 + 校验 + 测试齐。局限：去重只按文件/哈希，**不识别"同一建筑的另一张照片"**（T26 的 A002 仍是帕特农），验收标准字面上满足。 |
| (b) 用户补充请求 | ✅ 通过，有 2 个缺陷 | 契约/schema/fixture/测试齐；D 后确定性生成清单；`requests`/`provide` CLI；`wait` 退出码 4；`overall` 新取值；两条新硬门槛。缺陷：**B1**（resume 复用旧 E–H，provide 的材料不生效）、**B3**（请求文案仍写"稍后自绘"）。 |
| (c) E 映射 | ✅ 通过 | 只映射 acquired/provided；`unused_assets` 带原因；视觉模型缺失时 `relevance: not_assessed`（诚实）；missing 不被映射。 |
| (d) F 版式 | ⚠️ 基本通过，有缺陷 | 几何/容量/少字/页数约束/占位元素都有；T36 没超 5 页。缺陷：**B2**（把规划说明文字当正文印到页面上）、**B6**（版式质量：图小留白大、大圆角卡片）。 |
| (e) G 生成 | ⚠️ 部分通过 | 原生文本/形状/图片、备注署名、`pptx_opens`/`native_content`/`attribution_present` 是真检查；缺陷：**B4**（硬门槛可被改名骗过）、**B5**（流程图不画边、静默截断节点）。**该阶段将被 Office 渲染器取代，B5 不必在旧 G 里修，而要在新渲染器里验收。** |
| (f) H 渲染+QA | ⚠️ 部分通过 | LibreOffice→PDF→PNG 真跑、缺工具明确报错、有修复循环和复验记录、有测试；缺陷：**B6**（QA 对"看得出来的丑"报 0 issue）。 |

### Bug 清单（文件:行，均已在 `4550cc1` 复现）

- **B1 `runner.py:306/338/371/398`**：`--resume` 只要 `slide_asset_mapping.json`/`page_design.json`/`deck.pptx`/`qa_report.json` 存在就直接复用，**不比较上游输入是否变了**。复现：T36 跑完后 `pptv01 provide … U002 --file flow.json` 再 `--resume`，deck 里 U002 仍是 `PLACEHOLDER_U002`（要手工删 E–H 产物并重答 E 才生效）。任务书写的是"`--resume` 后 E–H 使用用户材料"，**未满足**。修法：在 `StageRecord` 里记录上游产物的 sha256，resume 时不一致就作废该站及下游；`provide` 成功后明确标记 E–H 为 stale。
- **B2 `f_layout/stage.py:501`**：`body_text = page.content_summary…` 被渲染成页面正文，而 `content_summary` 是 B 的**页面规划说明**（如"用一个简洁的图示说明概念，文字只保留最核心的一句"、"用开场提出观看建筑的几个角度，并交代后面怎么组织"），不是给听众看的文案。T26/T36/T02 的每页都印出了这种"导演提示"。修法：不渲染 `content_summary`；它进讲者备注或设计注释；正文只用 `key_points`。加测试：任何页面文本不得包含规划提示句式（至少断言文本集合 ⊆ `key_points ∪ title ∪ 署名/来源`）。
- **B3 `d_retrieve/stage.py:507-508`（`_MISSING_WHY`）及 C 的 `acceptance`（"若取用现成图须许可明确；否则自绘并说明"）**：写进 `user_input_requests.json.why` 的是 "diagram: nothing to retrieve; to be drawn natively in a later stage (F/G)" ——与产品规则（**不许自绘无来源的图**）矛盾，会诱导后续阶段"补画"。修法：`why` 改成"D 未找到可标来源的现成图；不允许凭空绘制，请用户提供结构化数据或图"；C 的 acceptance 去掉"否则自绘"。
- **B4 `grading/gates.py:92-105, 224`、`h_qa/inspect.py:207`**：`no_fabricated_visuals`/`attribution_present`/QA 的绑定检查**只看形状名字的正则**（`ASSET_A###…`）。复现：往 deck 里塞一张 A011 的图并命名为 `ASSET_A001__fake`，`pptv01 gates` 判 pass。另外 `is_visual` 只认 PICTURE/chart/table，**用普通矩形/连接线手画的"示意图"完全不在检查范围内**。修法：① 图片绑定要**用图片 blob 的 sha256 与 `asset_registry`/`inputs/` 里记录的 sha256 比对**；② 形状名用**白名单**（`bg_*`/`card_*`/`accent_*`/`title`/`bullets`/`summary`/`footer`/`PLACEHOLDER_U###`/已绑定的 `ASSET_/INPUT_/CHART_/TABLE_/DIAGRAM_`），出现白名单外的 AutoShape/Group/Freeform/Connector 就判 `unknown element`；③ 加"故意改名骗过""故意手画示意图"两个失败用例。
- **B5 `g_pptx/stage.py:243-282`（`_add_diagram_json_element`）**：① `edges` 只收集来源文字，**从不画连线**（T36 复现：5 个节点、2 条边的用户流程图只画出 3 个方框，没有箭头）；② `if nx + box_w > …: break`（行 260）**静默丢掉放不下的节点**（5 个节点只显示 3 个）；③ G 阶段不再校验节点/边的 `source`（只在 `provide` 时校验过一次）；④ `docs/stage-g-research.md` 声称用"原生连接线"，实际没有。**新渲染器里要用真连接线并对溢出报错或换行/换页，不许截断。**
- **B6 `h_qa/inspect.py`（全文件；PNG 只在 261-278 行查"是否空白/不可读"）**：QA 只做 PPTX XML 上的几何/估算，渲染出来的 PNG 基本没被分析。结果：T26 第 1 页是一张**空的大圆角卡片**、T36 的文字区大半空白、图片被缩成小图居中留白、各页图片大小不一致——全都报 "0 issue"。`font_or_ratio`/`text_overflow` 的依据是 `estimate_slot_capacity` 估算而不是真实排版。修法见第 5 节 R3/R5：用 PowerPoint 实测尺寸 + 可选视觉模型审图 + "留白/填充率"规则。
- **B7（D，已知弱点，评审新发现的实例）**：T02 的 A009 `File:Pekin park 20231019 0017.jpg` 是**美国伊利诺伊州 Pekin 的公园**（图里有美国国旗），不是北京（第 1 版任务书"北京公园照"的说法有误，已更正）；A011 `A Solarpunk tower.png` 是**插画**，不是真实城市场景；D 对这两张都判 `acquired`，只因为我在手写 E 答案时看图才发现并放进 `unused_assets`。D 仍然**没有任何看图核验**。（修法见 R6。）
- **B8（次要）** `README`/任务书里没有说明 `pptv01 provide` 之后需要 `--resume` 且要重答 E（B1 修好后应自动）；另外 `runs/*/user_input_requests.json` 的 `status` 在渲染后变成 `placeholder_rendered`，`provide` 能把它再改成 `provided`——行为合理，但请在 README 写明状态机。

### 评审通过的做法（保留，别推倒）
契约 + schema + fixture 同步机制；`user_input_requests` 由代码而非 LLM 生成；`no_fabricated_visuals`/`placeholders_marked` 两条硬门槛的**设计**（实现要加固，见 B4）；E 的 `not_assessed` 诚实标注；`--on-missing wait|placeholder`；占位的外观与命名；署名检查；每站调研一页。

---
## 5. 新任务清单：Office 自动化渲染器（按顺序做；每项一个小批次，推送前过测试）

> 用户的原话：「不能用 py pptx，用我说的 Office COM / VBA / Office.js API 自动化；保留优质思路；确保后续优化 copy reuse 1st；好改的就使劲改」。
> **路线结论（理由与证据见 `docs/renderer-office-research.md`）：主路线 = Windows 上 Python + pywin32 驱动桌面 PowerPoint 的 COM。** VBA 只当对象模型参考；Office.js（PowerPointApi 到 1.10 仍无法创建/读写图表）只留给第二阶段"插入当前文档"，**本轮不做**。
> **架构要点：** F 只产出后端无关的 `page_design.json`；新增纯 Python 编译层把它编成确定性的 `deck_ops.json`；COM 执行器只负责把操作翻成 COM 调用。这样契约和编译层在 Linux/CI 上就能测，只有执行器需要 Windows。

### 已确认的决定（用户 2026-11-20 回复，取代原"待确认问题"）
1. **目标环境：Office 2021 桌面版（永久授权）**，即 Office 16.0 对象模型；Excel 按"随 Office 2021 一起装了"假设，**但必须由 `pptv01 doctor` 实测确认**（版本/Build、`Excel.Application` 能否创建）。**不得使用 Microsoft 365 专属特性**：不用 Office.js 的 PowerPointApi 1.8+ 新接口（本轮本来也不做 Office.js）、不用 M365 才有的功能（如 Designer、Copilot、云端字体/图标库、动态数组图表等）；只用 Office 2021 里存在的 COM 对象模型（`AddChart2`、`AddConnector`、`TextFrame2.TextRange.BoundHeight`、`Presentation.Export`、`ExportAsFixedFormat` 等都是 Office 2013/2016 起就有的）。
2. **是真正的 Microsoft PowerPoint，不是 WPS**。不需要适配 `KWPP.Application`。
3. **Antigravity 就跑在用户同一台本机 Windows 上**，不是云服务器 → 它可以**直接测 COM**；R2/R3/R7 的真机验收由它在本机跑并汇报，宝特只在 Linux 侧复核产物（zip）。
4. **python-pptx 只读校验器可以接受**：放 `[verify]` extra，只读、永不写文件（规则 9 不变）。
5. **云端批量生成：以后再说，不在本轮范围。** 用户有云服务器（由云宝收集的免费额度机器，例如 Linux 的 Simular 盒子）：**Linux 云机不能跑 COM**；要云端批量就得 Windows VM + 交互式桌面会话（微软不支持无人值守自动化 Office，KB257757），届时再评估，**本轮不要为此写任何代码**。

**据此对下面任务的调整：**
- **R2**：`preflight()` 额外记录是否 Office 2021（`Application.Version == "16.0"` + Build）；`AddChart2` 在**真机 Office 2021**上先做一个最小实测（建图→写数据→关 Excel→保存→重新打开），结果写进 `docs/stage-g-office-research.md`；若 Excel 缺失或 `AddChart2` 失败，**明确报错/把图表请求回退为用户补充请求或占位**，不得静默画假图。
- **R3**：`Presentation.Export` 的 PNG 尺寸与字体渲染以本机 PowerPoint 2021 为准；`render_provenance` 写明 `powerpoint-com 16.0 (build …)`。
- **R7**：`pptv01 doctor` 必检项增加：`Application.Version` 为 16.0 且 Build 对应 2021 永久版（如实打印，不强行判断授权类型）、**Excel 存在且 `Excel.Application` 可创建**、中文字体（如微软雅黑/等线）是否安装、`NameFarEast` 设置能否生效。
- 所有验收里凡写"Windows"处，具体指**用户本机 Windows + Office 2021**；Linux 侧只跑不依赖 COM 的测试。

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

### Windows 上怎么测（给用户/你自己）
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
11. 目标机器已确认为 **Office 2021 桌面版 + 真正的 PowerPoint，Antigravity 在同一台本机 Windows 上运行**（见第 5 节「已确认的决定」）；Excel 是否随装、Build 号待 `pptv01 doctor` 实测。云端批量（含 Windows VM 方案）**后做**。

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
