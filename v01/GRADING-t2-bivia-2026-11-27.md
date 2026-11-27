# T2 Bivia 接线批改（miu宝代行宝特）

- 对象：`feature/v01-bivia-wiring` @ 6df1e59（基线集成分支 `feature/miu-integration-aux` @ 119cb6a）
- 批改人：miu宝（宝特本周回不来，lumine 2026-11-26 改派代行）
- 日期：2026-11-26 晚
- 证据报告：`../hidden_files/grading-evidence-2026-11-26.md`（四路取证：规则审计 / 独立复验 / Bivia live / T3 备忘）

## 给分：A−（2026-11-27 验收后上调，原 B+）

原批改：5 条 PASS、2 条 PARTIAL（均非主干造假）；回执 diff 数字逐字属实；live 缺口已补；B 路独立复验完成（pytest 对账严丝合缝）。
2026-11-27 Codex 修复分支 `feature/codex-t2-fixes` @ 8e697c1 验收通过（混合验证：miu宝亲自审计 diff + live 复核，小弟独立复跑 pytest 对账），B+ → **A−**。未给 A 的原因：验收中新发现 F7（见文末）——deep 模式下文本否决被弃权大面积替代，是定版口径问题，待 lumine/宝特定夺后再议。

## 七条定版规则逐条核

| # | 规则 | 结论 | 证据 |
|---|------|------|------|
| ① | 双开关才调用 | PASS | bivia_judge.py:78-86 `bivia_enabled()` 双条件；:200-215 未启用返回 None；test_bivia_wiring.py:208-224 覆盖缺失组合 |
| ② | 阈值 0.90 | PARTIAL | 默认/判定达标（:37、:341）；但:34 `PPTV01_BIVIA_THRESHOLD` 可任意 float 静默覆盖（基线既有、非本 diff 引入） |
| ③ | 弃权/低置信标「待补充」 | 代码 PASS / live PARTIAL | 代码路径成立（:323/:341 → uncertain；C/E/H 三阶段落地一致）；但 live 下弃权分支永不触发，见 Bug 1 |
| ④ | 图片只有否决票 | PASS | bivia_judge.py:398-406 图片 pass 强制改写 `not_vetoed`；E/H 只走 `judge_image_veto`；test:315-324 断言 yes@0.99 不提升 relevance |
| ⑤ | fail-open | PARTIAL | API 层全 fail-open（:291-310）；但 H/E 阶段层无 except 隔离，见 Bug 3 |
| ⑥ | 入口 from_env() | PASS | C stage.py:152、E:527、H:376 回退 from_env()；runner.py:216 统一创建传三阶段 |
| ⑦ | 零合同变更 | PASS | diff 实测 6 files +1110/−31，与回执逐字相符；contracts/schema/fixture/benchmark 零命中 |

回执措辞小错：9 个新测试中 test_bivia_wiring.py:194 是 backend 排序测试，「9 条 MockTransport 测试」不精确（实为 8 条）。

## Bug 清单

1. **弃权 live 语义不成立** — bivia_judge.py:246 `judge_boolean` 默认 `mode="fast"`，:265 payload 只含 question/state/mode/think，从不发送 `allow_abstain`；三阶段调用点（c_asset_requirements/stage.py:112、e_mapping/stage.py:295、h_qa/stage.py:181）均不传 mode。live 实测：fast 模式对证据不足用例不弃权（no p=0.9978，加 allow_abstain 重跑逐字相同），只有 deep + allow_abstain 才返回 insufficient_evidence。后果：证据不足被当高置信 veto（方向偏严、不误放行，但会误杀，且与规则③「待补充」语义不符）。（终审补查：miu宝经 GitHub 直读接线分支源码确认，2026-11-26）
2. **阈值可被 env 静默覆盖** — bivia_judge.py:34, 218-225（基线既有，非本 diff 引入）。
3. **阶段层无异常隔离** — h_qa/stage.py:378-406 只有 try/finally 无 except（:88 pptx 解析亦无保护）；e_mapping/stage.py:519-543 同型。Bivia 相邻代码异常可打死整个阶段，fail-open 只在 API 层成立。
4. **C 阶段 veto 触发点混淆** — c_asset_requirements/stage.py:166-175 veto 嵌在 complete_json 校验 lambda 里，会触发 LLM 重试 + 重复计数；test_bivia_wiring.py:285-289 把此行为固化为预期。
5. **** h_qa/stage.py:401-402 稀疏 dict KeyError 潜伏；runner.py:711-712 close 不在 finally。

