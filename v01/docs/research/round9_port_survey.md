# 第 9 轮移植调研：版式怎么选、主题怎么选、分组怎么拆成卡片／分栏

调研日期：2026-11-21（美东时间）。只读调研，没有推送，也没有写 HANDOFF，等用户批准方案后再写。
基线：`feature/v01-office-renderer` @ `d551e31`（第 8 轮评 B），worktree 在 `/workspace/r8wt`。
源码克隆在 `/workspace/research/src/`。本轮新克隆的有：`presenton_v05`（presenton 的 `v0.5.15-beta` 标签，那一版还是 Zod 版式）、`tristan-mcinnis_pptx-from-layouts-skill`、`xiongwenhao112_ppt-template-fill`、`addsumtech_slides_maker`。
用户的新规矩（pt210u）：每次写 HANDOFF 之前都要先调研成熟方案，按「照抄 → 复用 → 融合 → 超越」来写任务书，不让反重力自己发明规则、再靠测试打补丁。

---

## 0. 结论先行

**第 8 轮的三个病，根子是同一个：F 阶段用 4040 行 Python 现场「算」版式。** 选哪个版式（`_score_layouts_for_page` 打分）、能不能分栏（`_column_cluster_underflows` 加 0.52 的比例）、怎么摆（每个版式一个手写 builder）全在代码里，所以每修一次就多一个阈值。

调研的所有成熟项目都没有这么做。它们的共同做法是：

1. **版式是数据，不是代码。** 版式库里每个版式声明自己有哪些槽位，每个槽位写清容量（最少／最多字数、最少／最多条数）。
2. **由模型（LLM／agent）从版式目录里挑版式，并给出理由。** 代码只做硬校验：条数和组数对得上、字数落在范围里、需要图的版式确实有图。校验不过就报错重试，不靠打分换版式。
3. **组数不同就是不同的版式。** 两组用「两栏」，三组用「三卡」，四组用「四卡」，每个都是独立的版式条目。presenton 的 v2 模板里大量版式写的是 `min_children == max_children == 2`；pptx-from-layouts 里有 `column_2`、`column_3`、`column_4`、`column_5` 四个母版版式。
4. **内容少时不把内容拉开铺满。** 二选一：内容簇按自身高度摆在上中部（SlideSpeak 叫 content-sized island），或者换成槽位更小、节奏是「留白」的版式（ppt-master 的 `page_rhythm: breathing` 和 slot capacity 规则）。
5. **主题由人或模型选，没有一家用关键词打分。** presenton 和 PPTAgent 由用户或 agent 从带描述的模板列表里挑；ppt-master 由 strategist 读风格索引后决定，并请用户确认；slides_maker 用「领域 → 候选 → 一票否决（anti-pick）→ 四个维度打分 → 写明输给了谁」的比稿流程。

**推荐主移植对象：presenton（Apache-2.0）的「版式目录 + 带容量的 schema + 逐页选版式」契约。** 另外融合：

- pptx-from-layouts 的「视觉类型决策顺序」和「按组数分版式」；
- PPTAgent 的 layout_selector 理由格式和 `suggested_characters`；
- ppt-master 的 `presentation_core` 20 个版式名单、`page_rhythm`、slot capacity 规则；
- SlideSpeak 的「内容按自身高度成团」原则和「报错要带上可行的替代版式」；
- slides_maker 的主题比稿流程（design-by-topic）。

我们能超越的地方是证据和引用诚实门槛、真机 COM 实测的 occupancy 质检，以及 agent 请求／答案文件可审计。

---

## 1. 协议总表

| 项目 | ★（约） | 协议 | 最近提交 | 能不能抄 |
|---|---|---|---|---|
| presenton/presenton（现行 v2，以及 `v0.5.15-beta` 的 Zod 版） | 12k | **Apache-2.0** | 2026-11-20 | ✅ 可以抄代码和 prompt。要保留 LICENSE 和 NOTICE，改过的文件要注明改动。**不抄** `templates/*/static` 里的图片和字体（素材另有授权）。 |
| icip-cas/PPTAgent（v0.2.0、v1.1.38 DeepPresenter、现行 skills 版） | 5.3k | **MIT** | 2026-11-08 | ✅ 代码和 prompt 可以抄。**不抄** `templates/{thu,ucas,hit,cip}/source.pptx`：那是高校品牌模板，校徽和商标不在 MIT 范围内。 |
| hugohe3/ppt-master | 79k | **MIT** | 2026-11-20 | ✅ 可以抄。`templates/brands/*` 里是各公司的品牌（华为、腾讯、麦肯锡……），不抄。 |
| SlideSpeak/slide-design-skill | 41 | **MIT** | 2026-06-25 | ✅ 可以抄（第 7、8 轮已经引用过）。 |
| allweonedev/presentation-ai | 3.1k | **MIT** | 2026-06-05 | ✅ 可以抄。 |
| tristan-mcinnis/pptx-from-layouts-skill | 42 | **MIT** | 2026-08-30 | ✅ 可以抄。`templates/inner-chapter.pptx` 是演示模板，不建议直接用。 |
| xiongwenhao112/ppt-template-fill | 新项目 | **MIT** | 2026-07-17 | ✅ 可以抄。 |
| addsumtech/slides_maker | 680 | **MIT** | 2026-11-19 | ✅ 可以抄（参考文档和脚本）。 |
| MartinPacker/md2pptx | — | **MIT** | 2026-11-09 | ✅ 可以抄。 |
| barun-saha/slide-deck-ai | — | **MIT** | 2026-10-26 | 代码可以抄。它自带的 `Ion_Boardroom.pptx` 等是 Office 内置模板，不抄。 |
| frontend-slides、academic-pptx-skill、Paper2Slides、AutoPresent | — | MIT | — | 可以抄（见第 6 轮调研）。 |
| banana-slides、veasion/AiPPT、SmartSchoolAI、YOOTeam、quarkdown | — | **AGPL 或 GPL** | — | ❌ 排除。 |
| LandPPT、anthropics/skills | — | 协议不明或没有协议 | — | ❌ 排除。 |

