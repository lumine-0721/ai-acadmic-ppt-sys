# 选型、分层与实施顺序
审计时 Python 3.13.6；原包要求 3.11+，构建使用 setuptools。原 orchestrator 固定 jsonschema 4.25.1；本机 jsonschema 4.26.0、Pydantic 2.11.7、protobuf 5.29.5、python-pptx 1.0.2。许可来自已装发行包 metadata。
|候选与原始依据|类型/互操作与可追踪性|演进/维护决策|
|---|---|---|
|[JSON Schema 2020-12 / python-jsonschema](https://python-jsonschema.readthedocs.io/en/stable/validate/)，MIT 实现|运行期封闭结构；多语言可读 JSON；可逐字段审阅|选用。本包自带正式 Schema，引用完整性等跨节点规则用小型 Python 层；不复制库代码|
|[Pydantic](https://docs.pydantic.dev/latest/concepts/json_schema/)，MIT|Python 类型提示优秀、能导出 Schema|在已有 JSON 合约之外维护类模型/转换行为有重复成本；不新增这一运行依赖|
|[Protobuf](https://protobuf.dev/programming-guides/proto3/)，BSD-3-Clause|生成多语言类型、字段号演进；二进制不便手工比对|上游都是 JSON；无高吞吐 RPC 需求，不引入编译器和第二种 wire 格式|
|[python-pptx](https://python-pptx.readthedocs.io/en/latest/user/slides.html)，MIT|有 master/layout/placeholder 和具体形状，适合未来导入/导出|直接持久化 OOXML 对象不利于证据图、文本 diff、独立迁移；借鉴引用关系而非复制类|

单一 v3 格式、主版本号显式；v2 是历史生产者格式，不修改其 Schema。工件适配器只读输入。映射表以稳定 ID 为键，列表只承担顺序/层级；每个页面有语义、设计、解析布局三个独立域。来源缺失用 null 或缺口状态，不创建假文献。每个提交保留父快照摘要、作者和变化路径；失败不发布。JSON 对象扩展仅允许指定 metadata/上游注释袋；不是第二个渲染数据源。

实施任务及依赖：
1. T1（AC01/04/06）正式 Schema、严格 codec、最小模型；用最小/畸形输入独立验证。
2. T2 ← T1（AC02/06/09/11）图约束和资源解析；用复杂页、目录逃逸、模板修改反例验证。
3. T3 ← T1/T2（AC05/07）快照、操作、差异和可重放合并；用过期/交错写入及失败原子性验证。
4. T4 ← T1/T2（AC03/08）上游投影和 v2 升级；用与 builder 对接的协议测试验证，不启动调度器。
5. T5 ← T2/T3/T4（AC09/10）消费协议、示例、打包、隔离 CWD 测试、独立审查和恢复演练。

主要风险：旧 v2 的本机路径及不完整来源；调用方须提供原包和明确路径重映射，快照不存机器位置。缺失真实输入、未执行的上游端到端和生产渲染不得计作通过。恢复采用独立克隆逆向补丁；不重置共享分支。
