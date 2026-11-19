# Academic Deck IR：审计与验收规格
基线：academic-ppt 的 203640d。各包已有 pyproject.toml、脚本、README、包内 schemas 和 unittest；没有统一的 OpenSpec、Spec Kit 或顶层规格树。本变更新增一个同级 Python 包，规格只在本目录。原工作树与各上游文件均不参与写入。

## 发现（来自代码，不是完成声明）
```
PPT_REQUIREMENTS.md + PPT_HANDOFF.json ─ APC 检查 ──────────┐
REFERENCE_PACKAGE / reference.pptx ─ APRD 检查或 inspect ──┼─ LangGraph gate → [revise → SEAA] → assemble(v2) → plan → QA → terminal
EVIDENCE_PACKAGE ─ SEAA 检查/采集 ────────────────────────┘
已校验 APC + APRD + SEAA 工件字典 ─ 导入 ─┐
已校验 v2 + 原工件 ─ 核对身份并迁移 ──────┴─ 独立 v3 → 只读消费者 / 带基线摘要的编辑提案
```

|现状、审计依据（相对仓库根）|可复用能力|缺口与本阶段接口|
|---|---|---|
|apc/.../schemas/ppt-handoff.schema.json、scripts/check_handoff.py|Markdown 规格 + 索引；project_id、revision、slide_order，M/C/E/R/V/S 记录、依赖及退休标记|索引摘要不是完整讲稿；保留依赖与输入记录出处，不扩写结论|
|aprd/.../scripts/check_package.py、aprd_engine/spec_compiler.py、inspect_pptx.py|独立设计系统、布局库、映射，0–100 几何、来源、精度和固定/可调约束；已有模板检查/提取|工件有 reference_only 和 project 两种；绑定布局和槽位，不将模板原型坐标写进正文|
|seaa/.../scripts/check_evidence.py、seaa_adapter.py|01 sources、02 evidence（带 claim_links、slides）、03 assets；位置、许可、hash、限制和支持/反对/背景关系|与 APC 同名的 E 不是可直接合并的证据；隔离 APC 原始记录与 SEAA 索引，保留 locator 与来源|
|orchestrator/src/academic_ppt_orchestrator/workflow/graph.py、adapters、contracts/ir.py|prepare 扇出 APC/APRD/SEAA，gate 决定重审；SQLite 检查点；结果含 acceptance/payload/validation_report；v2 合并页面就绪与 eligible_support|v2 缺可编辑对象树；资产摘要已省略部分溯源。迁移须核对原工件，不根据编号补造|
|orchestrator/.../backends/plan.py、probe_pptx.py|执行计划 v2 和 python-pptx 探针，不是正式渲染器|保留原计划；新增独立 Mock，仅消费 v3，不把解析成功称为生产可渲染|

## 行为与验收
|编号|输入 → 通过条件|
|---|---|
|AC01|空草稿 → 有版本/身份，合法，顺序唯一|
|AC02|含分组、富文本、图片、表格、公式、带出处数列的页面 → 语义/意图/已解析框分别可读|
|AC03|与真实字段对应的 APC、APRD、SEAA 模拟工件 → 正确的顺序、映射、图源、引用与定位；跨项目/修订拒绝|
|AC04|序列化再加载 → 规范字节及摘要不变，拒重复键/非 JSON 数|
|AC05|编辑、移动、替换、删除 → ID/引用不漂移；删除被引用对象须同时修复；操作失败原快照不变|
|AC06|非法字段、悬挂边、循环组、越界、缺失资源/来源、冲突 → 报错或显式诊断，不补数据|
|AC07|同一基线并发提案 → 不相交写入确定性合并；同位置不同值、过期基线拒绝；持久化 CAS 属宿主|
|AC08|真实 v2 builder 输出 + 原始工件 → 保留状态、typed edges 和 eligible；未知版本/v1/歧义拒绝|
|AC09|从任意 cwd 加载已安装包 → Schema 由包内资源定位；资产仅按注入根解析|
|AC10|只给 Mock 完整序列化快照 → 按页、对象、证据、来源和资源读取，不依赖 Office|
|AC11|模板原生对象、母版主题、许可、槽位与修改许可 → 细粒度引用可验证，禁止越权覆写|

成功不代表上游来源经过独立学术核验。引用冲突、待核查材料必须能留在草稿；对错误输入不尝试“修好”科学事实。上游所有权止于不可变输入；由宿主发布新快照。
