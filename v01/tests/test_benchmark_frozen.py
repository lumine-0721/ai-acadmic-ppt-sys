import json
from pathlib import Path

B = Path(__file__).resolve().parents[1] / "benchmark" / "v1.0"


def test_benchmark_v1_shape():
    tasks = json.loads((B / "tasks.json").read_text(encoding="utf-8"))
    layers = json.loads((B / "layers.json").read_text(encoding="utf-8"))
    ids = [t["id"] for t in tasks["tasks"]]
    assert tasks["frozen"] is True and tasks["version"] == "1.0" and len(ids) == 40 == len(set(ids))
    assert layers["layers"]["full"]["ids"] == ids
    assert [len(layers["layers"][k]["ids"]) for k in ("smoke", "regression", "full")] == [4, 12, 40]
    assert set(layers["layers"]["smoke"]["ids"]) <= set(layers["layers"]["regression"]["ids"]) <= set(ids)
