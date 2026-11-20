# 阶段 H（LibreOffice 渲染 + QA + 有限修复）：现成方案调研 + 融合决定（一页）

日期：2026-11-20。原则：先查现成方案与许可证，真实渲染必须依赖真实工具，缺少工具绝不假装成功。

| 需求 | 候选方案 | 许可证 / 实测与依据 | 决定 |
| --- | --- | --- | --- |
| PPTX 无头渲染为 PDF | [LibreOffice](https://www.libreoffice.org/) `soffice --headless --convert-to pdf` | MPL-2.0（外部 CLI 工具，非 Python 依赖）。使用 `-env:UserInstallation=...` 可避免与桌面实例锁冲突；当前测试机未预装 `soffice`，缺失时必须明确报错并记录 | **采用** `soffice` 真实渲染；环境缺失时诚实报错并失败/记录，绝不用 F 的示意图顶替 |
| PDF 每页转 PNG | Poppler `pdftoppm -png -r 150` | GPL-2.0+（外部 CLI 工具）。输出再按 `slide_001.png` ... `slide_NNN.png` 命名，直接对接 `pptv01 grade --slides-dir runs/<id>/render` | **采用** `pdftoppm`；环境缺失时同样诚实报错并记录 |
| 机械几何/遮挡/溢出/缺图/对比度 QA | `python-pptx`（MIT）+ `f_layout` 容量/几何检查 + `Pillow`（MIT-CMU）像素检查（参考旧仓库 `aprd/.../reconstructor_verifier.py` 中的无头机械检查与对比度校验思路） | 无新依赖；可在 `deck.pptx` 上直接检测溢出、遮挡、越界、字号/比例异常、缺图（blob 损坏/0 字节）与未绑定/不匹配素材 | **自研实现**，覆盖 `QAIssueKind` 全部 6 类问题 |
| 有限次数自动修复 + 复验 | 确定性修复循环（上限 `MAX_REPAIR_ROUNDS = 2`） | 针对 `blocker`/`major`：降字号（不低于字号下限）、扩大或微调文本框避免遮挡/越界、重生成 `deck.pptx` 并**复验**；达上限仍未修复的诚实标为 `recheck = "failed"` | **自研实现** |

## 诚实边界与 PPTEval 衔接
1. **缺少渲染工具绝不装作成功**：若环境中 `soffice` 或 `pdftoppm` 不可用（可注入或通过 `PATH` / 标准安装路径探测），Stage H 仍可先运行结构/PPTX QA 检测并写明渲染器缺失错误，或明确标记 H `failed`（`renderer_unavailable`）并把 `qa_report.json` 中 `rendered_pages = []` 诚实落盘，绝不跳过渲染后把 `run_report.overall` 标为 `complete`。
2. **命名对齐 PPTEval**：渲染成功后图片严格落盘为 `runs/<id>/render/slide_001.png` ... `slide_NNN.png`，与 `pptv01 grade runs/<id> --slides-dir runs/<id>/render` 的 `slide_*.*` 匹配逻辑完全对齐。
3. **不自评**：H 只产出 `render/` 与 `qa_report.json`，不自动调用或伪造 PPTEval 分数。
