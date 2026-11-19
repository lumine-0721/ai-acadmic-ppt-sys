# 验收与审查记录
源基线 203640d2200b9b70e84ac079032ce02f25e835cb（原树 7d371f32d47a9134a9f303962cd8f7c2bec7822f）。变更仅新增 deck-ir；没有修改旧 IR、四个现有模块或其机器路径。

## 自动化验收（实跑）
Python 3.13.6 / jsonschema 4.26.0，仓库根执行 `python -m unittest discover -s deck-ir/tests -q`，19 tests，OK，退出 0。受限 Windows 环境可把 TEMP、TMP 设置为可写临时目录；运行期无此依赖。首次测试发现测试夹具重绑定错误和系统临时目录权限错误，修正夹具并在可写环境重跑。另有一次源目录内构建被中止，以下构建证据来自新的独立目录，未把中止算成功。

|验收|实际验证位置（tests/）|判定依据|
|---|---|---|
|01、04|test_contract: minimal_and_schema / roundtrip_strict_json|检查元 Schema、最小草稿、规范字节、负例|
|02、10|complex_consumer_and_reproducible_example|从本地 CSV 重建 complex.json；6 个对象含组，正确数据、槽位、框、来源、locator；原生渲染标记为 false|
|03|mock_adapters_binding_and_input_ownership / adapter_rejects_unknown_or_duplicate|源数据不变、外项目/修订冲突、字段映射保留；见下方额外真工件检查|
|05、07|page_and_element_operations / parallel_determinism_and_overlap，以及 test_review 的 retired_ids、release_approval|顺序、替换、引用、原子失败、读依赖冲突、两种提交顺序、过期基线|
|06、11|relationship_failures / resource_resolution_and_missing_source / opposed_is_not_support / native_constraints，及 test_review 的 native_lock、rights_and_aprd_safe_area|悬空、环、越界、资源散列、来源缺失、许可和不可变项负例|
|08|migration_real_orchestrator_builder|原仓库 build_ir + 自己的 VALIDATOR 校验，真实代码、模拟数据；逐边、locator、就绪状态及收据；拒绝错来源、不同 gaps、未知版本|
|09|cwd_cli + 离线 Wheel 验证|独立进程/含空格及非 ASCII 的 cwd；另从实际安装包而非源码导入|

**Wheel 验证**：将 pyproject.toml、README 和 src 复制到临时目录，以
`python -m pip wheel --no-index --no-deps --no-build-isolation <临时源码> --wheel-dir <输出>`
构建，用 `python -m pip install --no-index --no-deps --target <临时安装目录> <wheel>` 安装（均为退出 0）。
在仓库外再次导入，断言模块来自临时安装目录，读取 examples/complex.json 和同目录资源。实际输出 `mock-pptx/1 1 6 False`，退出 0。这里使用已安装的 jsonschema，不是无依赖部署验证。

## 上游真工件抽查（非 LangGraph 全链）
以下参数均为调用方注入位置。APC/APRD 数据属 instrument-gain-smoke 修订 2，SEAA 数据属 synthetic-evidence-trial 的 APC 修订 1；刻意不拼成一份 deck。

|命令（从仓库根）|真实输出，退出码均为 0|
|---|---|
|`python apc/apc_cli.py validate <requirements> <apc-handoff> --strict-assets`|PASS revision=2 records=11 active_slides=2 final=false|
|`python aprd/aprd_pkg_cli.py package-validate <design> --handoff <apc-handoff>`|PASS design_revision=2 refs=1 layouts=2 mapped=2 production=false|
|`python seaa/seaa_pkg_cli.py evidence-validate <evidence> --handoff <its-handoff>`|PASS revision=2 sources=2 evidence=3 assets=2 slides=2 scope=full production=false|
|`python deck-ir/tools/check_artifacts.py <apc-handoff> --aprd <design> --source-root <smoke-root>`|SLIDES=2 SOURCES=4 ASSETS=0 LAYOUTS=2 RENDERED=False|
|`python deck-ir/tools/check_artifacts.py <its-handoff> --seaa <evidence> --source-root <smoke-root>`|SLIDES=2 SOURCES=2 ASSETS=2 LAYOUTS=0 RENDERED=False|

