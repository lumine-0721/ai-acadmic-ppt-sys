# APRD Fusion Engine CLI (`scripts/aprd_cli.py`)

`scripts/aprd_cli.py` 提供从参考 `.pptx` 提取设计规范、编译 `PPT_REFERENCE_PACKAGE`、执行忠实复刻（Level 2）与跨领域学术设计泛化（Level 3）、调用 PowerPoint COM 实测渲染与 `BoundHeight` 溢出校验、以及管理本地 SHA-256 设计资产缓存注册表的完整工程流水线。

## 1. 提取机器可消费的设计规范 (`extract`)

```powershell
python SKILL_DIR/scripts/aprd_cli.py extract INPUT.pptx DESIGN_SPECIFICATION.json --ref-id REF001 --registry ./asset_registry
```
- 解析 OOXML Theme（含 `clrMap`、`clrMapOvr`、`lumMod`/`lumOff`/`tint`/`shade`/`satMod`/`alpha` 及全白坏主题修复）、Master `p:txStyles` 字号阶梯、Layout/Master 继承占位符与 `grpSp` 仿射世界坐标；
- 自动剥离源 PPT 科研敏感正文（`content_scrubbed: true`），提炼 12 列网格、安全区、对齐关系图、内容槽位（`SlotContract` + 中英文二分容量估算）、跨页去重布局模式与 13 类学术角色映射；
- 输出通过 `schemas/design-specification.schema.json`（Draft 2020-12）验证的 `DESIGN_SPECIFICATION.json`。

## 2. 一键编译兼容设计包 (`compile-package`)

```powershell
python SKILL_DIR/scripts/aprd_cli.py compile-package INPUT.pptx ./PPT_REFERENCE_PACKAGE --handoff PPT_HANDOFF.json --design-revision 1
```
- 生成完整通过 `check_package.py validate --production` 的六件套、覆盖报告、`DESIGN_SPECIFICATION.json` 与直通 Orchestrator 的 `layouts.json`（含 `0.0 ~ 1.0` 归一化 `slot_ast`）。

## 3. 忠实复刻 (`replicate`) 与跨领域设计泛化 (`generalize`)

```powershell
# Level 2 — 忠实复刻 / 槽位替换
python SKILL_DIR/scripts/aprd_cli.py reconstruct --mode replicate --spec DESIGN_SPECIFICATION.json --source INPUT.pptx --payload slot_payload.json -o replicated.pptx

# Level 3 — 跨领域学术设计泛化（零旧正文残留，适配不同篇幅与新内容）
python SKILL_DIR/scripts/aprd_cli.py reconstruct --mode generalize --spec DESIGN_SPECIFICATION.json --payload new_academic_plan.json -o generalized.pptx
```

## 4. 真实渲染、溢出检测与视觉相似度验证 (`verify`)

```powershell
python SKILL_DIR/scripts/aprd_cli.py verify generalized.pptx --spec DESIGN_SPECIFICATION.json --render-dir ./render_out --report verification_report.json
```
