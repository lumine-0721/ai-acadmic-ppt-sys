# HANDOFF.md — 给编码 Agent（Antigravity / Gemini）的任务书

> 读者：接手写代码的 Agent。本文自包含：读完即可开工，不需要别的上下文。
> 仓库：<https://github.com/lumine-0721/ai-acadmic-ppt-sys>，基线分支 `feature/v01-pipeline`，本项目目录 `v01/`。
> 分工：**你负责实现**；宝特（Grok Bot，代表用户 qing he）**定方向、出基准试卷和评分、做评审**。你不改基准试卷，也不自己给自己评分定稿。

---

## 1. 项目目标与现状

**目标。** AI PPT Sys V0.1：用户一句话 → 一份**可检查、可编辑的原生 PPTX**。中间是一条线性链 A→H，每一站把结果以 JSON 契约落盘到 `runs/<run_id>/`，任何一站都能单独打开检查，没有多 Agent 编排、没有 Deck IR、没有旧的 handoff 文件。

| 阶段 | 作用 | 状态 |
| --- | --- | --- |
| A | 一句话 → `production_plan.json`（含 retrieval_brief） | ✅ 完成 |
| B | 检索（Wikipedia/Crossref/可选 SearXNG）+ 相关性过滤 + 暂定页面规划 `slide_plan.json` + 结构化 `asset_needs` | ✅ 完成 |
| C | `asset_requirements.json`：每个 B 的需求要么落成素材需求，要么带理由丢弃 | ✅ 完成 |
| D | `asset_registry.json` + `assets/`：Commons 图片（许可白名单/作者/来源页）+ Wikidata 消歧 + OpenAlex(pyalex)/Crossref DOI 参考文献；diagram/icon/statistic/chart_data/table 记为 `missing` 并写原因 | ✅ 完成 |
| E | Slide Asset Mapping：哪个素材放哪页、干什么 | ⏳ `not_implemented` |
| F | Page Design：每页版式 JSON + 预览 | ⏳ `not_implemented` |
| G | 用 python-pptx 生成 `deck.pptx` | ⏳ `not_implemented` |
| H | LibreOffice 渲染 + QA + 修复 | ⏳ `not_implemented` |
| 评分 | PPTEval（Content/Design/Coherence，PPTAgent 提示词 MIT 原样引入）+ 独立的确定性硬门槛 | ✅ 框架完成；Design 维度要等 G/H 出渲染图才能评 |

`run_report.json.overall` 在任何阶段是 `not_implemented` 时为 `incomplete`；只有每站都 `done` 才可能是 `complete`。

**关键提交（分支 `feature/v01-pipeline`）：**
`6b7004c` 相关性过滤 + reranker 基准 · `bc6a618` cn2an 数字归一化 · `6cdde83` PPTEval 评分与硬门槛 · `e2fb844` D 阶段 · `d58ee60` D 的实跑缺陷修复 + `docs/smoke-v3-results.md`。