## Bivia live 核验（基米侧 not_run 缺口，已补）

12 次调用 / 10,000 billable tokens（模型实查 bivia v0.2.0）：
- 应通过 2/2（yes p=0.9964 / 0.9951）；应否决 2/2（no p≈0.996）；图片 2/2 判断准（live 层双向，代码层强制否决票语义已由规则④审计证明）。
- 关键发现即 Bug 1 的 live 证据。附带：连接器自带 client.py 不支持 images/allow_abstain/mode 参数（客户端缺口，非 API 缺口）。

## 独立复验：已完成（2026-11-26 晚，lumine 同意后续跑；证据 hidden_files/grading-evidence-b-2026-11-26.md）

隔离 clone HEAD 核对 = 6df1e59 ✓。clean-env（清尽全部 PPTV01_*）全量 pytest 连跑两遍一致：收集 675 条，**662 passed / 5 failed / 8 skipped，exit 1**。5 条失败全部为已知环境病（httpx 解析本 VM no_proxy 中方括号 IPv6 字面量报 `Invalid port: ':1]'`），对照实验实证：仅净化 no_proxy 重跑 5/5 全绿 exit 0，**零代码回归**。
与回执对账：662 + 5（环境病，Windows 通过）+ 7（门控 skip 在 Windows 实跑通过）= **674 passed**；skip 8 − 7 = **1 skipped**——与基米回执严丝合缝，无无法解释的差额。8 条 skip 全部有据（6 条 Windows-COM 平台门、2 条未装 rapidocr extras）。另本 VM 需补装 numpy（模块级 import，Windows 由可选 extras 带入，属环境差异非缺陷）。
**Smoke 4：本机未复验（非红非绿）。** 四题逐题实跑均 exit 2 卡在入口配置门——本 VM 无 LLM 后端配置（PPTV01_LLM_* 只在基米机器上），阶段 A 之前即被挡下。基米「Smoke 4 绿」维持回执状态，既未证实也未证伪；如需独立复验，须先给本 VM 配一个 LLM 后端再跑。

## T3 R9 冲突备忘：取舍意见

注：备忘全文未落盘（工作区/远端 pipeline/记忆均无），以下据基米汇报四点摘要 + 基线 8f5a999 代码核证。

1. **capacity 公式化：部分采纳 / 缓行。** 手填属实（capacity.py:241-382 共 14 份）；但 estimate_slot_capacity（:56-105）只算字符级，推不出结构性 min_points。max 可公式化，但每-point 字数预算须做成全局单常数、冻结并记 hash；min/ideal 豁免，定性为语义资格线。
2. **砍 8 版式：采纳，但备忘漏报。** LAYOUT_ALIASES 已存在（layouts.py:76-91），是扩充非新建；14 个注册属实，另有第 15 个 legacy `header-bullets-box` 须裁定；必红测试应点名（test_r6_3:27,184、test_round4:244,488）；删注册 ≠ 删 builder（layouts.py 共 4040 行）。
3. **LLM 选版式：方向采纳，「可复用」夸大。** validate_page_design（validate.py:86）不查 layout 合规，只能当第二层；硬校验须从 layouts.py:450-473 资格过滤抽出、新写 validate_layout_choice()。且全仓无 `layout_method` 字段（对照 contracts.py:706 有 theme_method）——契约缺口备忘没点出。
4. **theme holdout 拆分：采纳，证据最硬。** test_r8_2:57-69 正用 holdout 12 case 给 keyword_fallback 发合格证，与 R9 禁兜底正面对撞；拆分干净（select.py:143-158），但兜底单测须换 fixture，最终须在产物 theme_method 上验收。
- 备忘漏掉的实锤：0.52 常数还活着（layouts.py:143）；get_layout_capacity() 对别名/未知 ID 静默兜底返回 section spec（capacity.py:392-396）；keywords 字段本身未删（select.py:69-87）。