---

## 2. 逐个项目：机制、文件路径、能照抄的部分、和我们的契合度、短板

### 2.1 presenton（Apache-2.0）——推荐的主移植对象

**它怎么做（三步，顺序很关键：先选版式，再按版式写内容）：**

1. **大纲。** `servers/fastapi/utils/llm_calls/generate_presentation_outlines.py`。
2. **逐页选版式。** `servers/fastapi/utils/llm_calls/generate_presentation_structure.py`。
   - 输入是版式目录。`templates/presentation_layout.py: PresentationLayoutModel.to_string()` 把它写成「### Slide Layout: i / Name / Description / Schema」的格式。
   - 输出是 `PresentationStructureModel{slides: List[int]}`，即每页一个版式下标。
   - `utils/get_dynamic_models.py:32` 规定输出长度必须正好等于页数（`min_length = max_length = n_slides`）。
   - prompt 分两种：
     - `GET_MESSAGES_SYSTEM_PROMPT` 规定按内容的用途选版式：开场和结尾用标题版式，流程用流程版式，对比用左右并列，数据用图表，关键结论用强调版式。它也鼓励多样，相邻两页尽量不同，但必要时允许重复。
     - `STRUCTURE_FROM_SLIDES_MARKDOWN_SYSTEM_PROMPT` 是硬规则：没有图就不选带图的版式，没有表就不选表格版式，n 列数值表对应 n−1 个图表。
3. **按选中版式的 schema 写内容。** `generate_slide_content.py`（v0.5 和现行版都在）。
   - 关键规则：「Strictly follow the max and min character limit for every property」「Number of items should not be more than max number of items… merge them」。
   - verbosity 用字数占上限的比例来控制：concise 约 ≤1/3，standard 约 2/3，text-heavy 约 ≥3/4。
   - 讲者备注和正文一起生成。

**版式定义有两代：**

- **v0.5（Zod）：** `servers/nextjs/presentation-templates/general/*.tsx`。每个文件导出 `layoutId`、`layoutName`、`layoutDescription` 和 `Schema`（zod 对象，用 `.min()`、`.max()` 限定字数和条数，`.meta({description})` 写槽位说明）。
  - 例子：`BulletWithIconsSlideLayout.tsx` 里，title 3–40 字，description ≤150 字，bulletPoints 1–3 条，每条标题 2–60 字、描述 10–100 字。
  - 12 个 general 版式的全部 min／max 我已经抽出来了，见附录 A。
- **v2（现行）：** `templates/<name>/template.json`，16 套模板、约 400 个版式。
  - 每个版式由若干 component 组成，每个 element 带 `position`、`size`（1280×720 px）、`font`、`max_length`、`min_length`。
  - 重复子项带 `min_children` / `max_children` / `min_items` / `max_items`。`templates/v2/schema.py:369-449` 把这些转成 JSON Schema（`maxLength`、`minItems`、`maxItems`）。
  - 实测：civic 模板 29 个版式里大量是 `min_children = max_children = 2`，也就是**组数固定，三组就是另一个版式**。

**主题：** 用户在界面里选模板，代码里没有自动选（`api/v1/ppt/endpoints/presentation.py:212 _resolve_requested_template`）。

**能照抄的：**

1. 「版式目录 → 文本」的格式（`to_string`）。
2. 两段选版式的 prompt（翻译后照搬结构）。
3. 写内容的 prompt 里关于 min／max 字数、条数上限要合并、verbosity 比例的规则。
4. schema 写法：每个槽位写 `min_length` / `max_length`，重复项写 `min_items` / `max_items`，并用 `description` 说明用途。
5. 「输出长度等于页数」的硬校验。

**和我们的契合度：高。**

- presenton 的「版式下标数组」可以直接变成我们的一个 agent 阶段，即新的 LS 阶段，请求和答案文件格式和 A–E、TH 一样。
- 我们的 B 已经产出 `groups`、`headline`、`key_points`，相当于 presenton 的大纲加内容，所以选版式放在 B 之后、F 之前。
- presenton v2 的坐标是 px，按 1280 px = 13.333 in（96 px／in）就能换成我们 `PageDesign` 的英寸，`DesignElement` 不用改。

**短板：**

- 渲染走 HTML 和 flex，空白靠浏览器排版吸收，所以它**没有** underflow 检测。这一块要靠 SlideSpeak 和我们自己的 occupancy 补。
- 选版式时要求多样，没有容量反馈。它的容量约束全压在写内容那一步。
- 主题不会自动选。

### 2.2 PPTAgent（icip-cas，MIT）

**它怎么做（以 v0.2.0 的 `pptagent/` 为准，v1.1.38 基本一样）：**

1. **从参考 PPT 归纳版式。** `induct.py`：
   - `category_split()` 用 `prompts/category_split.txt` 让模型挑出开场、目录、章节页、结尾这些结构页。
   - `layout_split()` 对剩下的内容页，按 `(slide_layout_name, content_type)` 分桶，再用图像 embedding 做相似度聚类，每簇取形状最多的一页当模板页，由视觉模型给版式起名（`ask_category.txt`）。
   - `content_induct()` 用 `roles/schema_extractor.yaml` 从模板页的 HTML 抽出元素 schema：每个元素有 `name`、`type`（text／image）、`data`（原文列表）。
