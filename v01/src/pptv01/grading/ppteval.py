"""PPTEval-style grading (Content / Design / Coherence, 1-5), adapted from PPTAgent's PPTEval (MIT).

Prompts are PPTAgent's, verbatim (ppteval_prompts/). What we adapted (also recorded in every grade_report.json):
  * placeholders are filled with str.replace (no jinja2 dependency);
  * the judge may be an OpenAI-compatible MLLM API or the agent backend (宝特 by hand, labelled, not an API model);
  * evidence level: ``pptx_render`` = rendered slide images (the real PPTEval flow: describe image -> score description);
    ``plan_text_proxy`` = slide_plan.json text only, because no PPTX exists before stages G/H. The proxy scores
    Content and Coherence on text and marks Design ``not_assessable`` - it never invents a Design score;
  * pass rule is ours (benchmark spec 6.3: every dimension >= 3), not PPTEval's.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import httpx

from pptv01.contracts import (
    DimScore, GradeReport, GradeSummary, GraderIdentity, RubricInfo, SlideGrade, SlidePlan, SCHEMA_VERSION,
)
from pptv01.llm import (
    AGENT_MODEL_ID, AgentClient, ChatClient, LLMConfigError, LLMRequestError, Messages, complete_json,
)

PROMPTS = Path(__file__).with_name("ppteval_prompts")
RUBRIC = RubricInfo(
    source_url="https://github.com/icip-cas/PPTAgent", source_ref="v1.1.38 (2e68c095a86bdbb91635dc4d91dad4662aba163c)",
    license="MIT", copyright="Copyright (c) 2025 ICIP-CAS",
    paper_url="https://arxiv.org/abs/2501.03936",
    deviations=[
        "prompts verbatim; placeholders filled by str.replace",
        "pass rule is every dimension >= 3 (benchmark spec 6.3), not PPTEval's mean scores",
        "slide-level Content/Design are averaged per deck; Coherence is one score per deck",
    ],
)

ENV_BACKEND = "PPTV01_GRADER_BACKEND"          # api | agent
ENV_BASE_URL = "PPTV01_GRADER_BASE_URL"
ENV_API_KEY = "PPTV01_GRADER_API_KEY"
ENV_MODEL = "PPTV01_GRADER_MODEL"              # text judge
ENV_VISION_MODEL = "PPTV01_GRADER_VISION_MODEL"  # image describer (defaults to ENV_MODEL; must be multimodal)
ENV_EXCHANGE = "PPTV01_GRADER_EXCHANGE_DIR"
ENV_TIMEOUT = "PPTV01_GRADER_TIMEOUT_S"


def _prompt(name: str) -> str:
    return (PROMPTS / f"{name}.txt").read_text(encoding="utf-8")


class GraderConfigError(LLMConfigError):
    pass


# --------------------------------------------------------------------------- graders
class _ApiChat:
    """OpenAI-compatible chat that can attach images ("images": [path]) to a user message. Never logs the key."""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float, transport: httpx.BaseTransport | None):
        self._url, self._key, self.model_id = base_url.rstrip("/"), api_key, model
        self._http = httpx.Client(timeout=timeout, transport=transport)

    def set_stage(self, stage: str) -> None:
        pass

    def describe(self) -> dict[str, Any]:
        return {"backend": "api", "model": self.model_id}

    def chat(self, messages: Messages) -> str:
        out = []
        for m in messages:
            imgs = m.get("images") or []
            if imgs:
                parts: list[dict[str, Any]] = [{"type": "text", "text": m["content"]}]
                for ip in imgs:
                    b64 = base64.b64encode(Path(ip).read_bytes()).decode()
                    mime = "image/png" if str(ip).lower().endswith(".png") else "image/jpeg"
                    parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
                out.append({"role": m["role"], "content": parts})
            else:
                out.append({"role": m["role"], "content": m["content"]})
        try:
            r = self._http.post(f"{self._url}/chat/completions", json={"model": self.model_id, "messages": out, "temperature": 0},
                                headers={"Authorization": f"Bearer {self._key}"})
        except httpx.HTTPError as e:
            raise LLMRequestError(f"grader request failed: {type(e).__name__}") from None
        if r.status_code != 200:
            raise LLMRequestError(f"grader HTTP {r.status_code}: {r.text[:300]}")
        try:
            return r.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise LLMRequestError("grader response was not a valid chat completion") from None


@dataclass
class Grader:
    text: ChatClient          # judge (and describer when evidence is text)
    vision: ChatClient        # image describer
    identity: GraderIdentity

    def close(self) -> None:
        for c in (self.text, self.vision):
            getattr(c, "close", lambda: None)()


def grader_from_env(env: Mapping[str, str] | None = None, transport: httpx.BaseTransport | None = None) -> Grader:
    """PPTV01_GRADER_BACKEND=api (default) needs BASE_URL/API_KEY/MODEL; =agent needs EXCHANGE_DIR. No mock fallback."""
    env = os.environ if env is None else env
    backend = (env.get(ENV_BACKEND, "api").strip().lower() or "api")
    if backend == "agent":
        d = env.get(ENV_EXCHANGE, "").strip()
        if not d:
            raise GraderConfigError(f"{ENV_BACKEND}=agent requires {ENV_EXCHANGE}")
        c = AgentClient(Path(d))
        return Grader(c, c, GraderIdentity(kind="agent", label=AGENT_MODEL_ID))
    if backend != "api":
        raise GraderConfigError(f"unknown {ENV_BACKEND}={backend!r}; use 'api' or 'agent'")
    missing = [k for k in (ENV_BASE_URL, ENV_API_KEY, ENV_MODEL) if not env.get(k, "").strip()]
    if missing:
        raise GraderConfigError("grader is not configured; set " + ", ".join(missing)
                                + ". Grading never falls back to made-up scores.")
    try:
        timeout = float(env.get(ENV_TIMEOUT, "120"))
    except ValueError:
        raise GraderConfigError(f"{ENV_TIMEOUT} must be a number") from None
    model = env[ENV_MODEL].strip()
    vmodel = env.get(ENV_VISION_MODEL, "").strip() or model
    base, key = env[ENV_BASE_URL].strip(), env[ENV_API_KEY].strip()
    return Grader(_ApiChat(base, key, model, timeout, transport), _ApiChat(base, key, vmodel, timeout, transport),
                  GraderIdentity(kind="api", label=f"api:{model}", model=model, vision_model=vmodel))


# --------------------------------------------------------------------------- scoring
def _parse_score(raw: Any) -> DimScore:
    if not isinstance(raw, dict):
        raise ValueError("judge output must be a JSON object with reason and score")
    sc = raw.get("score")
    if isinstance(sc, bool) or not isinstance(sc, (int, float)) or int(sc) != sc:
        raise ValueError(f"score must be an integer 1-5, got {sc!r}")
    return DimScore(score=int(sc), reason=str(raw.get("reason") or "").strip() or "(no reason given)")


def _judge(client: ChatClient, prompt: str, stage: str, trace: list) -> DimScore:
    client.set_stage(stage)
    res = complete_json(client, [{"role": "user", "content": prompt}], _parse_score)
    trace.append({"stage": stage, "attempts": res.attempts})
    return res.value


def _fill(name: str, **kw: str) -> str:
    t = _prompt(name)
    for k, v in kw.items():
        t = t.replace("{{" + k + "}}", v)
    return t


@dataclass
class SlideEvidence:
    index: int
    page_id: str | None = None
    text: str = ""                 # slide text (plan proxy) or extracted text
    image: Path | None = None      # rendered slide image (pptx_render)


def slide_text_from_plan(plan: SlidePlan) -> list[SlideEvidence]:
    out = []
    for i, p in enumerate(plan.pages, 1):
        lines = [f"Title: {p.title}", f"Goal: {p.goal}", "Key points:"] + [f"- {k}" for k in p.key_points]
        lines.append(f"Content summary: {p.content_summary}")
        lines.append(f"Visual intent: {p.visual_intent}")
        if p.asset_needs:
            lines.append("Planned assets: " + "; ".join(f"{a.type.value}: {a.description}" for a in p.asset_needs))
        out.append(SlideEvidence(i, p.page_id, "\n".join(lines)))
    return out


def grade_deck(evidence: list[SlideEvidence], grader: Grader, *, run_id: str, task: str = "",
               level: str, background_text: str = "") -> tuple[GradeReport, list[dict]]:
    """Run PPTEval. ``level`` is 'pptx_render' (every slide has an image) or 'plan_text_proxy' (text only)."""
    if not evidence:
        raise ValueError("nothing to grade")
    if level == "pptx_render" and any(e.image is None for e in evidence):
        raise ValueError("pptx_render grading needs a rendered image for every slide")
    trace: list[dict] = []
    slides: list[SlideGrade] = []
    for e in evidence:
        if level == "pptx_render":
            grader.vision.set_stage(f"describe_s{e.index:03d}")
            c_descr = grader.vision.chat([{"role": "user", "content": _prompt("ppteval_describe_content"), "images": [str(e.image)]}])
            s_descr = grader.vision.chat([{"role": "user", "content": _prompt("ppteval_describe_style"), "images": [str(e.image)]}])
            design = _judge(grader.text, _fill("ppteval_style", descr=s_descr), f"design_s{e.index:03d}", trace)
            status = "scored"
        else:
            c_descr, s_descr, design, status = e.text, "", None, "not_assessable"
        content = _judge(grader.text, _fill("ppteval_content", descr=c_descr), f"content_s{e.index:03d}", trace)
        slides.append(SlideGrade(index=e.index, page_id=e.page_id, content=content, design=design, design_status=status,
                                 content_descr=c_descr if level == "pptx_render" else "", style_descr=s_descr))
    # coherence: extract per-slide descriptions + metadata, then score (PPTEval's two steps)
    pres_text = background_text or "\n\n".join(f"Slide {e.index}:\n{e.text}" for e in evidence)
    grader.text.set_stage("coherence_extract")
    ext = complete_json(grader.text, [{"role": "user", "content": _fill("ppteval_extract", presentation=pres_text)}],
                        lambda r: r if isinstance(r, dict) else (_ for _ in ()).throw(ValueError("extract output must be an object")))
    trace.append({"stage": "coherence_extract", "attempts": ext.attempts})
    coh = _judge(grader.text, _fill("ppteval_coherence", presentation=json.dumps(ext.value, ensure_ascii=False)),
                 "coherence", trace)
    summary = summarise(slides, coh)
    rep = GradeReport(run_id=run_id, graded_task=task, evidence_level=level, rubric=RUBRIC, grader=grader.identity,
                      graded_at=datetime.now(timezone.utc).astimezone(), slides=slides, coherence=coh,
                      background_extracted=ext.value.get("background", {}) if isinstance(ext.value.get("background", {}), dict) else {},
                      summary=summary)
    return rep, trace


def summarise(slides: list[SlideGrade], coherence: DimScore | None) -> GradeSummary:
    cs = [s.content.score for s in slides if s.content]
    ds = [s.design.score for s in slides if s.design]
    not_assessed, failing = [], []
    cm = round(sum(cs) / len(cs), 2) if cs else None
    dm = round(sum(ds) / len(ds), 2) if ds else None
    for name, v in (("content", cm), ("design", dm), ("coherence", None if coherence is None else float(coherence.score))):
        if v is None:
            not_assessed.append(name)
        elif v < 3:
            failing.append(name)
    passed = None if not_assessed else (not failing)
    if failing:
        passed = False                      # a failing dimension fails the task even if another is undetermined
    return GradeSummary(content_mean=cm, design_mean=dm, coherence=None if coherence is None else coherence.score,
                        not_assessed=not_assessed, failing=failing, passed=passed)
