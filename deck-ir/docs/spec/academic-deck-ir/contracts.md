# v3 wire 与调用契约

**以包内 deck-v3.json 的 required、oneOf、additionalProperties 为准。** new_deck 生成空草稿，new_slide 生成 PROVISIONAL 页；加载不应用隐式默认值。可选字段缺省表示未提供，空 resolved 表示尚未排版；未知坐标不是零。schema_version 使用精确主版本匹配。

|域|规范含义|
|---|---|
|deck_id / metadata / sections / slide_order|项目身份、题目/作者/语言/任务、章节注册表。页序只在 slide_order 决定；章节展示顺序由首次出现决定|
|slides[id].semantic|语义目标、角色、标题、副标题、备注、讲者、章节、claim/evidence/asset IDs、elements 及顶层阅读顺序；正文为 text.runs，level 是内容层级|
|slides[id].design|布局 ID、按元素 ID 的槽位、字号/颜色/优先级/尺寸约束、原生对象 ID 与可改项。deck.theme、layouts 为共享的设计系统和模板定义；master/theme 是模板内部原始键|
|slides[id].resolved|按元素 ID 提供可选矩形，左上原点、x/y/w/h 均为画布比例 0–1。APRD normalized_geometry 保持 0–100，仅为模板原型。解析框须满足已知尺寸、安全区；不求解位置或测文字|
|claims / evidence / sources / assets|科学主张、证据、出处、资源分别独立建索引。边存于 evidence.links，以 claim_id + relation 索引；eligible 是**该边**的已评估资格。scope、locator、来源著录、SHA、许可与不许变动的数据保留。资产经 ID 解引用，不把位置当身份|
|origin / history / state|origin 是只读导入凭据/历史输入，不是内容更新入口；需要显示的数据读规范域。history 是修订链；父摘要寻址原快照（宿主保存）。draft/review/released 与 slide.status、旧 validation_state 含义不同|

对象 ID 为调用方分配的不透明稳定字符串，建议新 ID 用前缀 + UUID；现有上游 ID 保留。不同 deck 永不凭编号拼接；元素在本页作用域内唯一。排序、改字、换类型不改变引用目标。每个元素恰有一个所有者（顶层或某 group），related 是非所有权边。已删除的实体在已发布快照链中记录为 retired，不能用旧 ID 冒充新对象。

## 工件接口
- `assemble(handoff, requirements={...}, aprd={文件名: JSON对象}, seaa={文件名: JSON对象}, locations={旧位置: 包内键})`。交接清单为 PPT_HANDOFF.json；Markdown 任务书可传 `{"markdown": text}`。APRD 消费 SOURCE_MANIFEST、DESIGN_SYSTEM、LAYOUT_LIBRARY、SLIDE_STYLE_MAPPING 四个 .json；SEAA 消费 01_SOURCE_MANIFEST、02_EVIDENCE_MANIFEST、03_ASSET_MANIFEST 三个 .json。支持单独提供 APC、叠加其中一个或两个包。完整上游格式仍由各包自有 checker 验证，此处验证对接投影、身份、版本、图一致性。
- project 范围的包必须匹配 APC 项目/修订和所需页序；APRD 的 reference_only 只导入布局，不猜目标页。APC.E 与 SEAA.E 的数字没有跨模块相等含义，前者在 v3 用 `apc.E…`。同一证据可支持一个主张、反对另一个；不把 related/context 当作支持。来源未定位时可显式写 source_id=null 并诊断；指向不存在记录的非空 ID 是错误。
- 裸工件导入是草稿，永不根据文本自行推断 `eligible=True`。来源 URL 不联网获取。逻辑文件使用正斜杠相对包根；只在资源字段使用精确 locations 映射，不改摘要/标题。绝对引用须由调用方重绑定并按同一目录结构打包资源。
- `migrate(v2, handoff, check_v2=VALIDATOR.validate, receipt=receipt, ...)`。VALIDATOR 来自原 producer 的 contracts.ir。任务在**校验原工件时**记录 `input_receipt(handoff, requirements=..., aprd=..., seaa=...)` 和对应运行的 v2，迁移时对照。receipt 是规范 JSON 摘要，不等于旧 hash_tree、不是签名。历史运行若无可信收据，应复核原工件重新组装，而非在迁移时补算“旧”凭据。v1/未知版本拒绝。比较页序、两侧状态、gaps、typed edges、manifest pointer、locator、资产与设计投影；成功后仍保留旧 deck 级 gate。逻辑溯源为 02_EVIDENCE_MANIFEST.json + pointer、01_SOURCE_MANIFEST.json + source_id；不保留旧运行目录。v3 不伪装成 v2 回传旧 plan.py。
- Orchestrator 未来可以把 v3 的 digest、版本和逻辑 artifact key 放入现有 node 输出。本文没有注册 graph 节点、改 SQLite checkpoint 或宣称旧 backend 会读 v3。

