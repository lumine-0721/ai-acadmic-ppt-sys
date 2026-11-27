# AI 学术 PPT：主题 / 版式 / 写法 / 配图 开源方案调研（第 6 轮输入）

调研日期：2026-11-21（美东时间）。只读调研，没有改动或推送仓库。
基线代码：`feature/v01-office-renderer` @ 3a833ea（worktree `/workspace/r4wt`）。
源码克隆在 `/workspace/research/src/`（浅克隆，可直接查看）。

---

## 0. 结论先行：为什么四份 smoke5 deck 长得一样（对照我们的代码）

| 现象（qing 的评价） | 代码里的根因 |
|---|---|
| 题目对 PPT 主题毫无指导 | Stage A 的 `production.style` 产出了，但下游只有 C 的 prompt 读它（`c_asset_requirements/stage.py:106`）。F/G 里配色全部写死：`f_layout/layouts.py:86-103` 的 `C_NAVY…`，`DEFAULT_FONT="Microsoft YaHei"`，`g_build/compile_ops.py` 回退 `#1E3A8A/#0F172A`。**主题这条线在 A 之后就断了。** |
| 版型只有一套 | `CANONICAL_LAYOUT_IDS` 只有 10 个，每个只有一种几何。`select_deck_layouts()` 用多样性 gate 硬换版式（第 2/3 遍：「换任意一个没用过的 TEXT_CAPABLE 版式」）。 |
| 对比页、大数字页半页空 | 多样性那一遍会把内容不是「对比」或「数字」的页强行换成 `comparison`/`big-number`；版式不知道内容有多少，QA 也没有空白检测。SlideSpeak 管这个叫 underflow。 |
| 文字高度模板化 | B 的 prompt（`b_slide_plan/prompt.py`）只要 title/goal/key_points，没有页面角色、文字结构、密度、讲者备注。所以每页都是「标题 + 3-4 条要点」。 |
| T02 海绵城市配了一张黄色告示牌 | `d_retrieve/image_rules.py` 只看元数据（标题、分类、国家），没有像素级的「图里文字占比」或图文相关度过滤。 |

---

## 1. 候选项目一览（已核实：存在、星数、协议、最后推送时间）

