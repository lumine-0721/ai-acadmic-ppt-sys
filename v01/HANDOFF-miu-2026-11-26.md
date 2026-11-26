# HANDOFF — 给基米（Antigravity）的任务书（miu宝代宝特格式，2026-11-26）

> 读者：接手写代码的 Agent（基米）。本文自包含：读完即可开工，不需要别的上下文。
> 仓库：https://github.com/lumine-0721/ai-acadmic-ppt-sys ，项目目录 `v01/`
> 分工：**你负责 Windows 侧实现与验证**；评分/定稿仍归宝特，你不改基准试卷，也不自己给自己评分定稿。合并由 lumine 定。
> 发出人：miu宝（本轮代宝特写任务书，内容已经 lumine 过目）

---

## 0. 先说清：你看不见的东西，别猜、别重造

下面三样**不在仓库里**，光靠本文交代不了，遇到就按标注处理，不要凭空补：

1. **两个 miu 分支的代码**（见第 1 节）——已于 2026-11-26 推上远端，直接 `git pull` 即可：
   `feature/miu-a8k-aux` @ `726097e`、`feature/miu-firecrawl-aux` @ `66789ea`（均基于 `feature/v01-office-renderer` @ `8f5a999`）。
   拉不到分支 = 停下，在汇报里写 `blocked: branch not on remote`，**不要**按本文的文字描述自己重写一遍。
2. **所有密钥**（`A8_API_KEY`、`KEENABLE_API_KEY` 等）——只从环境变量来，不进代码/日志/提交。
   你本地没有 key 时，相关实调记 `not_run: no key`，用单测的 MockTransport 结果说话即可，不要找 lumine 在聊天里要明文 key。
3. **miu宝本机的环境故障**——本 VM 的 `no_proxy` 含 IPv6 字面量会让 httpx 构造崩（`InvalidURL: ':1]'`）。
   这是本机独有问题，Windows/清环境不受影响；你在 Windows 上若全绿，正好就是它的定性证据，不要为它改仓库代码（是否做仓库层容错，留宝特判断）。

## 1. 现状增量（自上一份 HANDOFF 后新增，已验收）

基线：`feature/v01-office-renderer` @ `8f5a999`（smoke8 已完成 G/H）。

### (1) A8+K 辅助组件 —— 已验收 `accepted`，代码在分支 `feature/miu-a8k-aux`
- Bivia 判定席模块 `v01/src/pptv01/bivia_judge.py` + 12 单测；Keenable 检索 backend（`retrieval/backends.py` / `provider.py`）+ 11 单测；调研页 `v01/docs/aux-bivia-keenable-research.md`
- 全量 pytest：648 passed（基线 625 + 新增 23），2 failed 与基线相同、已定性为上面第 0.3 条环境问题，非回归
- Bivia 实调复验 10 题全对；Keenable 真题 T02（城市热岛）2 search + 2 fetch 全 200
- **未做**：Bivia 还**没有**接进 E/H/C 阶段代码，只留了接线口 `BiviaJudge.from_env()` —— 这正是下面任务 T2
- 远端提交 `726097e`（8 文件，+1049/−1），内容与 miu宝本地验收版一致

### (2) Firecrawl 辅助组件 —— 已验收，代码在分支 `feature/miu-firecrawl-aux` @ `66789ea`（远端提交；本地原提交 `7bae607`，内容相同）
- 新增 `v01/src/pptv01/retrieval/firecrawl_backend.py`（keyless backend）+ 8 单测 + 调研页 `v01/docs/stage-d-firecrawl-research.md`；`provider.py` / `d_retrieve/stage.py` 各小改
- 定位：D 阶段检索的「硬页面升级通道」，**不是**主力检索（主力仍是 Keenable 管量）
- miu宝清环境复跑全量 pytest exit 0；已知偏差（已接受）：D 阶段 scrape 挂钩延后（D 没有抓网页正文的路径，硬造是死代码）、配额闸做进 backend 本体

