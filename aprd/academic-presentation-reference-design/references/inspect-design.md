# inspect_design 可选设计结构与继承检查器

`scripts/inspect_design.py` 是 APRD 的可选只读增强工具，基于固定版本的 `python-pptx==1.0.2` 补充两类设计证据：

1. **继承占位符几何（Placeholder Geometry）**：当幻灯片占位符未声明本页 `a:xfrm` 时，复用 `python-pptx` 公开属性（`shape.left` / `top` / `width` / `height`）沿 `slide -> layout -> master` 链回溯有效 EMU 坐标，并标明实际提供该坐标的源部件与 `shape_id`。
2. **带来源标签的样式与主题声明（Source-Labelled Style Declarations）**：盘点 Slide、SlideLayout、SlideMaster 与 Theme 中的字号、字体、颜色映射和填充声明；仅当颜色为无修饰 `srgbClr` 时给出 `unmodified_srgb`，对其余主题色、亮度/透明度修饰、母版级联和真实渲染边界显式标记 `unknown`。

## 边界与安全规则

- **不替代 `inspect_pptx.py` 与 `check_package.py`**：原有零第三方依赖的脚本保持不变。
- **非渲染证据**：`rendered` 固定为 `false`；旋转框、组合变换、文本自动缩放和实际视觉外观需通过独立渲染（如只读 PowerPoint 导出 PNG 或 PDF 渲染）确认。
- **只读与防覆盖**：不修改输入 PPTX，校验前后源文件 SHA-256 一致；若输出 JSON 路径已存在则拒绝覆盖。

## 隔离安装与运行

```text
python -m pip install -r SKILL_DIR/requirements-inspect-design.txt
python SKILL_DIR/scripts/inspect_design.py INPUT.pptx OUTPUT_NEW.json
```