2. **容量。** `presentation/layout.py:21-41`：
   - `Element.suggested_characters = max(len(i) for i in data)`，即参考稿原文的最大长度。
   - 「default quantity」等于原文条数。
   - 还有一个 `variable_length` 加 `vary_mapping`：按条数映射到不同的模板页，也就是「3 条就换 3 栏那页」。但我 grep 过，**shipped 的 induct 代码从来不生成这个字段**，6 套内置模板的 `slide_induction.json` 里也全是空的，等于死代码。
3. **逐页选版式。** `roles/layout_selector.yaml`：看元素数量、字数和 `suggested_characters` 是否匹配，图是否相关。输出 `{"reasoning": "...", "layout": "..."}`，理由要写清和其他候选比为什么胜出。
4. **写内容加编辑。** `roles/editor.yaml` 按 schema 写内容。要求条数尽量等于 default_quantity，字数遵守 `suggested_characters`，「不得编造来源里没有的信息」。`apis.py` 提供 `clone_paragraph`、`del_paragraph`、`replace_image` 等编辑操作，在克隆出的模板页上原地改。超长时 `layout.length_rewrite()` 按 `lengthy_rewrite.txt` 重写。
5. **主题。** `mcp_server.py:160-201` 的 `list_templates()` 返回每个模板的 `description.txt`，由 agent 调 `set_template(name)` 来选。**没有关键词。**

**能照抄的：**

1. layout_selector 的理由格式：带字数区间对比，并说明和最接近的落选版式比为什么胜出。
2. 「`suggested_characters` 取参考稿原文的最大长度」，可以拿来给我们的版式槽位定 ideal 值。
3. editor 里「不编造」「保留原文实体名」两条。
4. 结构页分类的 prompt。
5. 「按条数映射到模板页」的思路：它没做完，我们按这个思路做完。

**契合度：中。**

- 「编辑现成模板页」和 COM 很契合，因为 COM 能复制幻灯片并改文字。
- 但它依赖参考 PPT 和视觉模型做归纳，太重；而且内置的学术模板是高校品牌，不能用。
- 我们只取「schema 加选择器加理由」这一层。

**短板：**

- 条数不够时只是删掉段落，删了就留空，仍然会稀疏。
- `vary_mapping` 是死代码。
- 版式名由 VLM 自由起，不稳定。

### 2.3 ppt-master（MIT）

**它怎么做：**

- **版式名单。** `skills/ppt-master/templates/layouts/presentation_core/templates/design_spec.md` 定义了 20 个真正的 PowerPoint 版式（Layout）：title_slide、title_content、section_header、two_content、comparison、title_only、blank、content_caption、picture_caption、hero_statement、editorial_split、three_card、kpi_dashboard、process_timeline、data_story、title_picture、two_picture_caption、screenshot_focus、chart_insight、table_summary。
  - 每个都有 SVG 原型和 `{{PLACEHOLDER}}` 槽位，比如 `12_three_card: [PAGE_TITLE, CARD_1, CARD_2, CARD_3]`，`14_process_timeline: [PAGE_TITLE, STEP_1..4, KEY_MESSAGE]`。
  - `layouts_index.json` 里还有 editorial_bleed、moments_square 等。
  - 导出时生成**真实的 Master 和 Layout 加占位符**（`references/pptx-structure-interface.md`）。
- **页面节奏。** `references/strategist.md:269` 和 `executor-base.md:235-243` 要求每页必须打一个 `page_rhythm` 标签：
  - `anchor`：结构页；
  - `dense`：信息页，可以用卡片格、多栏、KPI、表格、图表；
  - `breathing`：低密度的冲击页，用裸文字、分隔线、留白或满版图片撑结构。
  - 还有一句：「Mechanical repetition comes from reusing one carrier and topology without a page job」。
- **槽位容量。** `pptx-structure-interface.md:67`：「a slot's bounds are a capacity, not a whitespace quota. When a page's content leaves a slot mostly empty, set its type at the top of the role's band, bring in the source's supporting detail, or map the page to a prototype with smaller slots」。这正好是稀疏页的三条正解：文字顶到槽位带的上沿、补来源里的细节、换槽位更小的原型。**没有一条是拉伸。**
- **风格选择。** `templates/styles/styles_index.json`（summary 加 keywords）和 `references/visual-styles/_index.md`（每种风格的 character 和 paired rendering）。由 strategist 结合内容决定，并请用户确认。`workflows/routing.md:111` 写明「Bare template/brand name… Never resolve to a local path or preselect」。

**能照抄的：**

1. 20 个版式的名单和每个版式的槽位清单，作为我们版式目录的骨架（我们现有 14 个，只需补齐）。
2. `page_rhythm` 三档的定义原文。
3. 槽位容量规则原文。
4. 「风格只管处理方式，不决定有哪些载体」的边界。

**契合度：高（理念层），低（代码层）。** 它的执行器是「LLM 直接写 SVG」，我们不走这条路。

**短板：** 工作流文档极长，是写给 Claude 用的技能说明，不是可调用的库；容量靠 LLM 自觉，没有机器校验。

### 2.4 SlideSpeak slide-design-skill（MIT）

**它怎么做：**

- `docs/specs/2026-06-04-layout-fit-model.md` 分四层：
  1. 版式契约：槽位的 `kind` 加 `{min, ideal, max}` 加密度档加 family 加溢出策略；
  2. 内容按自身高度成团（Bounded Intrinsic Content Island），**禁止** `flex:1`、`space-evenly`、`height:100%` 这类拉伸；
  3. 静态校验加裁决：超容量就换同 family 里容量更大的变体，或者分页，或者标记；
  4. 渲染后实测。
