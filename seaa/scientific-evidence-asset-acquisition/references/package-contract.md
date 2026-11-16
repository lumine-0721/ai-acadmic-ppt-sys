# EVIDENCE_PACKAGE 数据与协作契约

## 交付文件

固定产出原 Prompt 2 的七个文件，不把链接清单冒充证据包：
- `00_RESEARCH_OVERVIEW.md`：任务/范围、Given 审计（M）、核心发现、限制。
- `01_SOURCE_MANIFEST.json`：对象 `{"sources":[...]}`，来源账。
- `02_EVIDENCE_MANIFEST.json`：下述项目头、证据、任务、比较与逐页交接索引。
- `03_ASSET_MANIFEST.json`：对象 `{"assets":[...]}`，素材账。
- `04_RETRIEVAL_LOG.md`：真实日期、R、查询/本地检查命令、平台/文件、范围、纳排理由和停止条件；未外查则明确写“本轮仅审 Given”，不伪造搜索日志。
- `05_GAPS_AND_CONFLICTS.md`：缺口严重度、对应 C/R/S/V/E/A、冲突双方、未读范围与用户实验责任。
- `06_HANDOFF_TO_PROMPT3.md`：逐页科学信息、证据/素材、定位与引用、必保留条件、禁止推论/比较、缺口与状态。
- `07_CHANGE_REQUESTS.md` 仅在需要 Prompt 0 改主张/叙事/页序/目标时生成；给 project_id/base_revision、affected_ids、原提法、证据、提案和保留项。不直接写上游任务书。

所有 JSON 使用 UTF-8。主键分别 SRC/E/A/R/S/CMP 加至少三位数字，稳定且不回收。项目级 E/C/R/S/V 沿用上游；新 E 不与已有未关联内容撞号。退役记录保留 `retired: true`，活跃记录不引用退役项。无值用 null/UNKNOWN，并说明影响；不要用虚构默认值填未知科研字段。来源与素材的本地路径可用绝对路径或相对包根路径，跨设备交付时说明可达性。

## 来源 SRC

必需字段：
`source_id,title,origin,url_or_path,read_scope,verification_status,metadata,usage_notes`。
- origin：`user_raw/user_processed/user_statement/primary_research/review/other`。
- read_scope：`metadata/abstract/section/full_text/data/user_notes`，表达实际读到什么；具体页范围放 usage_notes。
- verification_status：`VERIFIED/USER_PROVIDED/TO_VERIFY/UNAVAILABLE`。VERIFIED 只在声明范围内核对，并非论文的所有结论正确。
- metadata 对象：`authors,year,doi,publication,peer_reviewed`，未知为 null，peer_reviewed 用 true/false/null。
- 可加本地文件 `sha256`（小写）；此时校验会重读核对本地文件，若 url_or_path 是网络入口，另给 `local_path` 指向实际副本；没有全文，不自动填全文图号、实验条件。不联网的本地陈述核验不得伪称外部论文核验。

## 证据 E

必需字段：
`evidence_id,source_id,origin,status,verification_scope,locator,subject,method,finding,measurements,conditions,controls,limitations,alternative_explanations,applicability,claim_links,slide_ids,task_ids`。
- origin：`user/literature/other`；前三类用户来源只能产生 user 证据，原始论文/综述对应 literature。
- status 使用 Prompt 0 六状态：VERIFIED / USER_PROVIDED / INFERRED / HYPOTHESIS / TO_RETRIEVE / BLOCKED。
- verification_scope 精确说核验到哪层；locator 是文件页/图/章节/数据行等非空定位，尚未定位时可 null 且不升级为 VERIFIED/USER_PROVIDED。
- measurements 为数组，每项 `{value,unit,definition}`；没有量化结果留空，不能用 0 表示未测。
- conditions、controls、limitations、alternative_explanations 用数组，未提供时明确写“未提供/不适用”及影响；method 和 applicability 仍要解释。
- claim_links：`[{claim_id:"C001",relation:"supports"|"opposes"|"context"}]`。E 至少服务一个明确 C；关联不是“发表即证明”。
- slide_ids/task_ids 是所服务 S/R 列表；允许无指定 S 的局部研究，但不伪造目标页。

## 素材 A

必需字段：
`asset_id,source_id,evidence_ids,slide_ids,status,source_locator,path,sha256,format,caption,conditions,rights,scientific_immutable,transformation`。
- status：`LOCATED/ACQUIRED/UNAVAILABLE`；只有 ACQUIRED 填真实 path、SHA-256 和 format。仅有 URL 不填下载路径。
- rights：`{status:"ALLOWED"|"REFERENCE_ONLY"|"NEEDS_PERMISSION"|"UNKNOWN",basis,attribution}`；许可和科学核验分开。准备用作页面素材时必须明确允许相应使用。
- transformation：`{kind:"original"|"redrawn"|"converted"|"digitized"|"schematic",basis}`；任何派生需写来源、方法与精度，保留原值。digitized 明确是近似估读。示意不作为实验结果。
- 可加尺寸、分辨率、编辑性与检查记录；script 对 ACQUIRED 会读取文件并核哈希，不把同名文件等同。
- evidence_ids 指向至少一条有对应来源的 E；派生/合成素材可以涉及多个来源，但 source_id 对应其主源，其他源通过 E 保留。