最后两行设置 PYTHONPATH 为 deck-ir/src 或先安装包。外部工件不进仓库，不把快照中重绑定的位置当作已复制文件。单独验证两项目混合时触发 Conflict。
默认解释器首次跑 Orchestrator 回归时在导入阶段退出 1（缺 langgraph.checkpoint.sqlite）。按原包 PACKAGE_README 的来源记录找到了既有隔离环境，无须新装依赖或变更上游。该环境为 Python 3.13.6，langgraph 1.2.21、langgraph-checkpoint-sqlite 3.1.3、jsonschema 4.25.1、python-pptx 1.0.2，与原仓库固定版本一致。

|续验项目|实际结果|
|---|---|
|上述环境：`python -m unittest discover -s orchestrator/tests -p test_ir_merge.py -q`|30 tests，OK（退出 0，属于下行 37 项的子集）|
|上述环境：`python -m unittest discover -s orchestrator/tests -q`，模块根指向当前检出的四个对应目录/别名，临时工件目录独立|37 tests，OK（退出 0）；包括真实 CLI、探针、检查点恢复、增量重建等既有回归|
|本模块：`python -m unittest discover -s deck-ir/tests -q`|在 jsonschema 4.26.0 和原仓库固定 4.25.1 两个环境分别运行；均为 19 tests，OK（退出 0），不是 38 个不同测试|

## 真实 graph → 落盘 v2 → v3 → Mock
可选集成测试位于 `tools/check_graph_bridge.py`。它在临时目录调用现有 invoke_run，运行前对输入取摘要，关闭内容改写和 PPTX 探针，重新打开 producer 写出的 JSON，交由原 v2 validator 和 v3 迁移函数，再串到 Mock。对比全部活跃证据边的证据 ID、来源、主张、关系、资格、定位；逐页比较顺序、状态，并核对页引用与来源元数据。测试不属于运行包，也没有改 Orchestrator 的节点。

在安装了固定依赖和这两个包的环境运行以下命令；从源码运行则把 `deck-ir/src` 和 `orchestrator/src` 加入 PYTHONPATH。参数来自各自通过 checker 的那组工件：

    python deck-ir/tools/check_graph_bridge.py --handoff <its-handoff> --requirements <its-requirements> --seaa <evidence> --source-root <smoke-root>
    python deck-ir/tools/check_graph_bridge.py --handoff <apc-handoff> --requirements <apc-requirements> --aprd <design> --source-root <smoke-root>

前者实际输出 `V2=blocked V3=draft PAGES=2 EDGES=3 RENDERED=False`，后者为 `V2=blocked V3=draft PAGES=2 EDGES=0 RENDERED=False`；退出码均为 0。这里通过的是**保真传递与阻断语义**：前者未提供设计，后者未提供证据。与已通过的模拟齐备输入/accepted 用例分开计算。这不是三个生产包齐备的已批准报告，也未运行 Office 或正式渲染。

## 独立审查与修正
独立只读审查复现 7 类问题：v2 阻断被放行、发布与主张编辑竞争、混用同名来源包、原生对象锁跟错元素、资产授权未检查、安全区未执行、按值替换误改标题。根据复现分别修复：保留 deck 级发布门禁与页状态下界；发布绑定单一基线且事实修改需撤销资格；受信输入收据+逐页/资产核对；以模板对象身份及类别校验；检查使用中的素材许可；按百分比验证安全区；限定资源字段重映射。对应负例已在上述 19 个测试中实跑。独立初审不是放行结论；修后回归由实现方执行，第二次独立签核未取得。

## 恢复
本模块纯增量；发放包附外置 DIFF.patch、可执行 ROLLBACK.sh 和 VERIFICATION.txt。仅在一次性干净副本中运行脚本，先反向检查再应用到索引及工作树，最终比较完整 Git tree，拒绝无关修改。保留原 HEAD 历史；该机制不是对共享分支 reset。