- 决定 2 是：「**LLM 选的 slide type 是有约束力的**，只要通过 schema 和预算校验」，裁决留到以后再做；过渡做法是「Validator-Fehler mit vorgeschlagenem Fallback-Layout」，即校验报错时附上建议的替代版式。
- 内容少于容量算合格，因为模板本身按内容大小排，稀疏页看起来是安静的。
- `engine/occupancy.ts`：页面级空白带检测（`VOID_MAX_PX`）加卡片内部检测。editorial 密度档豁免。
- 其中一段注释说：space-between 撑开的列表和正确的「等高行居中」用矩形分不出来，所以在**生成端**杜绝，不靠阈值去分辨。
- `skills/academic/layout-grammar.md`：学术 slide-type 表（motivation、research-question、method、result-figure……），每种列出必填和可选槽位。

**能照抄的：** 契约字段、「LLM 选型有约束力，加带替代建议的报错」、成团原则、occupancy 原值（第 7、8 轮已经抄了）、学术 slide-type 表。

**契合度：高。** 这是我们 R7、R8 已经引用的来源。**这次要补抄的是第 2、3 层的「谁来选」**：由 LLM 选，代码只校验，而不是我们现在这样代码打分。

**短板：** 项目小（41★），德文规格，渲染走 HTML。

### 2.5 allweone presentation-ai（MIT）

**它怎么做：** `src/lib/presentation/layout-catalog.ts` 的 `LAYOUT_REFERENCE` 是写给 LLM 的「XML 组件家族目录」，LLM 直接输出 XML，前端 React 和 flex 负责排版：

- 列表类：BULLETS、ICONS、BOXES（14 种 boxType）、ARROWS；
- 序列类：STEPS、TIMELINE、CYCLE、PYRAMID、STAIRCASE；
- 对比类：COMPARE、BEFORE-AFTER、PROS-CONS；
- 关系类：CIRCULAR-GRID、CONNECTED-CIRCLES；
- 数据类：STATS、TABLE、CHART。

每个家族都写了「Use these when…」。

**能照抄的：** 「内容关系 → 组件家族」的词表和每条的使用条件。用在 B 的 `groups` 上，给每组再标一个 `relation`：parallel、sequence、compare、cause、part_whole。

**契合度：中。** 我们不用 XML 和 flex，但它的词表可以当版式目录的「use_when」字段。

**短板：** 没有容量概念，主题由用户选。

### 2.6 pptx-from-layouts-skill（MIT）——最接近「母版版式 + COM」的路线

**它怎么做：**

- **画像，再写稿，再渲染。** `scripts/profile.py` 和 `profile_template.py` 读模板的母版版式，生成 `layout-catalog.md` 和 `config.json`（`templates/inner-chapter-config.json` 的 `layout_mappings`：`column_2 → layout_index 6 "column-2-centered"`、`column_3 → 35`、`column_4 → 31`、`column_5 → 27`……）。
- 写稿时每页标 `[HINT: layout]` 和 `**Visual: cards-3**`。
- `scripts/generate_pptx.py` 用 python-pptx 的 `slide_layouts[idx]` 建页，把内容填进占位符。
- **视觉类型的决策顺序。** `rules/visual-types.md`：
  1. 有顺序就用 `process-N-phase`；
  2. 有对比就用 `comparison-N`；
  3. 平行而离散就用 `cards-N`；
  4. 数字之间有张力就用 `data-contrast`；
  5. 有好引语就用 `quote-hero`；
  6. 真的是表格数据才用 `table`；
  7. 一句话结论用 `hero-statement`；
  8. 都不是就用 `bullets`。

  其中 **N 等于栏数，由内容决定**，每个 N 对应母版里独立的版式。
- **校验。** `generate_pptx.py:5586, 6193` 在栏数和 visual type 不一致时直接报错，并告诉你「Add N more column(s) or change visual_type」。
- `rules/columns.md` 规定每栏最多 4 条、全页最多 10 条，栏标题 2–3 个词。
- `rules/visual-types.md` 的 Content Length Guidelines 给每种类型定了字数上限，超了就拆页、精简，或者换类型。

**能照抄的：** 8 步决策顺序（原文）、「visual type 带 N」的命名、栏数不一致时的报错文案、每栏和每页的条数上限、「用母版真版式，不是在背景上叠文本框」的理念。

**契合度：高。** 它就是「组数决定版式」的现成实现，而且是母版路线，和我们的 COM 渲染器同一路子。

**短板：**

- `generate_pptx.py` 一个文件 6000 多行，整个 skill 两万多行。
- `content_fitter.py` 会缩字号，最低到 70%，违背我们「不缩字号」的规矩。
- 只有一套演示模板。
- 基于 python-pptx，不是 COM。

### 2.7 ppt-template-fill（MIT）

**它怎么做：**

- 「你的模板就是版式库」。`scripts/inspect_template.py` 和 `pptxkit.py:75 estimate_budget()` 给模板每页的文本槽估出 `max_chars` 和 `max_lines`（按全角字宽粗估），并识别装饰槽。
- `build_deck.py --scaffold` 生成 plan 骨架，每个槽预填「【待填】＋原文」，然后整页克隆，只替换文字 run。
- 生成前校验：「【待填】」残留或模板原文残留就报错阻断。
- `snapshot.ps1` 用 **PowerPoint COM** 逐页导出 PNG，给 agent 目检。

**能照抄的：** 「预填骨架，让漏填在结构上不可能发生」的答案文件骨架（我们 LS 和 B 的请求里可以带骨架），以及 COM 截图脚本的写法。

**契合度：中高。** 它是给中文「野生」模板用的，很多中文模板不用占位符，它照样能填。

**短板：** 只能整页克隆，表格行列数固定，超长只告警。

### 2.8 slides_maker（addsumtech，MIT）

**主题选择**（`skills/slide-maker/references/design-by-topic.md`）是一场比稿，不是查表：

