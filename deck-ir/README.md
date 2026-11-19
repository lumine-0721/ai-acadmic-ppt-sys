# Academic Deck IR
独立的 Python 3.11+ 包。输入是经过各生产者检查的 JSON 工件；输出是可编辑、可复核的 `academic-deck-ir/3`。本包不启动 Orchestrator、PowerPoint 或网络请求。

从仓库根：
```sh
python -m pip install ./deck-ir
python -m unittest discover -s deck-ir/tests -v
python -m academic_deck_ir deck-ir/examples/complex.json --resource-root deck-ir/examples
```

```python
from academic_deck_ir import new_deck, new_slide, edit, dumps, loads, inspect_deck

base = new_deck("seminar", "方法讨论")
slide = new_slide("如何检验重复性？", "说明测量设计", "question")
deck = edit(base, "author", ("create_page", "S001", 0, slide),
            ("insert", "S001", "body", {"kind":"text","runs":[{"text":"列出重复测量条件"}]}, None, 0))
assert loads(dumps(deck)) == deck
assert inspect_deck(dumps(deck))["pages"][0]["objects"][0]["id"] == "body"
```

- [输入输出、编辑与版本契约](docs/spec/academic-deck-ir/contracts.md)
- [审计、依赖图和验收条件](docs/spec/academic-deck-ir/spec.md) · [技术选择与任务依赖](docs/spec/academic-deck-ir/plan.md)
- [正式 Schema](src/academic_deck_ir/schemas/deck-v3.json) · [验证记录](docs/spec/academic-deck-ir/evidence.md)
- `src/academic_deck_ir/`：model（结构与图约束）、operations（编辑与并发合并）、adapters（工件与迁移）、consumer（验收替身）；`examples/` 是标注为测试用途的本地示例，`tests/` 为 unittest。