## 证据账项目头与交接

02_EVIDENCE_MANIFEST.json：
`project_id,base_revision,package_revision,mode,scope,target_slide_ids,claim_ids,evidence,tasks,comparisons,slides`。
- project_id 是上游项目或明确声明的本地审计项目；base_revision 无上游时 null，否则上游正整数；package_revision 为独立包版本正整数。
- mode 是 FULL_RESEARCH / GIVEN_FIRST_AUDIT / TARGETED_RETRIEVAL / EVIDENCE_UPDATE。
- scope 为 full / targeted；target_slide_ids 是本包负责的 S，按上游顺序。claim_ids 列本包涉及 C（可含上游其他上下文 C）。
- evidence 是 E 记录数组。tasks：`[{task_id,status,evidence_ids,asset_ids,resolution}]`，status 为 OPEN/IN_PROGRESS/DONE/BLOCKED。DONE 有实际成果或明确无结果的收尾说明；不代表证据/素材足够。
- comparisons：`[{comparison_id,evidence_ids,status,dimensions,rationale,permitted_use}]`。status 为 DIRECTLY_COMPARABLE/CONDITIONAL/NOT_COMPARABLE/UNKNOWN；dimensions 对象逐项包含 metric、unit、conditions、controls、statistics、scope 的判断。permitted_use 为 rank/qualified/separate/none；仅 DIRECTLY_COMPARABLE 允许 rank。没有横向比较可为空。
- slides：`[{slide_id,status,scientific_message,claims,evidence_ids,asset_ids,visual_ids,asset_requirement,asset_rationale,must_preserve,prohibited_inferences,gaps,comparison_ids}]`。
  - status 为 READY/PROVISIONAL/BLOCKED。READY 是科学/素材条件就绪，不代表排版或生成已验收。
  - claims 为 `[{claim_id,purpose:"establish"|"discuss",disclosure}]`；establish 必须有足够、可追溯的支持证据；discuss 必须显式说明假说/问题/反证，避免把待讨论内容写成既定结论。包中若有反对该 C 的证据，READY 的 establish 要补 `conflict_assessment`（审查理由）并在 disclosure 中保留可见限定，不以数正反论文代替证据判断。
  - asset_requirement 为 required/optional/none；asset_rationale 解释。READY 引用的 A 应实际取得且允许当前使用；只是可选候选的 A 留在素材账，不列为 READY 页的已用素材。
  - must_preserve/prohibited_inferences/gaps 为文本数组。READY 不得带未解决硬缺口；非 READY 要写缺口。不要求每页有图；纯讨论页写 none 并说明。
  - E/A.slide_ids 表示可服务或候选页面；S.evidence_ids/asset_ids 表示本页当前选用项。候选不必全部采用，但每个选用 E/A 必须反向关联本页，未选用的支持证据不解除本页缺证状态。某比较在页面用到时写 comparison_ids。资料不一致时不按软件 PASS 自动改判断。

06 手册保留机器可核对的摘要表：每个目标 S 一行，首两列 `| S001 | READY |`（可接更多列），顺序与 slides 一致；表后展开全部交接细节。无目标 S 时说明局部研究范围，按 C/R 交接，不造页号。

## 跨 Skill 与更新

Prompt 0 是需求权威。新证据通过 07 提案回填；不要把本包的 READY 直接改成上游 READY。Prompt 1 消费 A 的科学边界、尺寸、条件和可用状态，只决定布局是否容得下；版式 COVERED 与科学 READY 分账。两个包与任务书都要标明基线。Prompt 3 尚未安装时，以可供人/其他生成器读取的 06 手册和 JSON 交付，不虚报已调用。

Mode C 定向增量可以仅有选中 S/R；合并采用新的 package_revision，保留旧条目和未受影响页面。Mode D 更新前复制整个旧包到版本目录；若需要更新证据内容，用新的文件路径保存新输入，不在旧包底下悄悄改数据文件。源身份改变（换论文/换原始数据）用新 SRC/E/A；旧观点保留为反证或退役并说明。复核变化影响到的 C/E/A/S/V 与用到它们的比较；全局范围变更由统筹补充影响范围。

## 命令

```text
python SKILL_DIR/scripts/check_evidence.py validate EVIDENCE_PACKAGE
python SKILL_DIR/scripts/check_evidence.py validate EVIDENCE_PACKAGE --handoff PPT_HANDOFF.json
python SKILL_DIR/scripts/check_evidence.py validate EVIDENCE_PACKAGE --handoff PPT_HANDOFF.json --production
python SKILL_DIR/scripts/check_evidence.py impact OLD_PACKAGE NEW_PACKAGE
```

--production 检查本包负责的页面，不等于检查全稿。TARGETED_RETRIEVAL 通过不能宣称未覆盖页也通过。脚本验证记录、追踪、指纹、上游页/版本和基本状态约束；不验证科研真伪、全文含义、许可合法性、统计推断或给定声明的真实来源。实质审查仍须执行。过期/部分/冲突包可以交付为草稿，必须保留状态与解除条件。