1. 从批准过的内容里读出主题的领域，不是看标题字面（「做 AI 汽车的公司」是汽车／硬件加 AI，不是「科技」）；
2. 从领域表里列 2–3 个候选；
3. 碰到一票否决的 ANTI-PICK 就剔除；
4. 按领域、内容形态、正式度、情绪温度四个维度打分；
5. 选出一个，**并写明最接近的落选者，以及区分两者的那一句理由**。

记录格式是 `style pick: <preset> for <domain> · beat <rival> because <clause> · anti-pick avoided: <cliché>`。领域表大约有 20 个领域：医学、文化遗产、气候、数据、法律政策、中国文化……每行给出候选和反选。

**版式选择**（`form-selection.md`）同样要求：「NEVER record the first matching form」，每页先列 2–3 种形式，再按 tie-breaker 选，并记录胜出的理由；「cards 只适合平行、无序、等权的条目」。

**能照抄的：** 比稿流程和记录格式原文、领域加反选表（改成我们 8 套主题）、form-selection 的「先列候选再选」和「卡片只用于平行等权」。

**契合度：高，正好用在 TH 和 LS 的 prompt 上。**

**短板：** 是给 Claude 用的技能文档，本身没有机器校验；领域表本质上仍是表，但它是**领域类别**，不是题目里的名词。

### 2.9 md2pptx 和 slide-deck-ai（MIT）

- **md2pptx：** 用用户模板的 `slide_layouts[n]`。版式号写在 metadata 里（`contentSlideLayout`、`sectionSlideLayout`……），卡片（`card.py`）按几何计算。可借鉴的是「版式号可配置、按 Office 标准版式下标约定」。
- **slide-deck-ai：** `helpers/pptx_helper.py` 写死了 Office 标准下标：0 标题页、1 标题加内容、4 对比、5 仅标题、8 图片加说明。说明 Office 内置版式的下标约定很稳定，可以作为 COM 母版路线的保底。

### 2.10 真 PowerPoint 模板（.potx 母版和版式），用 COM 驱动

COM 接口（Microsoft Learn）：

- `Slides.AddSlide(Index, pCustomLayout)` 用指定的 CustomLayout 建页；
- `Presentation.SlideMaster.CustomLayouts(i)`、`Presentations.Open(potx)` 和 `ApplyTemplate` 能套母版；
- `Shapes.Placeholders(i)` 按占位符填文字或图片；
- `TextFrame2.AutoSize`、`VerticalAnchor`，以及 `BoundHeight` 实测（我们的 `renderers/powerpoint_com/measure.py` 已经在用）。

现状：我们 `executor.py:470-491` 是 `Presentations.Add()` 加 `Slides.Add(n, ppLayout)`，然后用绝对坐标贴文本框，**没有用母版**。

**利：**

- 版式几何设计一次就固定下来，不再每页计算；
- 用户在 PowerPoint 里能换主题、改母版，整份跟着变；
- 和 ppt-master、pptx-from-layouts 一致。

**弊：**

- 8 套主题乘约 16 个版式就是 128 个 CustomLayout，需要生成工具。COM 可以给 CustomLayout 加占位符，但「在 Layout 上新增任意占位符」**要在 Windows 上先做一次可行性 spike 验证**。
- 占位符尺寸固定，卡片底板画在版式上，所以组数和卡片数必须严格相等，否则就是空卡。
- 稀疏问题仍然要靠「换小槽位的变体」来解决。

**结论：** 母版路线是**正确的终点**，但第 9 轮先做「版式目录（数据）→ 我们自己的 `PageDesign` 绝对坐标」，保持现在的契约和 COM 执行器不变。版式目录的几何天然可以在后续轮次编译成 .potx，所以本轮只做 spike，不作为验收项。

---

## 3. 对照：成熟项目怎么解决我们的三个病

| 病 | 我们第 8 轮的做法 | 成熟项目的做法 | 出处 |
|---|---|---|---|
| **分组从不驱动版式** | B 给了 groups，但 F 用 `_column_cluster_underflows`（layouts.py:326-344）和 `MIN_HORIZONTAL_CLUSTER_FILL_RATIO = 0.52`（:142）把两栏和三卡几乎全否掉，hero-statement 又把第 1 条要点抬成大字结论 | 组数是版式身份的一部分：`cards-N`、`comparison-N`、`column_N`，或者 `min_children = max_children`。组数不一致就**报错让模型改**，不偷偷降级 | pptx-from-layouts `rules/visual-types.md`、`generate_pptx.py:6193`；presenton v2 `template.json`；ppt-master `12_three_card` |
| **稀疏页** | 代码估算高度，用比例阈值决定换版式，或者按 48/52 竖向居中 | 槽位容量是写进 schema 的硬约束（min 字数、min 条数）。内容不够时：① 换槽位更小或节奏为「留白」的版式；② 从来源补细节；③ 内容按自身高度成团、文字顶到槽位带上沿。**绝不拉伸** | ppt-master `pptx-structure-interface.md:67`；SlideSpeak layout-fit-model 第 2、3 层；presenton `generate_slide_content.py` 的 min／max 规则 |
| **关键词主题过拟合** | `selection-index.json` 的 keywords 里写进了留出题的原词（糖尿病、中医、新能源、量子……） | 没有一家用关键词：用户选（presenton），agent 读描述后选（PPTAgent），strategist 加用户确认（ppt-master），领域比稿加反选（slides_maker） | 见 2.1、2.2、2.3、2.8 |

---

## 4. 推荐：主移植 presenton，再融合其他五家，并在三处超越

**为什么主移植 presenton：**