## 后续任务（编号下发）

- **T2-F1（合并前置）** 修 mode：接线调用传 `mode="deep"` 且 payload 加 `allow_abstain=true`，让弃权分支 live 可达；或书面论证接受 fast 语义并同步修订规则③口径。
- **T2-F2（已清，pytest 部分）** B 路复验补跑完成，pytest 与回执对账一致（见上）；Smoke 4 本机未复验（无 LLM 配置），如 lumine 要求独立复验需另配后端，不再列为合并前置，状态如实存档。
- **T2-F3** 阈值覆盖治理：锁死 0.90，或 env 覆盖时显式日志告警。
- **T2-F4** H/E 阶段层补 except 隔离，落实阶段级 fail-open。
- **T2-F5** C 阶段 veto 移出校验 lambda，消除重试重复计数，并修正固化该行为的测试预期。
- **T2-F6** 基米把 T3 备忘全文落盘进 HANDOFF —— 现在只有口头摘要，无档可查，是流程缺口。

## 合并意见：建议合并（2026-11-27 更新）

T2-F2 已清；Codex 修复分支已验收通过（见下）。修复分支内容达到可合并标准——合并拍板仍归 lumine，本批改只给意见。F7 口径建议在「真开启 Bivia 跑生产」之前定，不必卡合并本身（judge 默认关，合并不会启用它）。

## 附：Codex 修复分支验收（2026-11-27，混合验证）

证据：hidden_files/codex-fix-acceptance-2026-11-27.md（小弟跑量）+ miu宝亲自审计/live 记录（本节）。
- HEAD/父提交：8e697c1 / 6df1e59 ✓，基线之上仅 1 个提交，无夹带；diff 7 文件 +315/−113，零合同变更 ✓。
- pytest 独立复跑（本机 clean-env、净化 no_proxy）：687 collected，679 passed / 0 failed / 8 skipped，exit 0；与回执 686/1 算术对账严丝合缝（差额 = 6 Windows-COM 门 + 2 rapidocr − 1 LibreOffice），较基线 +12 条与回执一致；Bivia 专项 33 passed ✓。零回归。
- 五项修复逐项亲验：①五处接线显式 mode="deep"（c:124、e:256/:303、h:157/:189），allow_abstain 仅 deep 时入 payload（bivia_judge.py:307-308）；②C 逐条 except（c:139-141）、E 阶段级 except（e:538-545）、H 含 PPTX 读取 except（h:384-390），均记 not_run 继续；③C veto 已移至 complete_json 返回后（run_stage_c 结构），计数恰一次；④阈值 warning 在 bivia_judge.py:263，默认仍 0.90；⑤H 稀疏 stats 改 .get（h:403-404），close 进 bivia_session 的 finally 且外传 judge 不关（bivia_judge.py:161-166）。
- live 复核（走修复后代码 from_env）：证据充分 → pass p=0.9966 ✓；证据不足 → uncertain/abstained ✓（小弟探针）；**直白矛盾（埃菲尔铁塔在柏林 vs 引用说巴黎）→ uncertain/abstained，不是 veto**（miu宝亲测与小弟探针一致；同题 fast 模式 veto p=0.9974）。

## 新发现 F7（非 Codex 缺陷，定版口径待定）

deep + allow_abstain 的 API 行为是「有疑即弃权」：弃权修通的同时，文本否决被大面积转成「待补充」。方向 fail-safe（弃权≠通过：E 不接受、H 记待复核、C 留注记），但 Bivia 的自动否决职能在文本路径上被实质削弱。三选一待 lumine/宝特拍板：(a) 接受现状（judge 定位改为 pass/弃权门，否决靠人工复核待补充）；(b) 分层：fast 先行判明显矛盾（保住 veto），不确定再走 deep 弃权；(c) 暂维持，等宝特复核后再定。在定口径前，不建议在生产跑次里开启 Bivia。
