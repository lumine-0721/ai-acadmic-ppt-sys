"""Deterministic image-metadata relevance rules & optional VLM pixel verification (R6 / B7 fix).

Runs in Stage D even when no vision model is configured:
  1. Synthetic / Illustration / AI-generated filter: blocks non-photographic artwork,
     AI-generated renders, and illustrations when the requirement seeks a real-world photo/scene.
  2. Geographic / entity homonym conflict filter: detects conflicting place/jurisdiction qualifiers
     in Commons title, description, or Categories vs the target entity/place (for example,
     ``Pekin, Illinois`` when targeting ``Beijing / 北京``, or ``Nashville, Tennessee`` replica
     when targeting ``Athens / Greece``).
  3. Optional vision verification: if a vision ChatClient (``PPTV01_E_VISION_MODEL``) is supplied,
     inspects downloaded image bytes directly and returns ``relevant | weak | irrelevant``.
"""

from __future__ import annotations

import base64
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from pptv01.contracts import AssetRequirement, NonEmptyStr
from pptv01.llm import ChatClient, complete_json

from .sources import CommonsFile

# Requirements that explicitly ASK for non-photographic artwork/illustrations exempt Rule 1.
_ALLOW_ARTWORK_RE = re.compile(
    r"插画|手绘|绘画|油画|国画|版画|漫画|概念图|效果图|渲染图|艺术作品|雕塑|"
    r"\b(?:illustration|drawing|painting|artwork|sketch|cartoon|comic|concept art|3d render|sculpture|engraving|woodcut)\b",
    re.I,
)

# Strong non-photographic artwork / AI-generated indicators in Commons title / description / categories.
_NON_PHOTO_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("AI-generated", re.compile(r"\b(?:ai[- ]generated|artificial intelligence[- ]generated|stable diffusion|midjourney|dall[-· ]?e)\b", re.I)),
    ("Solarpunk/sci-fi concept art", re.compile(r"\b(?:solarpunk|cyberpunk|sci[- ]fi art|science fiction art|fantasy art|concept art)\b", re.I)),
    ("digital illustration/art", re.compile(r"\b(?:digital art|digital illustration|digital painting|computer[- ]generated|3d render(?:ing|s)?|cgi)\b", re.I)),
    ("illustration/drawing/cartoon", re.compile(r"\b(?:illustrations?|drawings? of|cartoons?|clip ?art|vector graphics|comic art)\b", re.I)),
)

# Canonical country/region signals inferred from target / purpose / query hints / Wikidata entity.
_REGION_SIGNALS: dict[str, re.Pattern[str]] = {
    "china": re.compile(
        r"中国|北京|上海|广州|深圳|杭州|南京|西安|成都|重庆|武汉|天津|苏州|山西|五台|佛光寺|故宫|长城|香港|台湾|"
        r"\b(?:china|chinese|beijing|peking|shanghai|guangzhou|shenzhen|hangzhou|nanjing|xi'?an|chengdu|chongqing|wuhan|shanxi|foguang)\b",
        re.I,
    ),
    "greece": re.compile(r"希腊|雅典|帕特农|卫城|\b(?:greece|greek|athens|acropolis|parthenon)\b", re.I),
    "france": re.compile(r"法国|巴黎|普瓦西|萨伏伊|\b(?:france|french|paris|poissy|savoye)\b", re.I),
    "japan": re.compile(r"日本|东京|京都|大阪|\b(?:japan|japanese|tokyo|kyoto|osaka)\b", re.I),
    "italy": re.compile(r"意大利|罗马|威尼斯|佛罗伦萨|\b(?:italy|italian|rome|venice|florence)\b", re.I),
    "uk": re.compile(r"英国|伦敦|牛津|剑桥|\b(?:united kingdom|britain|british|england|london|oxford|cambridge)\b", re.I),
    "germany": re.compile(r"德国|柏林|慕尼黑|\b(?:germany|german|berlin|munich)\b", re.I),
    "usa": re.compile(
        r"美国|纽约|华盛顿|加州|芝加哥|波士顿|\b(?:united states|u\.s\.a?\.|american|new york|california|chicago|boston|texas|illinois|tennessee|arkansas)\b",
        re.I,
    ),
}

# Explicit US states / counties / US jurisdiction indicators in Commons metadata & categories.
_US_JURISDICTION_RE = re.compile(
    r"\b(?:"
    r"united states|in the united states|usa|"
    r"alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|"
    r"idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|"
    r"michigan|minnesota|mississippi|missouri|montana|nebraska|nevada|new hampshire|"
    r"new jersey|new mexico|new york|north carolina|north dakota|ohio|oklahoma|oregon|"
    r"pennsylvania|rhode island|south carolina|south dakota|tennessee|texas|utah|vermont|"
    r"virginia|washington state|west virginia|wisconsin|wyoming|"
    r"tazewell county|nashville|centennial park"
    r")\b",
    re.I,
)

