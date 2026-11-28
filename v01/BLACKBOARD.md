# 黑板（BLACKBOARD）— 全队共享的在途状态

位置：`feature/v01-pipeline` 分支 `v01/BLACKBOARD.md`，和 HANDOFF 放在一起。
用途：**没改完的东西写在这里，另一个人拉一下分支就能看见。** HANDOFF 记正式任务书和批改，这块黑板只记「此刻谁在干什么、卡在哪」。

## 规矩（就四条）

1. 开工先看黑板：自己要动的文件/分支若已有人挂着，先在黑板喊停对齐，不许闷头撞车。
2. 干活挂牌：开始一项就加一行——负责人、分支、状态、一句话进度、更新时间。
3. 状态只用四种：`进行中` / `卡住`（必须写卡在哪）/ `待接手` / `待验收`。
4. 干完擦掉：完成的行直接删（历史在 git 里），黑板上只留在途的。

## 在途（按更新时间倒序）

| 负责人 | 事项 | 分支 | 状态 | 进度一句话 | 更新 |
|---|---|---|---|---|---|
| Codex | F7 分层级联 + 7 条漏检修复（甲），再按 S1→S2→S3 分段合并进 pipeline（乙） | 新开 `feature/codex-t2-f7`（父 8e697c1）；合并目标 `feature/v01-pipeline` | 进行中 | 已从 8e697c1 开工实现 F7 级联、预算、trace 与回放；S3 等 Miu宝验收。 | 2026-11-28 |
| Miu宝 | **交接**：宝特/云宝/文宝可能 2026-11-29 起冻结（lumine 可能不续订），项目派任务、验收、写任务书由 Miu宝 接手 | `feature/v01-pipeline` | 待接手 | 先读 `v01/HANDOFF-miu-takeover-2026-11-27.md`（交接总书，含验收步骤、分支地图、基线、资产索引）；调研原件已抄进 `v01/docs/research/` | 2026-11-27 |
| Miu宝（原宝特） | F7 甲部分回执验收：F7 已定版 (b) 分层级联，合并顺序 S1 office-renderer → S2 Keenable/Firecrawl 集成 → S3 Bivia 接线 + 修复 + F7 | `feature/codex-t2-f7`（只读验收） | 待接手 | 等 Codex 回执；按交接总书 §4.1 逐条验收，通过后在 Codex 行写「甲部分验收通过」才放行 S3。关单前不在生产跑次里开 Bivia | 2026-11-27 |
| Miu宝（原宝特） | 第 9 轮任务书（版式即数据 / 选版式 / 主题选择，原 T-18） | `feature/v01-pipeline` | 待接手 | 调研已完成并入库 `v01/docs/research/round9_port_survey.md`（含 Miu宝评审与更正）；等 S3 合完 + lumine 拍板 5 个未决项（见交接总书 §4.2）后再写 | 2026-11-27 |
| 基米 | T2-F6：T3 R9 冲突备忘全文落盘进 HANDOFF | `feature/v01-pipeline` | 待接手 | 目前只有口头四点摘要，无档可查；批改意见已在 T2 批改全文里 | 2026-11-27 |

## 冻结与决定（动之前先看）

- 合并一律由 lumine 拍板，任何人不得自行合并。
  - 2026-11-27：lumine 把 T2/F7 这条栈的合并决定委托给宝特。宝特已定 S1→S2→S3 分段合并，只限 `feature/v01-pipeline`，由 Codex 凭 `v01/HANDOFF-codex-2026-11-27-f7.md` 乙部分执行。三条冻结分支本身仍然只读。
- 2026-11-27：宝特可能 11-29 起冻结，原宝特的验收权（含 F7 甲部分）移交 Miu宝；合并顺序和任务书不变。
- 冻结中：`feature/v01-office-renderer`、`feature/miu-integration-aux`、`feature/v01-bivia-wiring`（基线，只读参照）。
- 零合同变更是全队红线：contracts / schema / fixture / benchmark 不动。
