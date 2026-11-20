import json
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

_WINDOWS_COM_CHECKED = False
_WINDOWS_COM_SKIP_REASON: str | None = None


def check_windows_com_skip_reason(*, platform_name: str | None = None) -> str | None:
    """Return a human-readable skip reason if Windows PowerPoint COM cannot run, else None."""
    plat = sys.platform if platform_name is None else platform_name
    if plat != "win32":
        return f"windows_com test skipped: requires Windows desktop (current platform: {plat})"
    try:
        import pythoncom  # noqa: F401
        import win32com.client  # noqa: F401
    except ImportError as e:
        return f"windows_com test skipped: pywin32 not installed ({e})"
    from pptv01.renderers import PowerPointComRenderer, RendererUnavailableError

    try:
        PowerPointComRenderer().preflight()
    except RendererUnavailableError as e:
        return f"windows_com test skipped: PowerPoint COM unavailable ({e})"
    except Exception as e:  # noqa: BLE001
        return f"windows_com test skipped: PowerPoint COM probe failed ({type(e).__name__}: {e})"
    return None


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Auto-skip @pytest.mark.windows_com tests on non-Windows or machines without PowerPoint COM."""
    global _WINDOWS_COM_CHECKED, _WINDOWS_COM_SKIP_REASON
    if item.get_closest_marker("windows_com") is None:
        return
    if not _WINDOWS_COM_CHECKED:
        _WINDOWS_COM_SKIP_REASON = check_windows_com_skip_reason()
        _WINDOWS_COM_CHECKED = True
    if _WINDOWS_COM_SKIP_REASON is not None:
        pytest.skip(_WINDOWS_COM_SKIP_REASON)


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
    "retrieval_brief": [
        {"topic": "城市热岛的定义与度量", "why": "为概念页提供出处",
         "queries": [{"text": "城市热岛", "lang": "zh"}, {"text": "urban heat island intensity", "lang": "en"}]},
    ],
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


# need refs in GOOD_B: S001#1 image (某城市热像图, to_verify); S002#1 diagram (no target)
GOOD_C = {
    "requirements": [
        {"need_refs": ["S001#1"], "page_ids": ["S001"], "type": "image",
         "purpose": "在第一页并排展示城区与郊区的温度分布，让听众直观看到城区更热",
         "targets": ["某城市热像图"], "query_hints": ["urban heat island thermal image", "城市 热像图 城郊对比"],
         "priority": "required",
         "acceptance": ["与城市热岛直接相关，能同时看出城区与郊区的差异", "可合法使用：许可明确（CC 许可或公有领域）",
                        "须核实所选城市与图像确实对应"]},
        {"need_refs": ["S002#1"], "page_ids": ["S002"], "type": "diagram",
         "purpose": "画出地表材料、人为排热和通风三类成因如何共同抬高城区气温",
         "targets": [], "query_hints": ["urban heat island causes diagram"], "priority": "optional",
         "acceptance": ["与三类成因直接对应", "取用现成图须许可明确，或由用户提供带来源的结构化图"]},
    ],
    "dropped_needs": [],
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Unit tests never touch the network or a real LLM: scrub PPTV01_* and disable research."""
    import os
    for k in list(os.environ):
        if k.startswith("PPTV01_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("PPTV01_RESEARCH", "none")
    monkeypatch.setenv("PPTV01_RELEVANCE", "structural")   # never load an ML model in unit tests
    monkeypatch.setenv("PPTV01_ASSETS", "none")            # never hit Commons/OpenAlex from unit tests


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    import pptv01.retrieval.backends as b
    monkeypatch.setattr(b, "_sleep", lambda s: None)
