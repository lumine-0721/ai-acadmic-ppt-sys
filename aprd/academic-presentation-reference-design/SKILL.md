---
name: academic-presentation-reference-design
description: 为科研组会、文献汇报、开题、答辩等学术演示发现并核验 PPT 参考，逆向给定 PPTX/PDF/截图的视觉结构，把参考转成统一设计系统、可重建版式、逐页映射和覆盖报告。用于设计参考研究或向 PPT 制作端交付规范；按任务书定位和核对上页素材；独立文献证据检索、内容编译或直接改稿不由本技能负责。
---

# 学术演示参考发现与设计规范

**Prompt 1**：用真实参考解决学术页面的承载问题，交付 `PPT_REFERENCE_PACKAGE/`，而不是链接清单、素材拼贴或最终 PPT。

1. 真正进入设计参考任务时，**从头到尾读取并使用** [Prompt 1 完整原文](references/prompt-1.md)：按其十个阶段、三种模式和 20 项自审执行。只咨询命名或取舍时，答该问题，不启动检索或创建文件。用户对外查、指定模板、品牌和最终格式的明确约束优先；缺少任务书时，根据已有材料建可调整的需求画像，不把暂定设计当作上游已确认的科学结论。
2. 有任务书先读 00/04/06/07/08 节及相关证据说明，沿 S/V→R/E 读取 APC 给的素材入口、精确定位和必选/可选要求；有 `PPT_HANDOFF.json` 时核对项目与版本，沿用 S/V（及必要的 C/E）编号。单独逆向给定参考、没有目标页面时，只做参考包，不虚构目标映射。按 [实作路线](references/execution.md) 选择工具并记载实际检索、获取、解析和渲染结果。
3. **A · FULL DISCOVERY**：先盘点 Given 和硬约束；按页面功能生成检索画像，再在公开且可用来源中分层发现候选。**B · GIVEN REFERENCE**：先打开、核验、解析给定样本；仅当有缺口且用户允许时补查。**C · REFERENCE REVISION**：用新任务书、旧包和反馈定位受影响 S/V，保留仍适用的 REF/LAYOUT 和主风格；记录重审与变更，不默默改任务书。
4. 按[定向核验与返还](references/execution.md#定向核验与返还)处理关键页：按 S/V 分开跑科学素材与版式参考路线，先开 APC 指定入口并检查目标；失效时按原因换路线，不从首页泛搜。逐项返还实际定位、核验层级和当前能否上页；版式通过不代表素材通过。没有任务书的独立参考任务仍按既有流程，不新增文献检索任务。
5. 设计锚点负责全局颜色、字体、网格；补充参考只贡献可迁移的版式。记录真实提取、视觉估计、设计推导和未知，不用缩略图认定母版参数。优先保证轴标、图例、条件、结构式、对照和引用；布局不合适时换版、放大或反馈拆页，而不是为了套版改科学结论。
6. 按 [设计包契约](references/package-contract.md) 写出 Prompt 1 的六个文件和独立覆盖报告；需要上游决策时附 `DESIGN_FEEDBACK.json`。运行校验、重开设计包、给出关键缺口和下游使用顺序。最终 PPT 由用户另外要求的制作阶段完成。

可选：需要继承占位符几何及分层字体/颜色声明时，参见 [inspect_design 工具说明](references/inspect-design.md)，在隔离 venv 安装本技能可选依赖后运行 `scripts/inspect_design.py INPUT.pptx NEW.json`。该只读工具保留未知项、不渲染，默认防覆盖；原 `inspect_pptx.py` / `check_package.py` 行为不变。

工程增强（APRD Fusion Engine）：需要完整的 OOXML 主题/母版级联解析、内容槽位容量估算、跨页布局聚类、`DESIGN_SPECIFICATION.json` 编译、`PPT_REFERENCE_PACKAGE` 自动生成、跨领域学术设计泛化重建及 PowerPoint COM 真实渲染验证时，参见 [APRD Fusion Engine 说明](references/aprd-engine.md) 并调用 `scripts/aprd_cli.py`。

```text
$academic-presentation-reference-design 读取 PPT_REQUIREMENTS.md 和我给的参考 PPTX；只用给定材料，按 S/V 输出设计包和版式缺口。
$academic-presentation-reference-design 根据新版任务书和旧设计包，复审受影响的页面，沿用仍适用的风格和版式。
```