### 安装、测试、跑 CLI
```bash
git clone https://github.com/lumine-0721/ai-acadmic-ppt-sys.git && cd ai-acadmic-ppt-sys
git checkout feature/v01-pipeline && cd v01
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,rank]"          # rank = fastembed 相关性打分（可选，缺失时只用结构规则并记录）
# 干净环境跑全量测试（必须先清掉所有 PPTV01_*；一定要看 pytest 自己的退出码，不要接 | tail）
for v in $(env | grep -o '^PPTV01_[A-Z_]*'); do unset $v; done
pytest ; echo "PYTEST_EXIT=$?"            # 必须 0
pptv01 run "帮我介绍一下城市热岛效应。" --out runs/ --run-id T02
pptv01 validate slide_plan runs/T02/slide_plan.json
pptv01 gates runs/T02            # 确定性硬门槛（未实现的阶段是 not_run，不会假装通过）
pptv01 grade runs/T02            # PPTEval；需配置评分后端，见下
python -m pptv01.contracts       # 改了契约后重新导出 contracts/*.schema.json（有测试检查是否过期）
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
                  e_mapping/ f_layout/ g_pptx/ h_qa/   ← 目前是空壳，你要填的地方
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

1. **分支**：从 `feature/v01-pipeline` 拉自己的分支（例：`feature/v01-e-mapping`、`feature/v01-d-dedupe`），**只推你自己的分支**。**绝不推 `master`/`main`，绝不 force push，不开 PR，不改仓库设置，不改全局 git 配置。**
2. **测试**：每次推送前，在**清环境**（unset 全部 `PPTV01_*`）下跑全量 `pytest`，**看 pytest 自己的退出码是 0**（不是 `| tail` 的退出码）。新增行为必须带测试；契约改动必须同步 `contracts/*.schema.json`、fixtures。
3. **不造假**：不得把假数据当成真实模型输出或真实外部结果。fake client / HTTP replay 只能在 `tests/` 下；包代码里**不许有 mock 回退**（缺配置就明确报错）。用 agent 后端跑出的结果必须标注 `agent:宝特 (manual, not an API model)`。
4. **契约**：所有落盘 JSON 都是 Pydantic v2 模型，`schema_version = "0.1"`，带 `run_id`，ID 为 `S###`/`A###`，路径一律相对路径。不要悄悄放宽已有校验；要放宽先说明理由。
5. **先搜后写（GitHub-search-first）**：每实现一个阶段/组件之前，先搜成熟方案并优先"融合"而不是重造；在 `v01/docs/` 下为该阶段写**一页**《现成方案调研 + 融合决定》（候选、许可证、取舍、为什么融/不融；可参考 `docs/stage-d-research.md` 的格式），**先于实现提交**。引入第三方代码/提示词要保留许可证与署名（参考 `v01/NOTICE.md`）。
6. **冻结区**：不要改仓库根目录的旧代码（`apc/`、`seaa/`、`aprd/`、`orchestrator/` 等），也**不要改 `v01/benchmark/v1.0/` 的任务与分层**。新代码只放 `v01/`。
7. **运行产物**：`v01/runs/` 已被 gitignore，不要提交运行产物、密钥、下载的素材；`orchestrator/.tmp-tests/` 不是你的，别碰。
8. **诚实状态**：做不到的就记成 `missing` / `not_run` / `needs_user_input` 并写原因；不要让流程显示"成功"。

---

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

## 4. 优先级任务清单（按顺序做；每项一个小批次，推送前过测试）

### (a) D：跨需求去重
- **问题**：Smoke 实跑中 T26 的 A001/A002 是同一张帕特农图。
- **做法**：同一 Commons 文件（以 `identifier`/`sha256` 判断）被多个需求选中时，后来的需求优先选下一个通过过滤的候选；没有就诚实地复用并在记录里写 `shared_with: A001`（契约新增字段，E 阶段允许同一素材多页复用但每页要写用途）。
- **验收**：用 `tests/replay` 的真实录制响应写测试：两个同目标需求不再产生同一文件，或明确 `shared_with`；候选审计里能看到"因已被 A001 使用而降级"；不新增网络调用；全量测试绿。

### (b) 用户补充请求机制（第 3 节全部）
- 先写《现成方案调研》一页（占位/待办清单类做法有无可借鉴，如 human-in-the-loop 模式；结论可以是"自研，因为……"）。
- **验收**：① `UserInputRequests` 契约 + schema + fixture + 测试；② D 结束确定性生成 `user_input_requests.json`，每个 `missing` 的 diagram/chart/statistic/table/icon 都有一条，`image`/`reference` 的 missing 也有（kind 对应）；③ `requests`/`provide` CLI 与 `--on-missing wait|placeholder`（wait 时退出码 4）；④ `run_report.overall` 新取值与测试；⑤ 两条新硬门槛及单测（含"故意放一张未绑定素材的图 → 门槛失败"）；⑥ 用户提供的数据文件被拒绝的测试（缺来源/许可、类型不符）。

### (c) E：Slide Asset Mapping
- 调研融合：PPTAgent（`icip-cas/PPTAgent` tag v1.1.38，MIT；其中的 DeepPresenter 方向请你自行核对许可证与实际能力）里的素材与页面匹配做法；写调研一页。
- **输入**：`slide_plan.json` + `asset_registry.json`（+ `user_input_requests.json`）。**输出**：`slide_asset_mapping.json`（契约已有草案：`page_id/asset_id/usage/accepted`、`unused_assets`）。
- **规则**：只映射真实已取回（或用户已提供）的素材；每个映射写具体 `usage`；未使用素材必须写原因，不许为用掉而塞进无关页；`missing`/open 请求**不得**被映射成"已有素材"。图片与页面主题的**相关度**：有视觉模型（`PPTV01_E_VISION_MODEL` 等，自行命名并文档化）就做真实判断；没有就在 `accepted` 旁记录 `relevance: not_assessed`，不假装检查过。
- **验收**：契约/schema/fixture；runner 集成（E 变 `done`）；测试覆盖：未使用素材带原因、同素材多页复用各有用途、missing 不被映射、视觉模型缺失时诚实标注、LLM 输出走同一套 JSON 校验+一次修复重试；Smoke 4 的 E 能跑通。

### (d) F：Page Design + 预览
- 调研融合：PPTAgent 的版式/模板编辑思路；`hugohe3/ppt-master`（MIT，README 描述的"SVG → 原生可编辑 PPTX"路线，及其 `slot_capacity` 对中英文每行容量的估算思路，旧仓库 `aprd/.../THIRD_PARTY_NOTICES.md` 里有已经改写过的先例）。**先读其许可证与实际入口再决定融合到什么程度；不整包接入，不拼接两套生成流程。**
- **输出**：`page_design.json`（契约已有草案：每页元素、英寸坐标、层级、字号、绑定素材路径）+ `previews/`。画布 13.333×7.5 英寸。允许因素材比例或内容密度改动原规划，原因写进该页 `notes`；F 不发明未绑定的新素材；占位元素按第 3 节规范出现在设计里。
- 预览：F 阶段用 Pillow 按设计 JSON 画**版式示意图**，文件名/页内水印明确标注 "layout schematic, not a render"；真实渲染由 H 产出。
- **验收**：几何校验（不越界、不重叠到不可读、字号下限）、中英文文本容量估算有测试（溢出时降字号/拆行/记入 notes 而不是静默截断）、`user-stated` 页数/少字约束（如 T36）被遵守、runner 集成、测试。

### (e) G：python-pptx 生成 `deck.pptx`
- **要求**：原生文本框/形状/图表，外部图作为图片对象嵌入，**不是整页截图**；占位按第 3 节规范；每页备注写来源与许可（署名要求的图片必须在页上或备注里署名）；图表只用带出处的数据。
- **验收**：`deck.pptx` 能被 python-pptx 重新打开并校验页数=计划页数；硬门槛 `pptx_opens`、`native_content_not_screenshots` 从 `not_run` 变为真实检查并有测试；署名缺失会失败；runner 集成（G 变 `done`）。

### (f) H：LibreOffice 渲染 + QA + 修复，打开 PPTEval Design 评分
- 渲染：LibreOffice 无头 → PDF → 每页 PNG（需要 `soffice`、`pdftoppm`；环境里缺就**明确报错并记录**，不要跳过后说成功）。
- **QA**：逐页检测文字溢出、遮挡、缺图、布局异常、字体与比例、内容与素材不匹配，写 `qa_report.json`（契约有草案）；有 blocker/major 时触发有限次数的修复（改字号/位置/拆分），每次修复后**复验**并记录；达上限仍未修好就诚实留在报告里。
- **PPTEval**：`pptv01 grade runs/<id> --slides-dir runs/<id>/render` 即走 `pptx_render` 证据级别，评 Design（此前为 not_assessable）。你只需保证渲染图命名为 `slide_*.png`；评分归宝特，不要自己造分。
- **验收**：渲染/QA 的单测（用小型真实 PPTX 夹具，LibreOffice 不可用时对应测试 skip 并说明）；故意构造溢出/遮挡能被检出；修复循环有上限且有复验记录；Smoke 4 能 A→H 出 `deck.pptx` + 渲染图。

---

## 5. 已知弱点（接手时就要知道，别当作已解决）

1. **图片与主题的相关度没有被程序核验**：D 只靠 Wikidata P18 / Commons 元数据 + 文本相关性；"内容是否对题"从未看图判断。实跑里：T02 的北京公园照与热岛主题弱相关；T26 的佛光寺是屋檐局部、萨伏伊别墅是室内浴室。
2. **过滤误杀**：B 的 fastembed@0.30 在 T02 把相关的 "Green roof" 过滤掉了；基准样本很小（3 个任务、65 条笔记，标注由 agent 自己做、不独立，结构规则是在这批数据上拟合的）。bge-reranker 已评估，不优于 fastembed，默认关闭（`docs/relevance-benchmark.md`）。
3. **A/B/C 的答案是 agent 手写**（agent 后端），不是 API 模型的产出；换成真实模型后质量与失败模式未知。
4. **D 不画图也没有结构化数据源**：diagram/chart_data/statistic/table/icon 全部 `missing`——这正是第 3 节要解决的。
5. **跨需求未去重**（任务 (a)）。
6. **PPTEval 现在只有 `plan_text_proxy` 证据**（Design 不可评），要等 G/H。
7. 检索只覆盖 Wikipedia/Crossref/OpenAlex/Commons；OpenAlex 匿名配额小，配额错误会如实记录，会让参考文献偏少。

---

## 6. 宝特如何评审，你要汇报什么

**评审节奏**
- **Smoke 4（T02、T12、T26、T36）**：每个重要提交都跑一遍，看产物、门槛、`run_report`；不通过就打回。
- **Regression 12（T02 T04 T06 T08 T12 T16 T26 T32 T36 T37 T39 T40）**：每完成一个开发批次（比如 (b) 完成、(c) 完成）跑一遍。
- **盲测集**：宝特私有，不进仓库，不告诉你题目内容；不要针对 `benchmark/v1.0` 的具体题做硬编码/拟合（宝特会用盲测集检查过拟合）。
- 评分（PPTEval、硬门槛、人工看图）由宝特执行并回你结论；你可以本地跑 `pptv01 gates` 自检，但不要把自评分当结果。

**每次汇报（发给宝特/用户）必须包含**
1. 分支名与提交 hash 列表（一句话说明每个提交做了什么）；
2. 清环境全量 `pytest` 的结果（命令、**pytest 自身退出码**、用例数）；
3. 对应任务的验收清单逐条自检（做了/没做/做不到及原因）；
4. 新增的调研一页（路径）与引入的第三方代码及许可证；
5. 若改了契约：改了什么、为什么、schema/fixture/测试是否同步；
6. 你已知的新弱点或未完成项。

做不到就如实写"做不到 + 原因"，不要用假数据凑齐验收项。
