# Ported/adapted from zarazhangrui/frontend-slides (MIT): bold-template-pack/selection-index.json.
"""Topic- and style-driven theme selection over `theme/library/selection-index.json` (R7-3)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from pptv01.contracts import ProductionPlan, SlidePlan
from pptv01.llm import AgentClient, ChatClient, complete_json
from pptv01.theme.tokens import DEFAULT_THEME_ID, THEMES, load_selection_index


@dataclass(frozen=True)
class ThemeSelection:
    theme_id: str
    rationale: str
    method: str  # "agent" | "llm" | "keyword_fallback" | "explicit"


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


def _flatten_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return " ".join(_flatten_text(v) for v in value)
    return str(value or "")


def _rule_select_theme(
    production: ProductionPlan,
    slide_plan: SlidePlan | None = None,
) -> ThemeSelection:
    index = load_selection_index()
    presets: dict[str, Any] = index.get("presets") or {}

    # Topic & style carry higher weight than full transcript/slide plan prose
    high_signal = _normalize_text(
        " ".join(
            [
                _flatten_text(production.topic),
                _flatten_text(production.style),
                _flatten_text(production.purpose),
                _flatten_text(production.audience),
            ]
        )
    )
    broad_signal_parts = [
        _flatten_text(production.content_direction),
        _flatten_text(production.retrieval_brief),
        _flatten_text(production.source_utterance),
    ]
    if slide_plan is not None:
        broad_signal_parts.extend(p.title for p in slide_plan.pages)
        broad_signal_parts.extend(p.visual_intent for p in slide_plan.pages)
    broad_signal = _normalize_text(" ".join(broad_signal_parts))

    best_tid = DEFAULT_THEME_ID
    best_score = -1.0
    best_hits: list[str] = []

    for tid in sorted(THEMES):
        meta = presets.get(tid) or {}
        keywords = [str(k).casefold() for k in meta.get("keywords", []) if str(k).strip()]
        best_for = [str(b).casefold() for b in meta.get("best_for", []) if str(b).strip()]
        avoid_for = [str(a).casefold() for a in meta.get("avoid_for", []) if str(a).strip()]

        score = 0.0
        hits: list[str] = []

        for kw in keywords:
            in_high = kw in high_signal
            in_broad = kw in broad_signal
            if in_high:
                score += 3.0 if len(kw) >= 2 else 1.5
                hits.append(kw)
            elif in_broad:
                score += 1.2 if len(kw) >= 2 else 0.6
                hits.append(kw)

        for bf in best_for:
            for tok in re.findall(r"[a-z0-9\u4e00-\u9fff]{2,}", bf):
                if tok in high_signal:
                    score += 1.6
                    hits.append(tok)

        for av in avoid_for:
            for tok in re.findall(r"[a-z0-9\u4e00-\u9fff]{3,}", av):
                if tok in high_signal:
                    score -= 1.0

        # Small baseline so generic unspecialized academic decks cleanly resolve to academic-default
        if tid == DEFAULT_THEME_ID:
            score += 0.5

        if score > best_score:
            best_score = score
            best_tid = tid
            # Deduplicate preserving order
            seen: set[str] = set()
            best_hits = [h for h in hits if not (h in seen or seen.add(h))]

    chosen_meta = presets.get(best_tid) or {}
    scheme = chosen_meta.get("scheme") or "balanced"
    moods = ", ".join(chosen_meta.get("mood") or [chosen_meta.get("tone") or "structured"])
    cue_txt = "、".join(best_hits[:6]) if best_hits else "通用学术结构与平衡风格"
    tokens = THEMES[best_tid]
    rationale = (
        f"关键词兜底：匹配主题 `{best_tid}`（配色 {scheme} / 调性 {moods} / 字体 {tokens.title_font}+{tokens.font_family}），"
        f"依据：topic=`{production.topic}`、style=`{production.style}` 命中关键词 [{cue_txt}]。"
    )
    return ThemeSelection(theme_id=best_tid, rationale=rationale, method="keyword_fallback")


_TH_SYSTEM_PROMPT = """\
你是演示文稿视觉设计总监（阶段 TH：主题选择）。
根据制作计划中的主题（topic）、目的（purpose）、受众（audience）和风格（style），从给定 8 套主题索引中挑选最契合的一套，并用一到两句话说明理由。

要求：
1. theme_id 必须是给定 themes 字典里的合法键名之一；
2. rationale 必须是一到两句清晰的中文（或与题目语言一致）的理由，说明为何匹配受众、风格与主题语境，不能为空；
3. 只输出一个 JSON 对象，格式为 {"theme_id": "...", "rationale": "..."}，不要 Markdown 或额外解释。
"""


def _parse_theme_choice(raw: Any) -> tuple[str, str]:
    if not isinstance(raw, dict):
        raise ValueError("theme selection output must be a JSON object")
    cand = str(raw.get("theme_id") or "").strip()
    rat = str(raw.get("rationale") or "").strip()
    if cand not in THEMES:
        raise ValueError(f"unknown theme_id {cand!r}; must be one of {sorted(THEMES)}")
    if not rat:
        raise ValueError("rationale must be a non-empty 1-2 sentence explanation")
    return cand, rat


def select_theme(
    production: ProductionPlan,
    slide_plan: SlidePlan | None = None,
    *,
    client: ChatClient | Any | None = None,
) -> ThemeSelection:
    """Select an appropriate theme from the 8-theme library using Stage TH (agent/LLM) or keyword fallback.

    When an active ``client`` is supplied, always runs Stage TH (``TH_request_<n>.json`` / ``TH_response_<n>.json``
    on AgentClient) with strict validation and NO silent keyword fallback. Keyword matching runs ONLY when
    ``client is None`` and records ``method='keyword_fallback'``.
    """
    if client is None:
        return _rule_select_theme(production, slide_plan)

    index = load_selection_index()
    presets = index.get("presets") or {}
    catalog = {
        tid: {
            "mood": (presets.get(tid) or {}).get("mood", []),
            "tone": (presets.get(tid) or {}).get("tone", ""),
            "formality": (presets.get(tid) or {}).get("formality", ""),
            "density": (presets.get(tid) or {}).get("density", ""),
            "scheme": (presets.get(tid) or {}).get("scheme", ""),
            "best_for": (presets.get(tid) or {}).get("best_for", []),
            "avoid_for": (presets.get(tid) or {}).get("avoid_for", []),
        }
        for tid in sorted(THEMES)
    }
    user_payload = {
        "topic": production.topic,
        "purpose": production.purpose,
        "audience": production.audience,
        "style": production.style,
        "themes": catalog,
    }
    messages = [
        {"role": "system", "content": _TH_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(user_payload, ensure_ascii=False, indent=2),
        },
    ]
    if hasattr(client, "set_stage"):
        client.set_stage("TH")

    res = complete_json(client, messages, _parse_theme_choice)
    cand, rat = res.value
    desc = client.describe() if hasattr(client, "describe") else {}
    method = "agent" if (isinstance(client, AgentClient) or desc.get("backend") == "agent") else "llm"
    return ThemeSelection(theme_id=cand, rationale=rat, method=method)
