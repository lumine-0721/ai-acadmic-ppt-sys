import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture
def fx():
    def load(name):
        return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return load


GOOD_A = {
    "topic": "城市热岛效应", "purpose": "general_presentation", "audience": "general",
    "language": "zh-CN", "style": "clear_educational", "constraints": [],
    "defaults_applied": [
        {"field": "audience", "value": "general", "rationale": "用户未指定受众"},
        {"field": "style", "value": "clear_educational", "rationale": "未指定风格"},
    ],
    "content_direction": "从现象、成因、影响和应对几条线索展开。",
    "open_questions": ["如何度量城市热岛强度？"],
}

GOOD_B = {
    "narrative": "先建立概念，再讲成因与影响，最后讨论应对。",
    "pages": [
        {"title": "什么是城市热岛", "goal": "区分热岛与热浪", "key_points": ["城区相对周边更热"],
         "content_summary": "定义与对比。", "visual_intent": "对比示意图", "asset_needs": [{"type": "image", "description": "并排展示城区与郊区的温度分布", "targets": [{"name": "某城市热像图", "entity_type": "dataset", "verification": "to_verify"}]}]},
        {"title": "热岛从哪里来", "goal": "说出主要成因", "key_points": ["地表材料差异", "人为排热"],
         "content_summary": "逐个成因讲机制。", "visual_intent": "成因机制图", "asset_needs": [{"type": "diagram", "description": "画出三类成因如何抬高城区气温", "targets": []}]},
    ],
}