| 项目 | ★ | 协议 | 最后推送 (UTC) | 语言 | 相关度 |
|---|---|---|---|---|---|
| [hugohe3/ppt-master](https://github.com/hugohe3/ppt-master) | 79k | **MIT** | 2026-11-20 | Python+SVG→原生 PPTX | ★★★★★ 风格/模式/学术风格书/版式库/页面节奏 |
| [presenton/presenton](https://github.com/presenton/presenton) | 12k | **Apache-2.0** | 2026-11-20 | TS + FastAPI | ★★★★★ 16 套模板、约 400 个版式、主题 token、确定性主题角色代码、版式选择 prompt |
| [SlideSpeak/slide-design-skill](https://github.com/SlideSpeak/slide-design-skill) | 41 | **MIT** | 2026-06-25 | TS | ★★★★★ 跟我们病一样：underflow 检测 `occupancy.ts`、版式容量契约、密度档、反 AI 腔 lint |
| [icip-cas/PPTAgent](https://github.com/icip-cas/PPTAgent)（v0.2.0 / v1.1.38 DeepPresenter） | 5.3k | **MIT** | 2026-11-16 | Python | ★★★★ 从参考 PPT 归纳版式 + 每个槽位的 `suggested_characters`；layout_selector/editor prompt；PPTEval |
| [zarazhangrui/frontend-slides](https://github.com/zarazhangrui/frontend-slides) | 33k | **MIT** | 2026-06-23 | JS/HTML | ★★★★ 12 个预设 + 34 个 bold 模板，自带**选主题索引格式**（mood/tone/formality/density/scheme/best_for/avoid_for）+ 字体搭配表 + 「AI 通病」禁用表 |
| [allweonedev/presentation-ai](https://github.com/allweonedev/presentation-ai) | 3.1k | **MIT** | 2026-06-05 | TS | ★★★★ 38 套主题 token；**文字结构词表** `layout-catalog.ts`（BULLETS/BOXES/STEPS/TIMELINE/CYCLE/PYRAMID/COMPARE/BEFORE-AFTER/PROS-CONS/STATS…） |
| [Gabberflast/academic-pptx-skill](https://github.com/Gabberflast/academic-pptx-skill) | 1.6k | **MIT** | 2026-07-14 | Markdown | ★★★★ 学术页面角色、action title、ghost-deck 测试、每页 ≤40 字、引用规范 |
| [alchaincyf/huashu-design](https://github.com/alchaincyf/huashu-design) | 30k | MIT | 2026-11-21 | HTML/JS | ★★ 设计流程和审稿规范（偏 HTML、视频），可参考 `references/slide-decks.md`、`typography.md` |
| [HKUDS/Paper2Slides](https://github.com/HKUDS/Paper2Slides) | 3.9k | MIT | 2026-05-20 | Python | ★★ 整页出图（重画图表），**违反我们的不编造原则**；只借鉴 `STYLE_PROCESS_PROMPT`（自定义风格 → JSON） |
| [barun-saha/slide-deck-ai](https://github.com/barun-saha/slide-deck-ai) | 379 | MIT | 2026-10-26 | Python | ★ 4 个 pptx 母版，按索引选版式，思路简单 |
| [para-lost/AutoPresent](https://github.com/para-lost/AutoPresent) | 181 | MIT | 2025-05-26 | Python | ★ SlidesLib 加评测（reference-free），可当评测参考 |
| [MartinPacker/md2pptx](https://github.com/MartinPacker/md2pptx) | 516 | MIT | 2026-11-09 | Python | ★ 基于母版的 md→pptx，卡片版式可参考 |
| [Anionex/banana-slides](https://github.com/Anionex/banana-slides) | 16k | **AGPL-3.0** ⚠ | 2026-11-21 | Python | ✗ 整页 AI 出图，协议和原则都不合 |
| veasion/AiPPT, SmartSchoolAI/ai-to-pptx, YOOTeam/ChatPPT-* | — | **GPL/AGPL** ⚠ | — | — | ✗ 不抄 |
| sligter/LandPPT | 3.6k | NOASSERTION ⚠ | 2026-11-20 | — | 协议不明，不抄 |
| [material-foundation/material-color-utilities](https://github.com/material-foundation/material-color-utilities) / Python 移植 [T-Dynamos/materialyoucolor-python](https://github.com/T-Dynamos/materialyoucolor-python) | 2.3k / 79 | Apache-2.0 / MIT | 2026-11 / 2026-08 | — | ★★★ 从一个种子色生成色调盘，保证对比度 |
| [obskyr/colorgram.py](https://github.com/obskyr/colorgram.py) | 472 | MIT | 2021 | Python | ★★ 从主图提取主色（也可以直接用 Pillow 的 `quantize`，零依赖） |
| [qdrant/fastembed](https://github.com/qdrant/fastembed)（已是我们的依赖） | 3.3k | Apache-2.0 | 2026-11-18 | Python | ★★★★ 自带 `Qdrant/clip-ViT-B-32-vision/-text`（MIT，0.34+0.25GB，ONNX，CPU 可跑），可做零样本「告示牌/logo/文字图」判别和图文相关度打分 |
| [RapidAI/RapidOCR](https://github.com/RapidAI/RapidOCR) | 8.6k | Apache-2.0 | 2026-11-21 | Python/ONNX | ★★★★ 文字框面积占比（ppt-master `scripts/image_ocr.py` 也用它） |
| [christophschuhmann/improved-aesthetic-predictor](https://github.com/christophschuhmann/improved-aesthetic-predictor) | 1.3k | Apache-2.0 | 2024 | Python | ★★ CLIP-L/14 + MLP 美学分（较重，选做） |
| slidev / marp-core / reveal.js | 50k/1.2k/73k | MIT | — | — | ★ 它们是 HTML 主题，跟 COM 无关；只能借色板和字体搭配当灵感 |

没找到或已改名：`limhenry/paper2ppt`、`fengzhizi715/ChatPPT`、`AutoPresent/AutoPresent`（正确的是 para-lost/AutoPresent）、DocPres（没有对应的开源仓库）。「Gamma 开源替代」实际就是 presenton 和 allweone。

---

## 2. 逐项：抄什么、落到哪、协议、工作量

### 2.1 主题推导（话题 → 配色/字体/背景/母题）

**A. presenton：主题 token 结构 + 确定性主题代码**（Apache-2.0，可以直接拿 Python 来改，需要保留 NOTICE）
- 数据：`templates/*/template.json` 里的 `theme`，一共 16 套，色值字段是 `primary/background/card/stroke/background_text/primary_text/graph_0..9`，再加 `fonts`。
- 代码：`servers/fastapi/templates/v2/theme.py`
  - `contrast_ratio()`、`_repair_text_color()`：文字和背景对比度不足 4.5:1 时自动换色。
  - `_complete_graph_colors()`：从主色出发按 0.137 色相步进补齐 10 个图表色。
  - `_surface_variant()`：卡片色或描边色离背景太近时自动拉开。
  - `select_theme_roles_deterministically()`：从一组色里分配角色。
- 落点：新建 `v01/src/pptv01/theme/tokens.py`（`ThemeTokens` 数据类和上面几个函数），并把 `layouts.py` 和 `compile_ops.py` 里写死的 `C_*` 全部改成从 token 取。
- 工作量：中（约 1 天）。函数是纯 Python，直接移植。

**B. frontend-slides：主题库的选择索引格式**（MIT）
- 数据：`bold-template-pack/selection-index.json` 每条有 `slug/name/tagline/mood[]/tone[]/formality/density/scheme/best_for/avoid_for`。这个格式非常适合让 LLM 按话题选主题。`STYLE_PRESETS.md` 里有 12 套预设（hex 色、标题/正文字体、标志性元素）、字体搭配表和 **DO NOT USE**（Inter/Roboto 当标题、#6366f1 靛紫、全居中、清一色卡片网格）。
- 落点：`v01/themes/index.json`（照抄这个索引的字段格式），每个主题再配一份 `v01/themes/<id>.json` 存 token。
- 工作量：小。

**C. ppt-master：风格和论证模式分开选，外加学术风格书**（MIT）
- `skills/ppt-master/references/visual-styles/_index.md`：19 种视觉风格（swiss-minimal、editorial、blueprint、data-journalism、ink-wash 等），**不带 HEX**，只描述形状语言、装饰密度、留白节奏和字体气质。
- `references/modes/_index.md`：5 种论证模式（pyramid/briefing/narrative/instructional/showcase），**跟视觉风格正交**。
- `templates/styles/academic-research/templates/design_spec.md`：学术风格书，包括页面角色表（研究问题 / 前人工作 / 贡献 / 方法 / 数据 / 结果 / 对比消融 / 解释 / 局限 / 结论）、证据纪律、「装饰几乎为零」，以及「图像只用作证据或装置，禁止装饰性概念图」。最后这条跟我们的不编造原则高度一致。
- `references/plan-core.md` §颜色：60-30-10 比例，正文对比度至少 4.5:1；行业锚色只当回忆提示（金融 #003366、科技 #1565C0、医疗 #00796B、政务 #C41E3A），不当默认值。
- 落点：
  - A 阶段：`production.style` 拆成 `visual_style`、`argument_mode`、`reading_mode`。
  - 新增 A2「设计读取」：LLM 从主题索引里选 `theme_id`，必须给出理由和 2 个备选。
- 工作量：小到中，主要是改 prompt。

**D. 用一张主图提取配色（选做）**
- 用 Pillow `Image.quantize(5)` 或 colorgram.py（MIT）取主色，再用 materialyoucolor-python（MIT）的 `TonalPalette` 生成色阶。
- 规则：只允许拿来**微调所选主题的 accent**，并且必须过对比度检查。主图必须是 D 阶段已经核实过来源的真实图片，不能为了配色去找图。
- 落点：在 F 阶段开头调 `theme/derive.py: accent_from_image()`。工作量小。

**E. COM 兼容**
- 主题 token 最终有两条去路：
  1. 每个元素的 `color_hex` 和 `font`：现有的 `DeckOps` 已经支持。
  2. 可选写进母版主题色：`Presentation.SlideMaster.Theme.ThemeColorScheme.Colors(msoThemeAccent1..6).RGB`，以及 `ThemeFontScheme`。这样用户在 PowerPoint 里换主题色时整套跟着变，可编辑性更好。
- 字体必须是 Windows/Office 自带或可嵌入的：
  - 中文：微软雅黑、等线 DengXian、宋体、黑体、楷体、仿宋。开源可嵌入的有思源黑体/宋体（OFL）、霞鹜文楷（OFL）。
  - 英文：Segoe UI、Calibri、Georgia、Cambria、Bahnschrift。
- doctor 要检查每个主题的字体在本机是否存在，缺了就按预设链回退。

### 2.2 版式库与内容自适应选版式

**A. presenton：版式 JSON 和选版式 prompt**（Apache-2.0）
- `templates/*/template.json` 的 `layouts[]` 一共约 400 个（civic 29、dynamic 32、mosaic 34…）。每个版式有 `id`、一句自然语言 `description`，组件坐标基于 1280×720 画布。我们可以把**几何**换算成英寸，作为版式变体的灵感来源。注意：模板里的 `static/*.png` 图片和字体文件不要拿。
- `servers/fastapi/utils/llm_calls/generate_presentation_structure.py` 的选版式规则：
  - 有表格时只能选表格或图表版式；数字表格的 n 列对应 n-1 个图表。
  - 内容里没有图片就不选带图版式。
  - 相邻页尽量不同，但**允许按需重复**。
  - 用途映射：开场/结尾→标题版式，流程→流程版式，对比→并排版式，数据→图表版式，概念→图文版式，要点→强调版式。
- 落点：F 阶段的 `_score_layouts_for_page()` 规则表。工作量小。

**B. PPTAgent v0.2.0：版式容量 schema**（MIT）
- `pptagent/induct.py` 把参考 PPT 按视觉相似度聚类成版式，并给每个元素生成 schema：类型、数量（`default_quantity`），以及**文字元素的 `suggested_characters` 区间**。
- `roles/layout_selector.yaml` 选版式时比对三件事：元素个数、字数区间、图片相关度，相关度低就选纯文字版式。
- `roles/editor.yaml` 写内容时严格遵守 `suggested_characters`，不编造缩写和事实。
- 落点：给我们 10 个以上版式的每个变体写一份容量契约（见 2.2C），并把字数区间写进 E/F 的 prompt。工作量中。

**C. SlideSpeak：版式容量契约、密度档和 underflow 检测**（MIT，最对症）
- `docs/specs/2026-06-04-layout-fit-model.md`：
  - 每种版式声明槽位（kind 取 `line/prose/items/stat/table/chart/media`），每个槽位声明容量 `{min, ideal, max}`。
  - 版式归属一个「家族」，并写明超出容量时的处理：同家族换高容量变体，或者续页。
  - 原则：**内容不够时缩小内容簇，绝不拉伸**；**绝不靠缩字号塞内容**。
- `engine/occupancy.ts`（359 行纯函数）：
  - `scoreOccupancy`：内容区里最大的竖向空白带超过 230/1080（约 21%）就判为欠填。
  - `scoreCellOccupancy`：卡片内部空洞超过 max(180px, 0.45×高)，或者文字覆盖率低于 3.5%，都判为空卡片。
  - editorial 密度档豁免。
- `engine/density.ts`：`editorial/balanced/data-dense` 三档是**版式语义**，不是字号。
- `engine/divergence.ts`：两份 deck 的版式集合用 Jaccard 比，序列用 LCS 比，判断是不是「同一骨架换皮」。可以直接用来检测 T02/T12/T26/T36 之间的雷同。
- 落点：
  - H 阶段新 gate `fill_rate` / `underfill`：把 `occupancy.ts` 移植到 `h_qa/geometry.py`。输入用 `page_design.json` 的元素矩形，或者 COM `measure.py` 量出的真实文字框。
  - F 阶段 `layouts.py`：每个版式加 `capacity` 和 `sparse_variant`。
  - 新增 `grading/divergence.py`。
- 工作量：中（约 1.5 天）。算法纯函数，移植很直接。

**D. ppt-master：版式目录和页面节奏**（MIT）
- `templates/layouts/layouts_index.json`：
  - `presentation_core` 有 20 种页型：title_slide、section_header、two_content、comparison、content_caption、picture_caption、hero_statement、editorial_split、three_card、kpi_dashboard、process_timeline、data_story、title_picture、two_picture_caption、screenshot_focus、chart_insight、table_summary 等。
  - 另有 `presentation_core_43`（学术和教室常用 4:3）、`report_core`（13 种，含 agenda、matrix_2x2、appendix）、`editorial_bleed`（10 种出血图版式，含 quote_over_image、triptych、image_grid_four）。
- `plan-core.md`：每页打 `anchor/dense/breathing` 节奏标签，整份 deck 做节奏检查。封面必须有「钩子」，结尾必须有带结论的收束，不能是空的「谢谢」。
- 落点：
  - 第一步把我们的版式从 10 种扩到约 18 种，见 §3.3 清单。
  - B 阶段给每页加 `rhythm`。
- 工作量：每个新版式约 1-2 小时，几何加 fake-COM 测试。

**E. allweone：文字结构族**（MIT）
- `src/lib/presentation/layout-catalog.ts`：BULLETS(basic/numbered/arrow)、ICONS、BOXES（14 种 boxType）、ARROWS、STEPS、TIMELINE、CYCLE、PYRAMID/漏斗、STAIRCASE、COMPARE、BEFORE-AFTER、PROS-CONS、CIRCULAR-GRID、STATS（plain/circle/bar/dot-grid）、TABLE、CHART、QUOTE、CALLOUT。
- 规则：每页只放一个主组件；信息图类最多 5 项，多出来的要合并。
- 落点：B/E 阶段给每页产出一个 `text_structure` 字段，用受控词表，F 按它选版式家族。这是「版式随内容形态走」的关键中间层。工作量小（主要是 prompt 和 schema）。

### 2.3 写法去模板化

| 来源 | 抄什么 | 落点 |
|---|---|---|
| academic-pptx-skill `content_guidelines.md`（MIT） | 1) **Action title**：标题写成结论句，不写「研究方法」这种标签。2) **Ghost-deck 测试**：只读标题也能读通整个论证。3) 正文约 40 字以内，要点只做定向提示。4) 一页只放一个证据物。5) 图放左、解读放右。6) 三种叙事脊柱：SCR、漏斗+答案、答案先行。 | B prompt + `textcheck.py` |
| ppt-master `academic-research/design_spec.md`（MIT） | 学术**页面角色表**（10 种角色，每种有沟通任务、证据义务、构图倾向）；观察、推断、推测分开写；局限单独成页 | B 输出 `role` 字段，F 按角色选家族 |
| ppt-master `plan-core.md` 的 reading mode 表 | `text`（阅读型，偏 dense）、`balanced`（默认）、`presentation`（演讲型：页上只放关键词，解释写进**讲者备注**） | A 产出 `reading_mode`；G 的 `set_notes` 写真正的讲者稿，而不是现在的「planning summary」 |
| SlideSpeak `quality-lint.ts` + anti-slop spec（MIT） | 机器可检的 AI 腔：空话词表（「赋能」「助力」「全方位」「深度融合」这类中文对应词需要我们自己补）、「Step 1/Phase 01」这类泛标签、没来源的精确数字、整份 deck 同一密度、每页都有 eyebrow 小标签、主题标签式标题 | `textcheck.py` 加 lint（先只告警） |
| PPTAgent `editor.yaml` | 按槽位 `suggested_characters` 改写；不编造缩写和事实；实体名保留原文 | E/F 的填充 prompt |
| presenton「Don't select image layout unless content contains image」 | 跟我们的不编造原则一致 | F 规则 |

**多样性靠结构，不靠随机**：每页有 `role × text_structure × rhythm` 三个维度，再规定**同一 text_structure 不能连用、bullets 占比不超过 40%**。这比现在的 `layout_diversity`（单看版式 id）更接近 qing 说的「文字有特色」。

### 2.4 配图相关度与质量过滤（T02 黄牌子问题）

| 方案 | 怎么做 | 依赖 / 协议 | 成本 |
|---|---|---|---|
| **CLIP 零样本（首选）** | fastembed 的 `Qdrant/clip-ViT-B-32-vision` + `-text`（我们已经在用 fastembed）。图片向量跟几组负类 prompt 比：「a sign with text」「a logo」「a screenshot of text」「a document page」「a map legend」「a poster」；跟正类比：asset requirement 的 purpose + target 英文描述。负类分数最高并且超过正类一定幅度就拒，同时把图文余弦相似度作为排序分。 | fastembed Apache-2.0，模型 MIT，约 0.6GB，CPU 可跑 | 小到中 |
| **OCR 文字面积占比** | RapidOCR（ONNX，Windows CPU 可跑）算文字框面积占图片面积的比例。超过 15-20% 且请求类型是 `image`（照片）就拒；`diagram/chart` 类型放宽。ppt-master `scripts/image_ocr.py` 有现成的扫描代码可参考。 | Apache-2.0，约 15MB 模型 | 小 |
| 美学分（选做） | improved-aesthetic-predictor：CLIP-L/14 + MLP，低于约 4.5 降权 | Apache-2.0，较重 | 中，放到后面 |
| 现有的视觉 LLM 检查 | `image_rules.py: inspect_image_with_vision()` 保留，作为兜底复核 | — | — |

落点：
- D 阶段新增 `d_retrieve/pixel_rules.py`，跑在元数据规则之后、下载之后，结果写进 `rejections` 并带原因。
- H 阶段 gate `no_text_only_visuals` 复查。
- 网络约束：模型第一次运行要从 HF 下载，必须走现有的 fastembed 超时、缓存和代理 doctor 路径。

---

## 3. 融合方案（跟 COM 渲染和不编造原则兼容）

```
A plan ──► A2 设计读取 (LLM 选 theme_id + visual_style + argument_mode + reading_mode，附理由)
             │  规则兜底：domain 关键词 → 主题族（学术/工程/城市/生态/医学/人文/数据）
             ▼
B slide plan ── 每页新增: role(学术角色表) · text_structure(受控词表) · rhythm(anchor/dense/breathing)
             │           · action_title · notes_intent
             ▼
C/D 素材 ── 新增 pixel_rules: CLIP 负类拒绝 + OCR 文字占比 + 图文相似度排序
             ▼
E mapping ── 槽位字数预算(suggested_characters) 写进 prompt
             ▼
F layout ── ThemeTokens 注入（替换 C_* 常量）
          · 内容形态特征 → 版式家族 → 家族内按容量选变体（sparse/normal/dense）
          · 多样性只在“同分候选”之间做，绝不把不匹配的家族硬塞进来
          · 可选：主图取色微调 accent（过对比度）
             ▼
G build ── 颜色/字体从 token 取；可选写 SlideMaster ThemeColorScheme；set_notes 写讲者稿
             ▼
H QA ──── 新 gate: fill_rate/underfill(occupancy 移植) · theme_contrast(≥4.5) · text_lint(告警)
          · deck_divergence(跨题 Jaccard/LCS，基准集回归用)
```

### 3.1 主题库：首批 8 套，以学术为主
每套是一份 JSON：`id, name, mood[], tone[], formality, density, scheme(light/dark), best_for, avoid_for, colors{primary, accent, background, surface, card, stroke, text, text_muted, on_primary, graph_0..5}, fonts{heading, body, data, fallbacks[]}, shape{corner_radius_in, rule_weight_pt, card_style}, motif{type: none|rule|band|grid|corner, ...}, cover_variant, section_variant`。

建议首批（色值以 presenton、allweone、ppt-master 的为种子，再过对比度检查）：
1. `academic-swiss`：白底、深青强调色、思源黑体或等线；细线，无装饰（默认学术风）
2. `academic-serif`：米白底 #F5F1E8、#0F4C5C，标题用宋体或思源宋体（SlideSpeak academic tokens）
3. `eco-green`：生态、城市、海绵城市（presenton verdant #285F20 系）
4. `urban-blueprint`：城市规划和工程，深蓝底配网格母题（ppt-master blueprint 风格）
5. `data-journalism`：数据驱动，中性灰配单一高亮色
6. `medical-teal`：医学、生命科学（#00796B 系）
7. `humanities-warm`：人文、历史，暖色加衬线标题
8. `tech-dark`：AI 和计算机，深色底（presenton dynamic、pulse 系）

**选择规则**：
- LLM 读主题索引的 `best_for/avoid_for`，输出 `theme_id`、理由和备选。
- 校验器保证 `theme_id` 存在于索引中，否则回退到关键词规则。
- 同一个基准集里 4 道题不能全是同一主题（divergence 检查）。

### 3.2 内容形态 → 版式家族（确定性）
特征来自 B/E 阶段：`n_points`、平均字数、`text_structure`、真实图片数和每张的宽高比、有没有 chart/table 数据、有没有数字型 fact、有没有引语、`role`、`rhythm`。

| 形态 | 家族 | 稀疏变体（防半页空） |
|---|---|---|
| 1 个结论 + 1 个数字（有来源） | big-number | 数字和说明居中成簇，下面放出处；不拉伸到整页 |
| 2 方对比，每边 ≥2 条 | comparison | 每边 ≤1 条时**降级**成 quote 或 statement，不硬做对比 |
| 有序 ≥3 步 | timeline / steps | 3 步用横排，≥5 步用竖排或双列 |
| 3-4 个并列要点 | three-card / boxes | 2 点用 two-content |
| 1 张横图（宽高比 ≥1.3） | image-left/right，或 full-bleed 加遮罩 | 文字少时走 full-bleed |
| 2-4 张图 | two-picture-caption / image-grid / triptych | — |
| 竖图 | picture-caption（窄图配宽文字） | — |
| 表格 / 图表数据 | table-summary / chart-insight | — |
| 引语 / 结论句 | quote / hero-statement | — |
| 结构性页面 | cover / section / agenda / closing-takeaway / references | — |

### 3.3 版式扩充清单（10 → 约 18）
新增：`agenda`、`three-card`、`two-content`、`steps-horizontal`、`hero-statement`、`picture-caption`（竖图）、`two-picture-caption`、`chart-insight`、`table-summary`、`closing-takeaway`、`references`。
每个版式都要写：
- 槽位和容量 `{min, ideal, max}`
- `sparse/normal/dense` 三种几何变体
- 跟随主题 token 的母题位置（rule、band、corner）

几何可以参考 ppt-master 的 `presentation_core` 和 presenton 的版式 description，换算成我们 13.333×7.5in 的网格。

### 3.4 填充率 gate（移植 occupancy.ts）
- 页面级：内容区（扣掉标题栏和页脚）里最大竖向空白带超过 21% 高度就失败，`rhythm=breathing` 和 `anchor` 页豁免。
- 卡片级：卡片内部空洞超过 max(1.25in, 0.45×卡片高)，或者文字覆盖率低于 3.5%，都失败。
- 失败时按顺序处理：先换同家族的 sparse 变体，再降级家族（例如 comparison 降成 quote），最后标记。**不准放大字号去填空白。**
- 数据来源：先用 `page_design.json` 的元素矩形（F 阶段就能拦）。Windows 上再用 COM `measure.py` 的真实文字框复核。

### 3.5 不编造约束（继续有效）
- 主题和母题只用几何形状和颜色，**不生成插画、不 AI 出图**。SDXL 背景按原计划留到后面，单独评估。
- 主图取色只用 D 阶段核实过来源的图片。
- big-number 版式只接受有 `source_refs` 的数字。没有数字就降级成 statement，不准编数字去凑版式。
- 讲者备注只能用 B/E 里已有的内容和来源，不能新增事实。

---

## 4. 第 6 轮建议拆分（按优先级，每项都是能单独 PR 的小 HANDOFF 条目）

| # | 条目 | 抄自 | 阶段/文件 | 验收 | 估时 |
|---|---|---|---|---|---|
| R6-1 | **ThemeTokens 抽离**：`C_*` 和 `DEFAULT_FONT` 全部改从 token 取；先只有 `academic-swiss` 一套，渲染结果跟现在逐像素一致 | presenton `theme_data.py`/`theme.py` | 新 `theme/tokens.py`；`f_layout/layouts.py`、`g_build/compile_ops.py` | 全量 pytest 通过；grep 不到写死的 hex（白名单除外）；对比度函数单测 | 0.5-1d |
| R6-2 | **填充率 gate**：移植 occupancy（页面级 + 卡片级），先告警再阻断 | SlideSpeak `occupancy.ts` | `h_qa/geometry.py`、`grading/gates.py` | smoke5 的对比页和大数字页**能被抓出来**（先跑旧产物证明 gate 有效） | 0.5-1d |
| R6-3 | **多样性 pass 不再硬塞不匹配的家族**；comparison/big-number 加入降级规则和 sparse 变体 | presenton 选版式规则 + SlideSpeak 容量契约 | `f_layout/layouts.py: select_deck_layouts/_score_layouts_for_page` | R6-2 在 4 道题上全部通过；`layout_diversity` 仍然通过，或者阈值随家族数下调 | 1d |
| R6-4 | **配图像素过滤**：CLIP 负类和图文相似度，加 OCR 文字占比 | fastembed CLIP、RapidOCR、ppt-master `image_ocr.py` | 新 `d_retrieve/pixel_rules.py`；H gate `no_text_only_visuals` | T02 的黄牌子被拒，有拒绝原因；有超时、缓存和离线降级 | 1d |
| R6-5 | **主题库 8 套 + A2 设计读取**：索引 JSON、LLM 选择、关键词兜底、字体存在性 doctor | frontend-slides `selection-index.json`、ppt-master visual-styles/modes、allweone/presenton 色值 | `v01/themes/*.json`、`a_plan/prompt.py` 或新 `a2_design`、`doctor.py` | 4 道 smoke 至少分到 3 套不同主题；每套对比度 ≥4.5；缺字体时回退 | 1-1.5d |
| R6-6 | **B 页面角色 + 文字结构 + 节奏 + action title**；G 写真正的讲者备注 | academic-pptx-skill、ppt-master academic design_spec 和 plan-core、allweone layout-catalog | `b_slide_plan/prompt.py`、`contracts.py`、`g_build/compile_ops.py` | ghost-deck：只读标题能读通；bullets 占比 ≤40%；同一 text_structure 不连用；备注非空且无新事实 | 1d |
| R6-7 | **版式扩充**（先加 6 个：three-card、two-content、steps-horizontal、hero-statement、picture-caption、chart-insight），每个带容量和 3 种变体 | ppt-master `presentation_core`、presenton 版式描述 | `f_layout/layouts.py`、`capacity.py` | fake-COM 测试；每个版式在 min/ideal/max 三档样例上都过 R6-2 | 1.5-2d |
| R6-8 | **AI 腔 lint**（中文空话词表、泛标签、无来源数字、密度单一），先只告警 | SlideSpeak `quality-lint.ts` | `textcheck.py` | 4 道 smoke 出 lint 报告 | 0.5d |
| R6-9 | **跨题雷同度**：版式集合 Jaccard + 序列 LCS，写进冒烟汇总 | SlideSpeak `divergence.ts` | `grading/divergence.py`、smoke 汇总 | 4 道题两两之间都给出分数，有阈值告警 | 0.3d |
| R6-10（选做） | 主图取色微调 accent，可选写进 SlideMaster ThemeColorScheme | Pillow quantize、materialyoucolor-python | `theme/derive.py`、COM executor | 用户在 PPT 里换主题色，整套跟着变 | 0.5-1d |

顺序理由：
- R6-1 到 R6-4 先修「看得见的错」：半页空白、黄牌子、颜色写死。改动小，每一项都能单独验证。
- R6-5、R6-6 解决「主题无指导、文字模板化」的根因。
- R6-7 扩版式要先有 R6-2 的容量契约当安全网，否则只会多出更多半页空。
- 冒烟仍由反重力自己跑，并且每一项都要在 smoke5 的旧产物上先证明 gate 能抓到问题。

---

## 5. 协议备注
- **可直接抄代码或数据**：ppt-master、PPTAgent、frontend-slides、allweone、academic-pptx-skill、SlideSpeak、slide-deck-ai、md2pptx、Paper2Slides、huashu-design（均为 MIT，需要保留版权声明，记进 `v01/NOTICE.md`）；presenton、fastembed、RapidOCR、material-color-utilities（均为 Apache-2.0，需要保留 NOTICE，改过的文件要注明）。
- **不要拿**：presenton 模板里的 `static/*.png/.ttf`（素材和字体有各自的授权）；frontend-slides 里 Clash Display/Satoshi（Fontshare 授权）这类商用受限字体。Google Fonts 大多是 OFL，但 Windows 上不一定装了，以 doctor 的检查结果为准。
- **排除**：banana-slides（AGPL）、veasion/AiPPT、SmartSchoolAI/ai-to-pptx、YOOTeam/*（GPL/AGPL）、LandPPT（协议不明）、quarkdown（GPL）。
- **anthropics/skills**：GitHub API 显示没有协议（none），不要抄。