1. 它是唯一一个**在生产规模上**（约 400 个版式、16 套模板）把「模型逐页选版式 → 按版式 schema 写内容 → schema 里带 min／max 容量」做成稳定契约的项目。
2. 它的 schema 约定（每个槽位 `min/max_length`，重复项 `min/max_items`，`description`）可以直接落到我们已有的 `LayoutCapacitySpec`（`f_layout/capacity.py:221`），不用另起炉灶。
3. 选版式的输出就是「每页一个 id」，和我们的 agent 请求／答案文件协议天然契合，宝特写答案的成本很低。
4. Apache-2.0，可以照抄 prompt。
5. 它的弱点（没有 underflow 检测、不选主题）正好是我们已经有的（occupancy、TH 阶段）。

**为什么不选另外三个：** pptx-from-layouts 太小、python-pptx、还缩字号；PPTAgent 要参考 PPT 加视觉模型，而且模板有版权问题；ppt-master 是写 SVG 的工作流文档，不是可移植的契约。

**融合清单：**

| 从哪里 | 拿什么 | 放到我们哪里 |
|---|---|---|
| ppt-master | 20 个版式名单和槽位清单、`page_rhythm`（anchor／dense／breathing）、槽位容量规则原文 | `f_layout/catalog/*.json` 的 roster 和 `rhythm` 字段；LS 的 prompt |
| pptx-from-layouts | 8 步视觉类型决策顺序、`*-N` 命名、栏数不一致时的报错文案、每栏 ≤4 条、每页 ≤10 条 | LS 的 prompt、LS 的校验器 |
| PPTAgent | layout_selector 的 reasoning 格式（字数区间对比，以及为什么胜过次优）、用参考原文定 ideal 字数 | LS 答案格式 `{layout_id, slot_map, reasoning, runner_up}` |
| SlideSpeak | 「LLM 选型有约束力，校验错误要带替代版式」、内容成团原则、学术 slide-type 表 | LS 校验器的报错、`compile_layout()` 的摆放规则 |
| allweone | 关系词表（parallel、sequence、compare、cause、part_whole） | B 的 `PointGroup.relation`（新增的可选字段） |
| slides_maker | 主题比稿流程、领域加反选表、form-selection 的「先列 2–3 个候选再选，卡片只用于平行等权」 | TH 的 prompt 和答案字段；LS 的 prompt |
| ppt-template-fill | 请求里带「预填骨架」，避免漏填 | LS 和 B 的请求文件 |

**超越的三处：**

1. **证据和引用诚实。** 它们都没有「每个槽位的文字必须能追溯到 `source_refs`」。我们的 textcheck 加 citation 门槛对 LS 改写后的槽位文字照样生效。presenton 的 verbosity 会「为凑字数补内容」，我们规定**补内容只能补来源里有的细节，否则就换小版式**。
2. **真机 COM 实测的 occupancy 门槛。** presenton 和 pptx-from-layouts 都没有 underflow 检测；SlideSpeak 只在无头浏览器里测。我们在 PowerPoint 里实测 `BoundHeight`，并做卡片级和分栏级检查，阈值沿用原值不动。
3. **可审计的选择记录。** 每页的 `layout_id`、`reasoning`、`runner_up` 和每套主题的比稿记录都写进 `page_design` 和 `production_plan`，冒烟汇总逐页列出。presenton 只输出一个下标。

---

## 5. 第 9 轮计划（草案，等用户批准后再写 HANDOFF）

### R9-0：先照抄进仓库（vendor），再动手

- 新建 `v01/third_party/`，放 presenton 的两段选版式 prompt、写内容 prompt 的规则段、`to_string` 格式，以及 v0.5 的 12 个 Zod schema（写成 JSON）。每个文件头保留 Apache-2.0 声明并注明改动，`NOTICE.md` 补条目。
- 同样放 pptx-from-layouts `rules/visual-types.md` 和 `columns.md` 的决策段、ppt-master `presentation_core/design_spec.md` 的名单段和 `pptx-structure-interface.md:67`、slides_maker `design-by-topic.md` 的流程段（均为 MIT，保留版权声明）。
- 验收：`third_party/` 里每份文件都有来源 URL、commit 和协议；`rg -i "agpl|gpl"` 零命中。

### R9-1：版式目录改成数据，照 presenton 的 schema 加 ppt-master 的名单

- 新建 `f_layout/catalog/<layout_id>.json`，大约 16 个。基于我们现有 14 个，加上 ppt-master 名单里缺的 `process_timeline-N`、`content_caption`。分组版式按组数拆成独立条目：`two-column`（group_count=2）、`three-card`（3）、`four-card`（4），每个再分 `standard` 和 `compact` 两档。
- 每条的字段：
  - `id`、`name`、`description`（presenton 的写法）；
  - `use_when` / `avoid_when`（pptx-from-layouts 加 allweone 词表）；
  - `rhythm`（ppt-master 的三档）；
  - `group_count`（固定值，或 null）；
  - `slots[]`，每个槽位有 `slot_id`、`kind`（title、line、prose、items、group、stat、image、chart、table、citation）、`min_chars`、`ideal_chars`、`max_chars`、`min_items`、`max_items`、`box`（英寸几何，按骨架 token 给变体）、`anchor`（top 或 upper_middle）。
- `capacity.py` 的 `LayoutCapacitySpec` 改成从目录里读，作为唯一来源（SlideSpeak 说的「budget drift」靠这个避免）。
- 验收：
  - 目录 schema 校验通过；
  - 每个分组版式的 `group_count` 和它的 group 槽位数相等；
  - 每个版式有 min、typical、max 三份 golden fixture，用 compile-only 回放时 occupancy 和 overflow 都通过。这三份 fixture 由宝特提供，**反重力不得为了过 fixture 修改阈值**。

### R9-2：新增 LS（版式选择）agent 阶段，照 presenton 的选版式流程加 PPTAgent 的理由格式

