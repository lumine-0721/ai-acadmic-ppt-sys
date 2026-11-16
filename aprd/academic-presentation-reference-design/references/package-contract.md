# PPT_REFERENCE_PACKAGE 契约

## 文件

| 文件 | 职责 |
| --- | --- |
| `REFERENCE_REPORT.md` | 需求画像、检索画像及日志、环境、候选与六维比较、锚点及补充选择、许可、逐页分析索引、缺口、变更记录。 |
| `SOURCE_MANIFEST.json` | 实际来源和观察边界；字段见下。 |
| `DESIGN_SYSTEM.json` | 主锚点、设计版本、画布/网格/边距/颜色/字体等统一规范和逐参数来源。 |
| `LAYOUT_LIBRARY.json` | 可复用原型、原始页码和几何、功能、容量、约束和调整规则。 |
| `SLIDE_STYLE_MAPPING.json` | 项目与上游版本、逐页 S/V、版式和状态。 |
| `RECONSTRUCTION_GUIDE.md` | 以可执行顺序说明全局规则、几何、对象、适配、素材来源、制作注意事项和验收。 |
| `REFERENCE_COVERAGE_REPORT.md` | 独立覆盖报告，按每个 S/V 或独立模式的语义类型记录状态、关键证据元素是否容纳、缺口及下一步。有 S 时按映射顺序，每页一行，首两列写作 `| S001 | COVERED |`（可接其他列）。 |
| `DESIGN_FEEDBACK.json` | 可选：给 Prompt 0 的独立提案，有上游依赖且需要改内容/页序/约束时创建。 |

`inspection/REFxxx.json`、`inspection/REFxxx.md`、渲染样张、来源链接可作为支持物证。报告列出位置；可把受限参考保留在原处，只交付允许传播的路径说明和抽象版式。不要把第三方原稿打入设计包默认分发。

## JSON 形状（UTF-8）

- **SOURCE_MANIFEST**：对象 `{"references":[...]}`。每条：`ref_id`（REF001 等）、`title`、`source_url_or_path`、`source_type`（template/deck/page）、`file_format`、`access_status`、`inspection_level`、`page_count_if_verified`（未知为 null）、`inspected_pages`（已核对的正整数页码）、`rights_status`、`editability_status`、`selected_role`、`usage_notes`。可加作者、实际本地文件、SHA-256、原始探测记录、评分和外部依赖。用本技能的 PPTX 解析结果时，把 JSON 放入包内；用 `inspection_artifact` 指向包内相对路径，`sha256` 对应其源文件摘要（小写）。未接触页用空数组；编号按来源显示顺序，而不是 PPTX 文件名。
  - access_status: `discovered/opened/acquired/limited/unavailable`
  - inspection_level: `discovery/landing/thumbnail/page_visual/document_parsed/object_parsed`；实际渲染是额外维度，在报告/证据中标记。仅 landing 或 thumbnail 不能作为已选设计参考；仅 object_parsed 不表示目视过。
  - rights_status: `direct_use/reference_only/needs_permission/unavailable/unknown`；selected_role: `primary/supplement/rejected/candidate`。主参考最多一个。选为参考不等于获准套用，使用原稿须有已确认的相应权利。
- **DESIGN_SYSTEM**：`design_revision`（正整数）、`primary_anchor_ref_id`（未选源时 null）、`canvas`（`width`、`height`、`unit`，明确换算）、`grid`、`margins`、`colors`、`typography`、`spacing`、`shapes`、`components`、`graphics`、`image_treatment`、`chart_style`、`consistency_rules`、`constraints`。对每个关键值在 `provenance` 中写 `parameter`、`precision`（`exact/estimated/derived/unknown`）、`ref_id`（无来源时 null）、`basis`（具体 XML 字段/图像/页码/推导）。选择采用的样式和原稿观测分开；exact 表示参数实际精确读取或测量。
- **LAYOUT_LIBRARY**：对象 `{"layouts":[...]}`。每条至少 `layout_id`（LAYOUT001 等）、`source_ref_id`、`source_slide`（原创时两者为 null）、`semantic_role`、`normalized_geometry`、`element_hierarchy`、`alignment_rules`、`fixed_constraints`、`variable_parameters`、`content_capacity`、`adaptation_rules`、`scientific_content_compatibility`、`precision`。geometry 每项 `{slot,x,y,w,h}`，左上 0,0、右下 100,100，安全范围内，合法重叠需在层级说明。抽象/重绘版式用 derived，只有对已检查来源的真实精确量测用 exact。记录正常/超载两种处理。
- **SLIDE_STYLE_MAPPING**：`scope`（`project` 或 `reference_only`）、`project_id`（独立任务无已确认项目时 null）、`base_revision`（无上游索引时 null）、`design_revision`、`target_slide_ids`（原顺序）、`mappings`。独立参考研究两组页均留空，在覆盖报告按角色而非编造 S。项目每项有 `target_slide_id`、`required_content_type`、`selected_layout_id`（阻塞时可 null）、`reference_source`（同所选版式来源 ID，原创为 null）、`visual_requirements`（已知 V ID 列表）、`adaptation_instructions`、`layout_risks`、`unresolved_requirements`、`coverage`（`COVERED/PARTIAL/ORIGINAL/BLOCKED`）。COVERED 指已核对且满足；ORIGINAL 指已核对的自主布局；PARTIAL/BLOCKED 必写剩余问题。已完成的覆盖不能留未解决项。
- **DESIGN_FEEDBACK**：沿用第 1 技能回执头：`project_id`、`base_revision`、`stage`（`design-reference`）、`task_ids`、`owner`、`inputs`、`acceptance`、`output_dir`、`result`（accepted/partial/blocked/conflict）、`affected_ids`、`artifacts`、`observations`、`proposed_changes`、`checks`。在 `proposed_changes` 逐条写 `kind`（`DESIGN_GAP` / `PRESENTATION_OPPORTUNITY`）、`affected_ids`、`problem`、`proposal`、`preserve`。需要上游采纳才更改 S 或内容，反馈不是授权写入。

## 校验、迭代和版本

用实际路径运行：

```text
python SKILL_DIR/scripts/check_package.py validate PPT_REFERENCE_PACKAGE
python SKILL_DIR/scripts/check_package.py validate PPT_REFERENCE_PACKAGE --handoff PPT_HANDOFF.json --production
python SKILL_DIR/scripts/check_package.py impact OLD_PACKAGE NEW_PACKAGE
```

`--handoff` 核对上游项目、修订号、S 顺序及各页直接依赖的 V。映射反映任务书设计需求，不代表 PPT 页已通过科学或渲染验收。`--production` 拒绝未映射/部分覆盖/阻塞的项目**版式**交接；独立设计研究不使用该开关。科学素材是否齐备不由 JSON 覆盖状态推出：按定向回执查关键 V 的实际入口、许可、可读性和当前画法；硬依赖未过仍需阻止制作端把该页当成可出最终版。

Mode C 在项目草稿目录保留上个已发布设计包，再生成新版。保留同义 REF/LAYOUT；某个 ID 代表不同来源或不同版式用途时新建 ID。项目 ID 与上游版次由 Prompt 0 管；design_revision 是独立连续设计版本（一个上游版本可有多次设计修订）。`impact` 比较映射、上游顺序、所用版式和来源的变化，以及全局设计系统的变更，输出需复审页面；更改全局颜色、字体或网格时核对全稿。内容改了但 S/V 和映射未变时，仍需使用 Prompt 0 的影响输出或收到的具体回执补充复审范围。变更记录列明影响、实际复审结果和沿用项，不把脚本的候选页列表当成已检查。
