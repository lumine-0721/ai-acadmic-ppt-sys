# 任务书：T2 Bivia 接线修复（致 Codex）

下达：miu宝（代行宝特批改后下发）｜日期：2026-11-26
仓库：https://github.com/lumine-0721/ai-acadmic-ppt-sys
基线分支：`feature/v01-bivia-wiring` @ `6df1e59`
- 分支直达：https://github.com/lumine-0721/ai-acadmic-ppt-sys/tree/feature/v01-bivia-wiring
- 基线提交：https://github.com/lumine-0721/ai-acadmic-ppt-sys/commit/6df1e59
- 核心文件 bivia_judge.py（基线版）：https://github.com/lumine-0721/ai-acadmic-ppt-sys/blob/feature/v01-bivia-wiring/v01/src/pptv01/bivia_judge.py

## 给执行者的要求（这就是任务要求本身，不用再问）

**本文件即完整的执行要求：按全文实施修复并运行测试。** 不要反问「概括 / 只读核查 / 实施」——答案是实施。执行要点：
1. 从基线 `feature/v01-bivia-wiring` @ `6df1e59` 新开分支 `feature/codex-t2-fixes`，只在这条分支上改，不动任何既有分支，不合并。
2. 下面 5 项任务全部做，按优先级顺序（任务 1 最先），每项按文中要求补测试；中途不要停下来逐项确认。
3. 红线照下文：零合同变更；Bivia 默认关时行为与基线一致；key 不进代码/日志/提交。
4. 你没有 Bivia key，live 验证如实标 `not_run: no key`，不要假报。
5. 完成后跑 clean-env 全量 pytest（清掉全部 `PPTV01_*` 环境变量），推送新分支，并按下文回执格式逐项交账。
6. 黑板：开工前先拉 `feature/v01-pipeline` 看 `v01/BLACKBOARD.md`（全队在途状态都在上面）；你的进度行已挂在黑板上，状态有变（卡住/干完）时直接改黑板上你那一行，并随 pipeline 分支推送——全队靠黑板对齐，不靠人传话。
**新开分支 `feature/codex-t2-fixes` 从 6df1e59 切出，所有修改只落这条分支。**

## 背景与上下文（先读完再动手）

**这是什么项目。** 一个 AI 学术 PPT 生成流水线（v01）：输入研究主题，经过 A–H 共 8 个阶段（需求、检索、资产、映射、版式、渲染、QA 等），产出学术演示稿。流水线主体是确定性的，LLM 只在规定环节参与；每个阶段的产物都有合同（contracts/schema）约束，下游按合同验收。

**Bivia 是什么，为什么接它。** Bivia 是 Articul8 的布尔判定 API（给一个问题+上下文，返回 yes/no + 概率，可附图）。它在流水线里只当「否决门」：在 C（资产需求）、E（内容映射）、H（QA）三阶段，对「资产需求是否违规要求自绘/编造」「引用是否真的支撑论点」「图片是否对得上要讲的实体」这类是非题做第三方复核。它**不是**主判官：只能否决或弃权，不能给内容发合格证。尤其图片：历史教训 B15——一张纽约照片曾被它以 p=0.97 高置信判成「北京」，所以定版规则是图片永远只有否决票，「yes」绝不能当证明。

**T2 做了什么，你站在哪。** T2 刚把 Bivia 从独立模块接进 C/E/H 三阶段，就是你脚下的基线分支 `feature/v01-bivia-wiring` @ `6df1e59`。批改结论 B+（暂定）：接线骨架和开关、入口、零合同变更都对，问题集中在语义细节——下面 5 项全是批改实锤（代码审计 + 真实 API live 实测双证据），不是猜测。

**这个项目的七条定版规则**（修复不许动摇任何一条）：①Bivia 双开关（环境变量 `PPTV01_BIVIA_JUDGE=1/on` 且有 `A8_API_KEY`）才调用，缺一不调用；②置信阈值 0.90，低于阈值不算数；③判定方弃权或低置信 → 标「待补充」，绝不算通过、也绝不当否决；④图片只有否决票；⑤fail-open：Bivia 挂了/没 key/超时，流水线照常跑，它永远不是硬依赖；⑥统一入口 `BiviaJudge.from_env()`；⑦零合同变更。

**为什么修这 5 项，一句话各一条：** 任务 1——弃权语义现在是空的：请求侧从没要求过弃权，证据不足时 API 只会高置信乱否决，好内容会被误杀而不是进「待补充」，规则③形同虚设。任务 2——阶段层没异常隔离，一处抛错整阶段死，直接违背规则⑤。任务 3——C 阶段把 veto 混进 JSON 校验环节，一次否决会触发 LLM 重试和重复计数，账算错。任务 4——定版的 0.90 阈值能被环境变量静默改掉，门槛可被无声放低。任务 5——两处潜伏小崩溃，顺手拔掉。