- 位置：B 之后、F 之前（presenton 的顺序是先选版式再落内容；我们的 B 已经出了内容，所以 LS 只选版式和分配槽位，必要时在来源范围内压缩措辞）。请求和答案文件是 `LS_request_<n>.json` / `LS_response_<n>.json`。
- 请求内容：
  - 版式目录文本（照 presenton `to_string(with_schema=True)` 的格式）；
  - 每页的 `page_role`、`headline`、`groups`（含 relation）、`key_points`、可用图片和资产；
  - 决策顺序（pptx-from-layouts 的 8 步）；
  - 「先列 2–3 个候选再选」（slides_maker）。
- 答案格式（每页一项，数组长度必须等于页数，照 presenton 的约束）：
  ```json
  {"page_id": "...", "layout_id": "...", "slot_map": {"slot_id": "文字"}, "reasoning": "...", "runner_up": "..."}
  ```
- 校验器只做硬规则，**没有阈值和比例**：
  - `layout_id` 在目录里；
  - 有分组时 `group_count` 等于 B 的组数；
  - 每个槽位的字数和条数在 [min, max] 之内；
  - 图片槽确实有资产；
  - 槽位文字只能来自本页的 key_points 或 groups（防止编造）。
  - 不通过就返回可行动的错误（哪个槽位、预算多少、实际多少、建议哪几个替代版式，照 SlideSpeak 的做法），然后重试。
  - 相邻两页同版式、全份版式种类少，只给 **warning**，不强制换版式。
- 后端不可用时的兜底：按 pptx-from-layouts 的 8 步规则做一次确定性映射（页面角色加组数决定版式），写 `layout_method: "rule_fallback"`。正常运行写 `"agent"` 或 `"llm"`。
- 答案文件只能由宝特写。没有答案时照第 5 轮规则停下。
- 验收：
  - 单测：组数和版式不一致时校验失败；字数低于 min 时校验失败，并且错误里带替代版式；
  - smoke8 的 T02、T12、T36 里，每个有 2 组或 3 组的页面，最终版式要么是对应的 `*-N`，要么是宝特在答案里写了理由的替代版式；
  - `page_design` 里每页都有 `layout_method`、`reasoning`、`runner_up`。

### R9-3：layouts.py 删什么、留什么

- **删：**
  - `_score_layouts_for_page`、`_column_cluster_underflows`、`MIN_HORIZONTAL_CLUSTER_FILL_RATIO`；
  - `select_deck_layouts` 里按多样性强行换版式的几遍；
  - 每个版式各自手写的 builder，约 3000 多行；
  - hero-statement「拿第 1 条要点当大字结论」的逻辑。
- **换成：** 一个通用的 `compile_layout(spec, slot_map, theme) -> PageDesignEntry`。
  - 按目录给的槽位框摆放；
  - 卡片和栏的高度取同一行里内容最高的那张，**整组**按 `anchor` 放在槽位带的顶部或上中部（SlideSpeak 的成团规则，ppt-master 的「文字顶到槽位带上沿」）；
  - 不拉伸文本框，不缩字号到最小字号以下。
- **留：** `capacity.py` 的测量（`fit_text_to_box`、`estimate_text_height_in`）、主题骨架 token 画页面装饰（R8-4）、引文锚定在底部（R8-L）、占位托盘、证据和引用门槛、occupancy 的阈值和全部检查（**不许改**）。
- occupancy 只加一条豁免：目录里 `rhythm == "breathing"` **并且**实际内容不超过该版式声明的 max，才可以豁免（ppt-master 的定义）。豁免原因写进报告。按内容判断的规则和 R8-3 的封面规则保持不变。
- 验收：
  - `layouts.py` 不超过 800 行；
  - `rg "0\.52|_column_cluster_underflows|_score_layouts_for_page"` 零命中；
  - 单测断言 F 里没有「数值和 VOID_MAX 或 fill 比例比较后决定选哪个版式」的代码；
  - smoke7 fixture 上那几页阶梯页仍然判不合格；
  - 用 smoke8 答案在 compile-only 下回放，四题全部通过 fill_rate 和卡片检查。

### R9-4：TH 主题选择，删掉关键词，照 slides_maker 的比稿流程

- 从 `selection-index.json` **整个删掉 `keywords` 字段**。`best_for` 和 `avoid_for` 改写成领域**类别**（比如「临床医学和健康科普」「文化遗产和艺术史」「气候和生态」「数据和政策分析」），不得出现任何具体题目里的名词。
- `_rule_select_theme()` 的关键词打分整段删掉。兜底改成固定返回 `academic-default`，写 `theme_method: "default_fallback"`，理由开头写「无模型兜底：」。理由：调研的所有成熟项目都没有关键词选主题；兜底一旦按关键词打分，就会被测试题目牵着走。
- TH 的 prompt 改用 slides_maker 的五步比稿；答案格式扩成：
  ```json
  {"theme_id": "...", "rationale": "...", "domain": "...", "runner_up": "...", "anti_pick_avoided": "..."}
  ```
  校验器要求 `runner_up` 在库里且和 `theme_id` 不同。
- 验收：
  - lint 测试：`selection-index.json` 里任何 `best_for` / `avoid_for` 的词，不得是仓库内任何 fixture 题目的子串（直接堵住第 8 轮的抄题问题）；
  - `theme_holdout.json` 里只保留期望和禁选，style 字段改成 `academic_clean`、`professional_business`、`minimal` 这类中性值，不再写 museum_reflective 这种提示性的值；
  - 冒烟四题至少用到 3 套不同主题，`SUMMARY.md` 写出每题的比稿记录原文。

### R9-5（只做 spike，不算验收）：.potx 母版路线可行性

