# PPT_HANDOFF 结构 Schema 与多错误诊断工具

`scripts/check_handoff_schema.py` 是 APC 的可选辅助诊断工具，复用 `jsonschema`（`Draft202012Validator`）对 `PPT_HANDOFF.json` 执行一次性多错误结构校验，并串联 `check_handoff.py` 的跨记录语义检查。

## 边界说明

- **不替代 `check_handoff.py`**：默认 `scripts/check_handoff.py` 仍是零第三方依赖的标准入口。
- **结构通过不等于科学主张成立**：JSON Schema 仅校验字段类型、ID 命名模式、状态枚举、退役标记布尔类型和本记录必填项；主张是否有合格证据支持、版本影响范围与任务书章节对齐仍由语义检查与人工/实测把关。
- **扩展字段向后兼容**：Schema 保留 `additionalProperties: true`（在已命名的 `M/C/E/R/V/S` 记录内），允许未来或实验性属性共存而不破坏旧索引。

## 隔离安装与运行

在独立虚拟环境中安装固定版本依赖：

```text
python -m pip install -r SKILL_DIR/requirements-schema.txt
python SKILL_DIR/scripts/check_handoff_schema.py PPT_HANDOFF.json --requirements PPT_REQUIREMENTS.md --strict-assets --out diagnostics.json
```

若目标 `--out` 文件已存在，工具默认拒绝覆盖。
