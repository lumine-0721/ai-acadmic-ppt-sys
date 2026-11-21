# Stage F 调研一页：版式多样性与非公文视觉设计（Copy-Reuse-First）

> 目的：解决第 4 轮真机评审中 T12 / T36 截图暴露的 5 类版式问题（同一套浅蓝顶栏+左列表+右大黄框打到底、占位框占主视觉、字小留白大缺大字/引语/满铺图、2010 公文风缺色彩层级、出处脚注漂浮），在动手改写 Stage F / 编译层 / 门槛前，先核对开源成熟方案并明确融合边界。

## 1. 候选方案与许可证核对

| 候选仓库 | 许可证（已核） | 核对路径 / 核心内容 | 取舍与融合决定 |
| --- | --- | --- | --- |
| [slidevjs/slidev](https://github.com/slidevjs/slidev) | **MIT** | `packages/client/layouts/` 内置具名版式：`cover`、`section`、`fact`、`quote`、`statement`、`image-left`、`image-right`、`image`、`two-cols`、`two-cols-header`、`intro`、`full`、`center`、`end` | **融合（核心词汇与版式结构）**：采用其语义化 `layout_id` 命名与视觉角色分工——深色 `cover` / 侧栏 `section` / 大字 `quote`（大引语·论断）/ 强聚焦 `big-number`（对应 `fact`）/ `image-left` & `image-right` / 双栏 `comparison`（对应 `two-cols-header`）/ 满铺 `full-bleed`（对应 `image`）。不引入 Vue/前端运行时。 |
| [presenton/presenton](https://github.com/presenton/presenton) | **Apache-2.0** | 根目录 `layouts.json` 具名版式体系（满铺封面、目录、目标网格、左右分栏、时间线/流程、数据页等） | **融合（结构化槽位声明）**：借鉴「具名版式注册表 + 明确的主槽/副槽/辅助托盘几何规则」，把占位框从主视觉槽拆出到独立的底部紧凑托盘（≤ 15% 内容区面积），并新增 `timeline` 与 `needs-input-list`。不引入其 Docker/Web 后端。 |
| [icip-cas/PPTAgent](https://github.com/icip-cas/PPTAgent) | **MIT** | 论文与代码中的「按页面内容语义与素材条件套版」思想（已在 `docs/stage-e-research.md` 调研） | **融合（选页规则思想）**：在 Stage F 用纯 Python 确定性选择器 `select_deck_layouts` 按页面位置、真实素材数量/宽高比、中英文内容特征（对比/流程/数据/定义/收尾/少字约束）打分选版，再做全局相邻去重与多样性求解。不搬运其多 Agent 运行时。 |
| [gitbrent/PptxGenJS](https://github.com/gitbrent/PptxGenJS) | **MIT** | `defineSlideMaster()` 与多版式示例（不同页使用不同底色、留白、标题位置与排版尺度） | **融合（一页一结构对照）**：参考其通过底色对比（深海军蓝英雄页 vs 浅暖灰引语页 vs 洁白双栏/时间线页）与排版尺度拉开翻页节奏，彻底取消每页重复的浅蓝公文顶栏。不引入 Node/JS 写 PPTX 路径（遵守规则 9）。 |
| [hugohe3/ppt-master](https://github.com/hugohe3/ppt-master) | **MIT** | `slot_capacity.py`（已在 `NOTICE.md` 登记）及 `skills/` 中的页面角色/结构分层（Brand vs Style vs Layout） | **继续复用**：保留已按本机 PowerPoint 16.0 校准的 CJK/Latin `slot_capacity` 算法，并将「配色主题」与「版式几何」解耦。 |
| [pipipi-pikachu/PPTist](https://github.com/pipipi-pikachu/PPTist) | **AGPL-3.0** | 仅查阅版式名称清单作覆盖度对照 | **严格不复制任何代码**（许可证不兼容）。 |

## 2. 本轮融合落地设计

1. **契约与编译层穿透**：
   - `PageDesignEntry` 新增规范字段 `layout_id`（兼容保留 `layout_name`），`DeckOp(add_slide)` 同步写入 `layout_id`；`contracts/page_design.schema.json` 与 fixtures 同步更新。
   - 废除每页硬贴 `bg_header + accent_bar + accent_divider` 的单一公文壳；每种 `layout_id` 由 `src/pptv01/f_layout/layouts.py` 产出独立几何与色彩层级，编译层（`g_build/compile_ops.py`）与 PowerPoint COM 执行器（`renderers/powerpoint_com/executor.py`）按 `z_index` 与 `layout_id` 原样执行（仅使用 Office 16 / Office 2021 基线 API 与允许的结构形状类型）。
2. **规范版式集（10 种现代版式 + 1 种受限旧版式登记）**：
   - 现代版式：`cover`、`section`、`comparison`（别名 `two-cols`）、`big-number`（别名 `fact`）、`timeline`、`image-left`、`image-right`、`quote`、`full-bleed`、`needs-input-list`。
   - 受限旧版式：`header-bullets-box`（含旧名 `split_text_left` / `split_visual_left` / `top_points_multi_visual` / `clean_text_focus`），在 `layout_diversity` 硬门槛里登记为全稿**最多出现 1 页**，Stage F 默认不再生成。
3. **紧凑占位条（根治「黄框当主角」）**：
   - 将 `placeholder`（缺失素材 `U###`）与真实素材（`image`/`chart`/`table`/`diagram_json`）彻底分离：占位绝不进入主视觉槽。
   - 缺失项渲染为页底紧凑虚线提示条（每条 ≤ 12% 内容区面积，多条并排合计 ≤ 15% 内容区面积，且严格小于页面最大内容槽），仍完整保留 `PLACEHOLDER_U###` 命名、`待补充：<need>（U###）` 文案与讲者备注，完全遵守第 3 节不造假产品规则。
4. **真实图主槽与紧贴署名**：
   - 有真实图片时优先进入主视觉槽（`full-bleed` / `cover` 右侧英雄图 / `image-left` / `image-right` / `comparison` 双图），署名 `cap_*` 紧贴图片槽底边（或 `full-bleed` 暗色信息带内），参考文献标注紧贴内容区下方而非漂在页底大片空白处，同时完整写入讲者备注。
5. **硬门槛与 QA 闭环**：
   - `grading/gates.py` 新增硬门槛 `layout_diversity`：强制相邻页 `layout_id` 不重复、页数 ≥ 4 时全稿不少于 `min(5, 页数)` 种版式、旧版式 ≤ 1 页、占位面积 ≤ 15% 内容区且非最大内容槽。
   - `h_qa/geometry.py` 与 `h_qa/inspect.py` 新增 `find_oversized_placeholders`，对 `deck.pptx` 直接检出超大/抢主位的占位框（以 T36 第 1–3 页为反例回归测试）。