- 在 Windows 上用 COM 把 R9-1 的目录，为 1 套主题编译成一个 .potx：每个版式一个 CustomLayout，带占位符；再用 `Slides.AddSlide(i, CustomLayout)` 加填占位符生成一份 5 页的 deck。
- 交付：spike 报告（能不能在 CustomLayout 上新增任意占位符、能不能导出 PNG、占位符继承有没有问题）和截图。这决定第 10 轮要不要切换到母版路线。

### 第 9 轮整体验收（全部满足才算过）

1. Windows 全量 pytest 退出码为 0；Linux 上清掉 PPTV01_* 环境变量后全量 pytest 退出码为 0。
2. R9-1 到 R9-4 的单测和 lint 全部通过；每一项单独提交，提交说明以 `r9-N:` 开头。
3. **密封留出集（sealed holdout）：**
   - 宝特另外准备 20 个**不进仓库**的题目，覆盖医学、人文、工程、商业、生态、教育、法律等领域，每题写好可接受的主题家族和禁选；
   - 合并后由宝特用 Bivia API 后端（`--llm api`，密钥只从环境变量读）跑 TH 和 LS；
   - 判定标准：主题落在可接受家族的比例 ≥ 90%，禁选命中为 0；
   - 每题的 LS 选择：分组页 100% 命中 `*-N` 或者有合理替代理由；用 compile-only 回放时 fill_rate 100% 通过；
   - 反重力看不到这套题，所以「抄题进关键词」在结构上做不到。
4. Smoke 9：宝特先写好 LS 和 TH 答案，反重力在 Windows COM 上**从头**跑四题。产出 deck、渲染图、`contact_sheet.png`、和 smoke8 的 before／after 对比、python-pptx 只读核验的讲者备注，以及每页的 `layout_id`、reasoning、runner_up 表。
5. 目检：同一份 deck 里，有分组的页和单栏页一眼就能分出来；四题的内容页能看出至少 4 种结构，不只是装饰不同。
6. 诚实：没有任何 `*_response_*.json` 出自反重力之手；兜底路径都有标记；pytest 的退出码和条数写进 SUMMARY。

**改动面估计：** R9-1 加 R9-3 是主体，删掉大约 3000 行、新增大约 800 行代码加 16 份 JSON。R9-2 加约 400 行。R9-4 加约 150 行。

---

## 附录 A：presenton v0.5「general」12 个版式的容量（摘自 Zod schema，路径 `presenton_v05/servers/nextjs/presentation-templates/general/`）

| layoutId | 关键约束 |
|---|---|
| basic-info-slide | title 3–40；description 10–150；1 张图 |
| bullet-icons-only-slide | title 3–40；bullet 2–3 条（标题 2–80，描述 5–150） |
| bullet-with-icons-slide | title 3–40；description ≤150；bullet 1–3 条（标题 2–60，描述 10–100） |
| chart-with-bullets-slide | 图表数据 2–5 组；bullet 1–3 条 |
| general-intro-slide | title 3–40；description 10–150；presenter 和 date 2–50 |
| metrics-slide | title 3–100；metrics 2–3 个（label 2–50，value 1–10）；描述 10–150 |
| metrics-with-image-slide | metrics 1–3 个（value 1–20） |
| numbered-bullets-slide | bullet 1–3 条（标题 2–80，描述 10–150） |
| quote-slide | heading 3–60；quote 10–200；author 2–50 |
| table-info-slide | 列 2–5；行 2–6；描述 10–200 |
| table-of-contents-slide | 章节 1–10 条（标题 ≤80） |
| team-slide | 成员 2–4 个 |

## 附录 B：本轮查过的关键文件（便于核对）

- presenton：`servers/fastapi/utils/llm_calls/generate_presentation_structure.py`、`generate_slide_content.py`、`templates/presentation_layout.py`、`utils/get_dynamic_models.py:32`、`templates/v2/schema.py:251-449`、`templates/*/template.json`；`presenton_v05/servers/nextjs/presentation-templates/general/*.tsx`。
- PPTAgent：`PPTAgent_v020/pptagent/induct.py:95-240`、`presentation/layout.py:21-140`、`roles/layout_selector.yaml`、`roles/schema_extractor.yaml`、`roles/editor.yaml`、`prompts/category_split.txt`；`PPTAgent_v1138/pptagent/mcp_server.py:95-215`。
- ppt-master：`skills/ppt-master/templates/layouts/layouts_index.json`、`presentation_core/templates/design_spec.md`、`references/pptx-structure-interface.md:60-72`、`references/executor-base.md:235-243`、`references/strategist.md:269`、`templates/styles/styles_index.json`、`references/visual-styles/_index.md`、`workflows/routing.md:111-114`。
- SlideSpeak：`docs/specs/2026-06-04-layout-fit-model.md`、`engine/occupancy.ts`、`skills/academic/layout-grammar.md`。
- allweone：`src/lib/presentation/layout-catalog.ts`。
- pptx-from-layouts：`.claude/skills/pptx-from-layouts/rules/visual-types.md`、`rules/columns.md`、`scripts/generate_pptx.py:3196-3360, 5570-5590, 6190-6200`、`scripts/content_fitter.py`、`templates/inner-chapter-config.json`。
- ppt-template-fill：`scripts/pptxkit.py:75`、`references/plan-format.md`、`scripts/snapshot.ps1`。
- slides_maker：`skills/slide-maker/references/design-by-topic.md`、`form-selection.md`。
- 我们自己的代码：`/workspace/r8wt/v01/src/pptv01/f_layout/layouts.py:142, 326-344, 405-440`、`f_layout/capacity.py:221-413`、`theme/select.py:43-117`、`renderers/powerpoint_com/executor.py:470-491`、`contracts.py:304-330, 620-700`。
- COM 文档：Microsoft Learn `Slides.AddSlide`、`Shapes.AddPlaceholder`。