## 操作、所有权与失败语义
已发布对象视作只读；API 采用值语义而非 Python 只读类型。`read_page` 返回副本。`edit(base, actor, *ops)` 按顺序暂存、整批验证；0-based 插入索引，parent=None 代表页根：
```text
("create_page", sid, index, slide)      ("delete_page", sid)
("edit_page", sid, semantic_fields)    ("move_page", sid, index)
("insert", sid, eid, node, parent, index)
("move", sid, eid, parent, index)      ("replace", sid, eid, node)
("group", sid, new_gid, ordered_sibling_ids, parent)
```
任意复杂变更（含删除元素、解析布局、整批补齐引用）可编辑私有 deepcopy，再走 proposal/merge。删除元素须一并处理层级、关联、意图、已解析引用。无隐式级联删除，不重编号。编辑已就绪页应同时将该页降为待审。主张或来源有变，需清除旧证据资格；普通修改不能签发新资格。

```python
from copy import deepcopy
from academic_deck_ir import digest, proposal, merge
candidate = deepcopy(base)
candidate["metadata"]["title"] = "第二版"
p = proposal(base, "qa-review", candidate, reads=[["claims"]])
out = merge(base, p)                    # 宿主负责以 digest(base) 为条件原子写入
assert out["history"][-1]["parent"] == digest(base)
```
提案信封含 base 摘要、actor、可选 reads、changes。路径是键数组；op 为 add/replace（带 value）或 remove。列表整体比较，宁可冲突也不按漂移的数组下标对齐。相同赋值去重；不同赋值、祖先/子孙覆盖、显式读依赖被并发写、过期摘要抛 Conflict。派生任务必须声明所读路径，例如计算布局要读主题、目标页内容/意图；未声明的业务依赖不自动推理。只有可交换且通过**合并后**验证的修改才发布。批准发布独占一次提交以绑定精确内容；模板对象锁按模板原生 ID 比较而非承载元素 ID。history 记录父摘要、排序后的作者和 JSON Pointer；重放需要父快照与提案，history 自身不存完整旧值。没有修改就不生成修订。异常不修改输入；结构、图和不允许的变更抛 Invalid，原 v2 checker 也可能抛 jsonschema.ValidationError；文件 I/O 抛 OSError。跨进程储存锁、身份认证与访问授权属于宿主。

## Backend 与 QA
`dumps` 返回排序键、UTF-8、无冗余空白/换行的规范 JSON 字节（不是 RFC 8785）；同一 Python 数值表示往返一致。禁止重复键、NaN、无穷及非 JSON 类型。包内 Schema 仅验证结构，跨对象语义须调用 validate/loads。扩展只在明确的 metadata/constraints 等注释袋中使用，建议以模块名命名，不对未知约束声称已执行。

Backend 的输入是完整 v3 字节和显式资源根；须先 loads，再按 slide_order 和语义树取对象，遇未解析布局交还布局阶段，不猜坐标。inspect_deck 为只读 Mock，返回每页元素/槽位/几何、证据定位、反对边和资源、诊断及 rendered=false。结构错误退出 2；可读草稿即便存在诊断仍退出 0，不能把进程成功当发布批准。diagnostics(deck, root) 按需查本地文件及资产 SHA；无 root 时仅能证明逻辑图有效，不证明文件存在。发布产品仍须检查实际资源、实际模板权限及版面。QA/Repair 读 diagnostics 的 code:id、提交基于父摘要的 proposal；不直接改上游输入。

**边界**：不核验外部论文真实性，不运行拟合、OCR、公式排版或字体测量；不将模板未知的自然语言规则当成可执行约束。原生元素导入/导出及主版内 key 的真实性由未来模板适配器负责；跨页移动需显式重新绑定作用域。此版本不包含渲染器、通用编排、持久化或修复引擎。