### (3) 第 9 轮评审（修正版）—— 下一轮输入，不是本轮任务
- 修正要点：第 3 节原结论说过头了。AutoPresent 正确仓库是 `para-lost/AutoPresent`（MIT，CVPR 2025），它和 Slidev / Marp / PptxGenJS / Quarto / reveal.js 都**没有**带容量（min/max 字数条数）的版式目录、LLM 选版式+硬校验、主题比稿——R9 的版式目录方案在这些生态里没有可直接移植的现成实现，只有单点借鉴（Slidev 具名版式+槽位、PptxGenJS 母版/占位符、AutoPresent 的 SlidesLib/评测）
- 第 1、2、4 节不变：目录防手填容量/公式生成、holdout 禁 fallback、砍 R9-0 整段 vendor / R9-5 .potx spike / 目录减半到 8 个核心版式
- 本轮你只需读完并在汇报里写一句：它与你当前 `feature/v01-office-renderer` 的实现有没有冲突、冲突在哪。**不要**在本轮动手改 R9 相关代码。

## 2. 硬规则（沿用宝特原规矩，违反打回）

1. 分支：从任务指定的分支拉自己的分支；`feature/v01-pipeline` 只放任务书/调研/冒烟答案，不推代码。
2. 密钥只读环境变量；默认关、双 env 门、fail-open 的组件，未启用时行为与改动前完全一致。
3. 诚实状态：做不到记 `missing` / `not_run` / `needs_user_input` 并写原因，不许让流程显示假成功；不许编造数据、图表、来源。
4. 改契约必须同步 schema / fixture / 测试，并在汇报里写清改了什么、为什么。
5. 先调研后写：引入第三方做法先写一页调研（候选、许可证、取舍），不整包接入、不拼两套流程。

## 3. 本轮任务（按顺序做）

### T1 Windows 侧复验两个 miu 分支（先做这个，做完先汇报）
- 分别拉 `feature/miu-a8k-aux`、`feature/miu-firecrawl-aux`，在**清环境**（unset 全部 `PPTV01_*`）下跑全量 `pytest`，看 pytest 自身退出码，不要接 `| tail`。
- 验收：给出每个分支的用例数、退出码；特别注明 A8+K 那 2 条在本 VM 失败的用例（`test_llm.py::test_key_never_in_repr_or_describe`、`test_retrieval.py::test_research_from_env_modes`）在 Windows 上是否通过——这是第 0.3 条的定性证据。
- 拉不到任一分支：停下写 `blocked`，不要重写代码顶替。

### T2 Bivia 接线进 E/H/C（T1 全绿、且 lumine 点头后才开工）
- 接线口：`BiviaJudge.from_env()`。规则（宝特定版，不许改口径）：
  - 双开关：`PPTV01_BIVIA_JUDGE=1` 且 `A8_API_KEY` 存在才启用，缺一静默关闭
  - 只做是/否把关：高置信 yes = pass，高置信 no = veto，**阈值 0.90**
  - 弃答/低置信 = 标「待补充」，**绝不**算通过
  - 图片判定**只作 veto 票**，yes 永远不作 proof（教训：B15 纽约照冒充北京，高置信误判）
  - fail-open：Bivia 挂了流水线照常跑，记 `not_run`，不许变成硬依赖
- 验收：新增单测覆盖五条规则（MockTransport，不出网）；默认关时 E/H/C 行为零变化；全量 pytest 清环境 exit 0；Smoke 4 跑一遍看 `run_report`。

### T3 R9 冲突备忘（轻量，可与 T1 并行）
- 读第 1.(3) 节，对照当前 office-renderer 实现，汇报里列：无冲突 / 冲突点（文件:行 + 一句原因）。只写备忘，不改代码。

## 4. 你要汇报什么（每次汇报必须包含）

1. 分支名与提交 hash 列表（一句话说明每个提交做了什么）
2. 清环境全量 pytest 结果（命令、pytest 自身退出码、用例数）
3. 对应任务验收清单逐条自检（做了 / 没做 / 做不到及原因）
4. 新增调研一页的路径、引入的第三方代码及许可证（如有）
5. 若改了契约：改了什么、为什么、schema/fixture/测试是否同步
6. 已知新弱点或未完成项；被卡住的写清 `blocked` + 卡点

做不到就如实写，不要用假数据凑验收项。
