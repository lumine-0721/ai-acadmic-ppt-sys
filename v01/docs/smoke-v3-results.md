# Smoke 4 · A→D 实跑记录（agent 后端，真实联网检索）

> 运行目录 `runs/smoke-agent-v3/`（gitignored，本文件记录结论）。LLM = `agent:宝特 (manual, not an API model)`：A/B/C 的答案由 agent 手写（复用 smoke-agent-v2 的答案，B 的 R### 编号按新的过滤后笔记重映射、删除指向被过滤笔记的引用）；B 的检索与 D 的 Wikidata/Commons/OpenAlex/Crossref 为**真实联网调用**，D 不使用 LLM。E–H 未实现，整体状态 `incomplete`（硬门槛 `not_run`，不假装通过）。

## B：检索相关性过滤（structural + fastembed@0.30）

| 任务 | 保留笔记 | 被过滤 | 被过滤示例 |
|---|---|---|---|
| T02 | 14 | 9 | bad_type 综述勘误、海绵宝宝角色列表、Heat map、大氣環流…（含 1 条相关的 Green roof，误杀） |
| T12 | 18 | 6 | INTRODUCTION:、韩国的咖啡（过短）、Israel、Cataract Surgery in the Dog |
| T26 | 22 | 5 | Chapter Sixteen、Inhalt、[Illustration]、Figure 3…（junk_title） |
| T36 | 23 | 1 | Overview of Machine Learning in Geomechanics（bad_type） |

## D：素材登记

| 任务 | 已取回/需求 | 图片 | 参考文献 | 未取回（原因） |
|---|---|---|---|---|
| T02 | 8/13 | 4（SEDAC 热岛全球图 CC BY 2.0；北京公园 PD；屋顶绿化 CC BY-SA；海绵城市公园 CC BY 4.0/BY-SA） | 4（OpenAlex 元数据 + Crossref DOI 核验） | diagram×4、chart_data（D 不画图、无数据源） |
| T12 | 3/12 | 2（Café de Flore PD；Specialty coffee CC BY 2.0） | 1 | statistic/chart_data/table/diagram×6 |
| T26 | 9/14 | 8（帕特农 CC BY 2.0、沙特尔 PD、佛光寺 CC BY-SA 3.0、埃菲尔 PD、萨伏伊 CC BY 2.0、克莱斯勒 PD、上海中心 CC BY-SA 3.0） | 1 | diagram×4；1 个多目标需求只取主目标（Chrysler 未覆盖） |
| T36 | 2/7 | 0（本任务图片需求全是 diagram/icon） | 2 | diagram×4、icon |

人工看图抽查（T02/T12/T26 共 14 张，内容未由程序视觉核验）：多数对应正确；弱点——T02 A009 北京公园照与"热岛"主题关系弱；T26 A006 佛光寺是屋檐局部；T26 A010 萨伏伊别墅是室内浴室；T26 A001/A002 重复同一张帕特农图（跨需求未去重）。

## 实跑中发现并已修复的 D 缺陷（均加了回归测试）

1. Wikidata 消歧选中"论文条目"（佛光寺东大殿 → 学术论文 Q119477242；萨伏伊别墅同）。修复：按 P31 剔除 scholarly article/thesis/列表/消歧页（真实录制响应作测试）。
2. 嵌入打分压过 Wikidata 自身排序（帕特农神庙 → 选中"山形墙浮雕"雕塑 Q3753726）。修复：搜索 `match.text` 与目标名精确一致时沿用 Wikidata 排序（`chosen_by=exact_label_match`）。
3. 极端长宽比图片（800×126 全景）入选。修复：长边/短边 >3 拒绝并记录原因。
4. CC BY 的 Commons 文件无 Artist 字段 → 契约校验使整个 D 失败（T02 实跑触发）。修复：候选阶段拒绝"需署名但无作者/credit"，有 credit 时以 credit 作作者。