**干完之后。** 你的回执会由 miu宝验收（重跑全量测试 + 用真 key 补 live 核验），之后项目定版人复核、负责人拍板合并。所以回执要经得起复核：数字给原文、没做的如实标，别写「全绿」了事。批改全文由 lumine 另附；本任务书自包含，不依赖外部文件。

## 全局红线

1. 零合同变更：contracts / schema / fixture / benchmark 一律不动。
2. 不动既有分支，不合并，不改 `feature/v01-office-renderer`、`feature/miu-integration-aux`。
3. Bivia 默认关（未设 `PPTV01_BIVIA_JUDGE` + `A8_API_KEY`）时，全流水线行为必须与基线逐字一致。
4. API key 不进代码、不进日志、不进提交。
5. fail-open 总原则只能加强不能削弱：Bivia 相关的任何失败都不许打死流水线。

## 任务 1让弃权在 live 真的能触发（批改 Bug 1）

现状实锤：`v01/src/pptv01/bivia_judge.py:246` `judge_boolean` 默认 `mode="fast"`，payload（:265 附近）只含 question/state/mode/think，从不发送 `allow_abstain`；C/E/H 三阶段调用点（c_asset_requirements/stage.py:112、e_mapping/stage.py:295、h_qa/stage.py:181）均不传 mode。live 实测结论：fast 模式对证据不足用例只会高置信回 no（p≈0.998），只有 `mode="deep"` + `allow_abstain=true` 才返回 `insufficient_evidence` 弃权。响应解析侧已支持 abstained → uncertain（bivia_judge.py:322-329），缺的只是请求侧。

要做：
- 接线调用（C/E/H 及 judge_image_veto 路径）改走 deep 语义：调用处显式传 `mode="deep"`，且 payload 增加 `"allow_abstain": true`（与 mode=deep 绑定发送）。
- 保持签名兼容：`judge_boolean` 的默认参数可保留，但接线路径必须显式传参，不许再靠默认值。
- 新增/补强测试（MockTransport 即可）：①断言接线请求 payload 含 `mode="deep"` 与 `allow_abstain=true`；②喂 abstained 响应，断言 verdict 为 uncertain，且 C/E/H 三阶段各自至少一条测试证明 uncertain 落到「待补充」语义（不算 pass、不算 veto）。
- 你没有 Bivia key，live 验证如实标 `not_run: no key`，不许假报；live 复核由 miu宝侧补。

## 任务 2 阶段级 fail-open 隔离（批改 Bug 3）

现状：h_qa/stage.py:378-406 的 Bivia 块只有 try/finally 无 except（:88 pptx 解析亦无保护）；e_mapping/stage.py:519-543 同型。Bivia 相邻代码抛异常会打死整个阶段，与 fail-open 定版规则不符。

要做：给 H、E 两处 Bivia 块补 except 隔离，异常时按既有 not_run 语义记录并继续阶段；C 阶段如有同型结构一并处理。验收：注入会抛异常的 fake judge，断言阶段照常完成且 stats 记 not_run。

## 任务 3 C 阶段 veto 移出校验 lambda（批改 Bug 4）

现状：c_asset_requirements/stage.py:166-175，Bivia veto 嵌在 complete_json 的校验 lambda 里，veto 会触发 LLM 重试并重复计数；test_bivia_wiring.py:285-289 还把此行为固化为预期。

要做：veto 判定移到 complete_json 成功返回之后独立执行，Bivia 计数只记一次；同步修正上述测试预期，并新增测试断言「veto 不触发 LLM 重试、计数为 1」。

## 任务 4 阈值覆盖不再静默（批改 Bug 2）

现状：bivia_judge.py:34, 218-225，`PPTV01_BIVIA_THRESHOLD` 可任意 float 静默覆盖定版 0.90。

要做：保留可覆盖能力（评估工具可能要用），但覆盖生效时必须用模块既有日志口径打一条 warning，含覆盖值与默认值；新增测试断言告警产生且阈值确实生效。不许改默认值 0.90。

## 任务 5两处小修（批改 Bug 5）

- h_qa/stage.py:401-402 稀疏 dict KeyError 潜伏：改安全取值，补一条稀疏输入测试。
- runner.py:711-712 close 不在 finally：移入 finally，保证异常路径也释放。

## 总验收

1. clean-env 全量 pytest：清掉全部 `PPTV01_*` 环境变量后运行，**不得低于基线参照 674 passed / 1 skipped**；有差异逐条说明是环境问题还是回归（注明你的运行平台）。
2. 上列每项任务的新增测试全绿。
3. Bivia 默认关口径下行为与基线一致（全量 pytest 通过即为主要证据，如有 smoke 口径可跑则附结果）。

## 回执格式（推送新分支后按此回）

- 分支名 + 头部 commit hash。
- 逐任务：改动文件（file:line）、做了什么、对应测试名与结果。
- pytest 完整计数与退出码（原文摘要，不许只写「全绿」）。
- 未做 / 受限项逐条如实标（如 live `not_run: no key`），不许省略。