# Specific well-known homonym traps: (target_pattern, conflicting_candidate_pattern, required_confirm_pattern, label)
_HOMONYM_CONFLICTS: tuple[tuple[re.Pattern[str], re.Pattern[str], re.Pattern[str], str], ...] = (
    (
        re.compile(r"北京|\b(?:beijing|peking)\b", re.I),
        re.compile(r"\bpekin\b", re.I),
        re.compile(r"北京|\b(?:beijing|peking|china)\b", re.I),
        "homonym 'Pekin' (US town) conflicts with Beijing/北京",
    ),
    (
        re.compile(r"帕特农|\bparthenon\b", re.I),
        re.compile(r"\b(?:nashville|tennessee|arkansas|centennial park)\b", re.I),
        re.compile(r"\b(?:athens|greece|acropolis)\b", re.I),
        "US replica/namesake (Nashville/Arkansas) conflicts with Parthenon in Athens",
    ),
)

_D_VISION_PROMPT = """你是学术演示文稿的素材质量核查助手。
请判断这张图片是否真实、准确地对应目标主题与用途：
- 目标：{target}
- 用途：{purpose}
- 文件标题与说明：{meta}

严格判定规则：
- relevant：图片内容清晰、直接展示目标主题或真实场景；
- weak：仅部分相关（如过于局部、视角不典型、或仅为间接关联）；
- irrelevant：与目标主体/地点明显不符（例如同名异地、无关建筑、或与要求相反的艺术插画）。

只输出 JSON：
{{"relevance": "relevant" | "weak" | "irrelevant", "reason": "简短中文理由"}}"""


class _DVisionVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    relevance: Literal["relevant", "weak", "irrelevant"]
    reason: NonEmptyStr


def check_non_photo_artwork(f: CommonsFile, rq: AssetRequirement) -> str | None:
    """Reject non-photographic illustrations / AI-generated art when a real-world scene is requested."""
    req_context = " ".join([rq.purpose, *(t.name for t in rq.targets), *rq.query_hints])
    if _ALLOW_ARTWORK_RE.search(req_context):
        return None

    meta_text = " | ".join([f.title, f.name, f.description, *f.categories])
    for label, pat in _NON_PHOTO_PATTERNS:
        m = pat.search(meta_text)
        if m:
            return (
                f"non-photographic artwork/illustration ({label}: matched {m.group(0)!r}) "
                f"cannot serve as real-world scene evidence"
            )
    return None


def _expected_regions(text: str) -> set[str]:
    return {region for region, pat in _REGION_SIGNALS.items() if pat.search(text)}


def check_geographic_entity_conflict(
    f: CommonsFile,
    rq: AssetRequirement,
    *,
    entity_label: str = "",
    entity_desc: str = "",
) -> str | None:
    """Detect geographic / homonym conflicts between requirement target and Commons metadata/categories."""
    target_text = " ".join(
        [
            *(t.name for t in rq.targets),
            rq.purpose,
            *rq.query_hints,
            entity_label,
            entity_desc,
        ]
    )
    cand_text = " | ".join([f.title, f.name, f.description, *f.categories])

    # 1) Explicit homonym conflict rules (e.g. Pekin vs Beijing/Peking; Nashville/Arkansas vs Athens Parthenon)
    for tgt_re, conflict_re, confirm_re, reason in _HOMONYM_CONFLICTS:
        if tgt_re.search(target_text) and conflict_re.search(cand_text) and not confirm_re.search(cand_text):
            return f"geographic/entity conflict: {reason}"

    # 2) General expected-region vs candidate-jurisdiction conflict
    want_regions = _expected_regions(target_text)
    if not want_regions:
        return None

    # If target expects a non-US region (e.g., China, Greece, France, Japan) and candidate explicitly
    # locates itself in a US state/county without mentioning the expected region, reject.
    if "usa" not in want_regions:
        us_m = _US_JURISDICTION_RE.search(cand_text)
        if us_m:
            cand_regions = _expected_regions(cand_text)
            if not (cand_regions & want_regions):
                return (
                    f"geographic/entity conflict: target expects {sorted(want_regions)}, "
                    f"but Commons metadata/categories indicate US location ({us_m.group(0)!r})"
                )

    # Cross-country conflict among tracked regions when candidate explicitly names another region only
    cand_regions = _expected_regions(cand_text)
    if cand_regions and not (cand_regions & want_regions):
        return (
            f"geographic/entity conflict: target expects {sorted(want_regions)}, "
            f"but Commons metadata/categories indicate {sorted(cand_regions)}"
        )
    return None


def inspect_image_with_vision(
    image_bytes: bytes,
    mime: str,
    f: CommonsFile,
    rq: AssetRequirement,
    vision_client: ChatClient,
) -> tuple[Literal["relevant", "weak", "irrelevant"], str]:
    """Run real VLM inspection on downloaded image bytes; raises on API/schema error."""
    b64 = base64.b64encode(image_bytes).decode("ascii")
    target = rq.targets[0].name if rq.targets else rq.purpose
    meta = f"{f.title}; {f.description[:200]}; categories={', '.join(f.categories[:8])}"
    prompt = _D_VISION_PROMPT.format(target=target, purpose=rq.purpose, meta=meta)
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ],
        }
    ]
    res = complete_json(
        vision_client,
        messages,
        _DVisionVerdict.model_validate,
    )
    verdict: _DVisionVerdict = res.value
    return verdict.relevance, verdict.reason
