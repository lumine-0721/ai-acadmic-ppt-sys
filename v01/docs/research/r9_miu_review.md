1. 最可能失败／钻空子

（1）目录成了新阈值仓库。 禁改阈值，却允许执行者手写16份JSON的min/max_chars、box、ideal_chars、rhythm。把容量放宽、盒子放大、常用版式标breathing，即可让fixture和occupancy全过，等于把0.52搬进数据。堵：目录先于fixture冻结并记hash；容量只能由盒宽高、字号、行高公式生成，禁手填；留出集运行前目录变更即失败；统计豁免率并设上限。

（2）兜底和人工答案使LS形同虚设。 rule_fallback按角色+组数映射，本就能100%命中*-N；Smoke9又由宝特预写LS/TH答案，实际只测编译器，不测选版式。reasoning/runner_up只要是非空字符串、且runner_up≠theme/layout就能过，可事后编。堵：holdout中layout_method/theme_method必须100%为agent/llm，出现fallback即失败；Smoke分开报“编译回放分”和“在线选择分”；reasoning必须引用候选版式的实测字数/条数违规值，由校验器复算。

（3）行数与grep验收可转移代码。 layouts.py≤800行、禁函数名，只会把builder拆到新模块、通用解释器，或把条件编码进JSON的box/anchor/变体；0.52可写.52、52/100、Fraction(13,25)绕rg；“无数值比较选版式”的单测也可改查表分发绕过。堵：统计F阶段全部.py+.json总行数与文件白名单，新文件需批准；用AST查比较、字典分发和按layout_id分支，不只grep字面量。

2. 模糊／无法客观判定

“有合理替代理由”无量表：分组页即使0%命中*-N，也可凭主观理由全过，与100%指标冲突。
目检“一眼就能分出来”“至少4种结构，不只是装饰不同”：无判定人、盲评、结构指纹定义。
min/typical/max fixture的typical无定义；“occupancy/overflow通过”未在本轮重述具体阈值、测量版本。
主题“可接受家族≥90%”：家族由谁映射、20题中错2题即达标，样本太小且domain自报可迎合。
R9-4子串lint：同义词、拆词、英文译名即可绕过，中文分词规则未定。
R9-5“继承有没有问题、能不能导出”无成功阈值、页数外样本和失败判据。
“相邻同版式只warning”与整体多样性目检脱节，可全程2种版式仍不违规。

3. 漏掉的成熟开源方案

注：以下偏“版式/主题即数据”的底座，非同类AI选版式系统，但恰可对照其成熟做法：

Slidev：https://github.com/slidevjs/slidev
Marp：https://github.com/marp-team/marp
PptxGenJS：https://github.com/gitbrent/pptxgenjs
Quarto：https://github.com/quarto-dev/quarto
reveal.js：https://github.com/hakimel/reveal.js
AutoPresent（不确定是否与所述同类、链接凭记忆）：https://github.com/para-llels/AutoPresent

材料只调研小众PPT生成项目，未说明为何排除上述生态的layout/theme声明、母版继承和渲染校验经验。

4. 砍掉一半，先砍这些

R9-0整段vendor： 只留来源清单、协议和自写摘要，抄prompt/规则段耗时且易腐烂。
R9-5 .potx spike： 与本轮主病无关，推迟到第10轮决策前。
R9-4五步比稿： 只保留删keywords、 agent单步选主题、禁选校验；anti_pick/runner_up/domain全砍。
目录规模减半： 砍standard/compact双档、process_timeline-N、content_caption，先做8个核心版式；48份golden fixture随之减半。
错误信息中的“建议替代版式”： 只报槽位、预算、实际值，重试由模型自行重选。

保留的只能是：容量schema、硬校验、通用编译器、删除打分选版式。
