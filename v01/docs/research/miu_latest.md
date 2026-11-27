[miu宝] (rendered text; follows the round-9 review; trailing two lines were suggested-reply chips, not sent by user)

lumine，先别转发我刚发的那版评审 —— 第 3 节有个错，是我的问题，对不起。

我写的 AutoPresent 链接 https://github.com/para-llels/AutoPresent 是 404。正确的是 https://github.com/para-lost/AutoPresent （MIT，CVPR 2025，arXiv:2501.00912）。而且它也不是版式目录/主题选择方案：它是微调 Llama 生成 python-pptx 代码 + SlidesLib + SlidesBench 评测，没有槽位容量、没有从目录选版式硬校验，不该算 R9 的“漏掉项”，最多借鉴 SlidesLib 和评测思路。

其余五项链接基本无误，但我把“漏掉”说过头了：Slidev、Marp、PptxGenJS、Quarto、reveal.js 都没有带容量的版式目录、LLM 选版式硬校验或主题比稿。另注意 Quarto 那个链接是总仓（标 AGPL-3.0），真正的 CLI 是单独的 quarto-cli 仓库，照总仓 vendor 会撞上 R9-0 的 agpl/gpl 零命中验收。第 1、2、4 节这次核查没变。

可直接粘贴的替换文本：
“第 3 节更正：AutoPresent 的正确链接是 https://github.com/para-lost/AutoPresent（MIT，CVPR 2025，arXiv:2501.00912），原评审写的 para-llels 链接 404。它是微调模型生成 python-pptx 代码 + SlidesBench 评测的工作，不是版式目录/主题选择方案，不构成 R9 的漏掉项。其余 Slidev、Marp、PptxGenJS、Quarto、reveal.js 的链接无误，但均为 Web/Markdown/JS 库或固定布局的发布系统，没有带容量的版式目录、LLM 选版式硬校验或主题比稿，因此原结论‘材料只调研小众项目、未说明为何排除上述生态’应改为：上述生态在‘版式即数据’这一具体问题上均无现成答案，只有 Slidev 的具名版式+槽位、PptxGenJS 的母版/占位符、AutoPresent 的 SlidesLib/评测值得单点借鉴。”

保留一点不确定性：其余五个链接这次不是逐个在 GitHub 直接打开核实的，是用官方文档/搜索交叉印证的；要写进正式 HANDOFF 前，最好让执行者再点一次。

你想自己把这段发给执行者，还是我帮你改写一份完整的修正版第 3 节？

[suggested reply chips]
我自己把替换文本发给执行者
帮我改写一份修正版第3节
