---
name: scientific-evidence-asset-acquisition
description: 为学术演示检索和核验科研文献、用户实验材料与可用图表，区分来源、科学证据和素材，检查可比性、冲突、许可与实际获取状态，交付可追溯 EVIDENCE_PACKAGE。用于任务书的科研证据执行、Given 材料审计、定向补证或证据更新；不承担版式参考发现、最终排版或生成 PPT。
---

# 科研证据与素材获取

Skill 3 / Prompt 2。搜索命中不等于证据成立，证据成立也不等于图表可直接上页。

## 入口与边界

1. 执行此技能时完整读取 [用户提供的 Prompt 2 指导](references/prompt-2.md)，使用其中 0–10 节和跨 Skill 设计。附件提到的另一个“完整版/ZIP”没有随文提供；以实际给出的指令和本技能实作接口为依据，不声称读过该安装包。
2. 先读 `PPT_REQUIREMENTS.md`（尤其 03/04/06/08）和 Given；有 `PPT_HANDOFF.json` 则核项目/版本及 C/R/S/V/M/E，沿 S/V→R/E 的具体入口执行。先复用同版本已核材料和缓存；不改任务目的，不重做第 1 技能的需求编译。没有上游文件时可按用户明确问题作局部研究，声明本地临时 ID，不装作上游已确认。
3. 保持来源 `SRCxxx`、证据 `Exxx`、素材 `Axxx` 三本账。现有 E 延续，不重复分配；新 ID 从整个项目的未使用区间分配，删除用退役标记。同一论文中不同实验/主张分别建 E；同一 E 的原图、数据和重绘图分别建 A。
4. 用户限制只用 Given、只做特定 R/S 或只审计时按限制执行。显著改变研究边界或需用户新实验的缺口再询问；其他缺口注明状态与责任方后继续。不将文献结果补成用户的实验结果，不为凑齐包虚构引用、下载、数字或谱图。

## 四种模式

- **FULL RESEARCH**：按任务书和证据缺口选择所需检索层次、实际检索、核证和取材；按完成判据停止，而非以论文数量为目标。
- **GIVEN_FIRST_AUDIT**：实际读取原稿、数据和记录，先审计已有材料，仅在允许且有明确缺口时补查。
- **TARGETED_RETRIEVAL**：只处理指定 R/S；读足其上下游来理解用途，其他条目不重搜、不改、不冒称覆盖。可独立交一个标明范围的增量包；合并进完整包时保留未受影响记录。
- **EVIDENCE_UPDATE**：读取旧包和新增输入，保留反例及有效要求，记录来源/证据/素材、比较和页面状态的变化，按影响复核，不为保住旧叙事删反证。

执行前读 [获取与核验路线](references/acquisition.md)；交包时读 [数据契约与协作](references/package-contract.md)。Prompt 1 的设计参考可并行；其已定位科学素材可以复用，但版式可用、图好看或文件可打开不代表科学主张已核实。

## 实做与交付

实际生成 `EVIDENCE_PACKAGE/` 的 00–06 文件，需要上游决策时才加 07_CHANGE_REQUESTS.md。素材按实际许可和需要留在原处或复制到包内，源文件不改；记录精确定位、获取/核验层级、单位、条件、对照与可用范围。获取不到的内容留缺口，不假装消失。

本技能附 `audit_files.py`（哈希/文件类型/分隔表机械审计）和 `check_evidence.py`（关联、状态、文件指纹、上游版本及影响检查）。脚本检查结构和记录约束，不替代全文阅读、实验判断、许可核验或视觉检查。需要对 PDF 执行 1-based 页级文本、表格、图像边界提取及扫描/空页边界核验时，可在隔离 venv 按 [PDF 溯源探针说明](references/pdf-probe.md) 运行可选的 `scripts/probe_pdf_evidence.py`（机械提取不自动提升为全文核验或转载许可）。完整说明在数据契约中。

本技能同时内建与 PPT 零耦合的通用科研证据与素材核心（`scripts/research_core/`）及 AI PPT 统一命令行入口 `scripts/seaa_cli.py`（支持 `discover` 文献发现去重、`ingest-pdf` 章节与多面板图表提取、`ground` 主张—证据—实验条件锚定、`audit` 科学一致性核查、`synthesize` 跨论文综述矩阵及 `build-package` 编译合规 `EVIDENCE_PACKAGE/`），并可通过 `scripts/export_standalone_core.py` 导出独立母版 `deliverables/research-evidence-asset-core/`。

包交给 Prompt 0 复核新主张或改动，再与 Prompt 1 的同版设计包汇合给下游编排；目前不假定 Prompt 3 技能已经存在。下游无须重搜即可知道证据支持什么、不支持什么、哪些图可用及哪些条件必须保留。报告实际完成、未解决问题与实物路径。

```text
$scientific-evidence-asset-acquisition 读取任务书和实验记录，只审计 Given，不外查，交付证据与素材包。
$scientific-evidence-asset-acquisition 只处理 R005 / S008 的选择性对照证据，保留原有其他记录。
$scientific-evidence-asset-acquisition 用新增文献更新现有证据包，指出哪些主张和页面需要重新审定。
```

