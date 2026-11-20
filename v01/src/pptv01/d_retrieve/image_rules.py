"""General deterministic image-metadata relevance rules & optional VLM pixel verification (R6 / N5: B7, B14, B15).

Runs in Stage D even when no vision model is configured, using ONLY general rules (no benchmark-specific hardcoding):
  1. Synthetic / Illustration / AI-generated filter: blocks non-photographic artwork,
     AI-generated renders, and illustrations when the requirement seeks a real-world photo/scene.
  2. General geographic / administrative conflict filter (B14 fix): extracts country and
     sub-national administrative qualifiers (US 50 states, Canadian provinces, UK nations,
     Australian states, Chinese provinces/municipalities, and Wikidata P17 country QIDs)
     and rejects candidates whose location qualifiers conflict with the target.
  3. Named-target entity grounding check (B15 fix): when a requirement targets a concrete named
     place/building/person/organization/work, candidate metadata (title, description, categories)
     must explicitly mention the target (or its Wikidata label/alias/standard transliteration);
     otherwise reject so Stage D honestly marks the asset ``missing`` rather than selecting
     an unrelated "somewhat similar" photo.
  4. Optional vision verification: if a vision ChatClient (``PPTV01_E_VISION_MODEL``) is supplied,
     inspects downloaded image bytes directly and returns ``relevant | weak | irrelevant``.
"""

from __future__ import annotations

import base64
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from pptv01.contracts import AssetRequirement, NonEmptyStr
from pptv01.llm import ChatClient, complete_json

from .sources import CommonsFile, WdEntity

# Requirements that explicitly ASK for non-photographic artwork/illustrations exempt Rule 1.
_ALLOW_ARTWORK_RE = re.compile(
    r"插画|手绘|绘画|油画|国画|版画|漫画|概念图|效果图|渲染图|艺术作品|雕塑|"
    r"\b(?:illustration|drawing|painting|artwork|sketch|cartoon|comic|concept art|3d render|sculpture|engraving|woodcut)\b",
    re.I,
)

# Strong non-photographic artwork / AI-generated indicators in Commons title / description / categories.
_NON_PHOTO_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("AI-generated", re.compile(r"\b(?:ai[- ]generated|artificial intelligence[- ]generated|stable diffusion|midjourney|dall[-· ]?e)\b", re.I)),
    ("concept/sci-fi art", re.compile(r"\b(?:solarpunk|cyberpunk|sci[- ]fi art|science fiction art|fantasy art|concept art)\b", re.I)),
    ("digital illustration/art", re.compile(r"\b(?:digital art|digital illustration|digital painting|computer[- ]generated|3d render(?:ing|s)?|cgi)\b", re.I)),
    ("illustration/drawing/cartoon", re.compile(r"\b(?:illustrations?|drawings? of|cartoons?|clip ?art|vector graphics|comic art)\b", re.I)),
)

# Standard Wikidata P17 (country) QIDs -> canonical country key
_WD_COUNTRY_QIDS: dict[str, str] = {
    "Q148": "china",
    "Q865": "china",
    "Q30": "usa",
    "Q41": "greece",
    "Q142": "france",
    "Q145": "uk",
    "Q17": "japan",
    "Q183": "germany",
    "Q38": "italy",
    "Q16": "canada",
    "Q408": "australia",
    "Q668": "india",
    "Q155": "brazil",
    "Q159": "russia",
    "Q884": "south_korea",
    "Q29": "spain",
    "Q55": "netherlands",
    "Q39": "switzerland",
    "Q334": "singapore",
    "Q96": "mexico",
    "Q79": "egypt",
    "Q43": "turkey",
}

# Standard 50 US states (excluding bare "georgia" which is also a country unless paired with US/state context)
_US_STATES: tuple[str, ...] = (
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut",
    "delaware", "florida", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas",
    "kentucky", "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "new york", "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota", "tennessee", "texas",
    "utah", "vermont", "virginia", "washington state", "west virginia", "wisconsin", "wyoming",
)

_CANADIAN_PROVINCES: tuple[str, ...] = (
    "ontario", "quebec", "british columbia", "alberta", "manitoba", "saskatchewan",
    "nova scotia", "new brunswick", "newfoundland", "prince edward island",
)

_AUSTRALIAN_STATES: tuple[str, ...] = (
    "new south wales", "queensland", "victoria, australia", "western australia",
    "south australia", "tasmania",
)

_UK_NATIONS: tuple[str, ...] = (
    "england", "scotland", "wales", "northern ireland",
)

# Sovereign country & administrative-division patterns used on CANDIDATE metadata.
# Deliberately excludes bare city names (e.g. Paris, Athens, London, Rome, Cambridge) because
# homonym cities exist across countries (Paris, Texas; London, Ontario; Athens, Georgia).
_CANDIDATE_COUNTRY_PATTERNS: dict[str, re.Pattern[str]] = {
    "china": re.compile(r"中国|中华|\b(?:china|chinese|prc|people'?s republic of china|hong kong|taiwan)\b", re.I),
    "usa": re.compile(
        r"美国|\b(?:united states|u\.s\.a?\.|american|"
        + "|".join(re.escape(s) for s in _US_STATES)
        + r")\b",
        re.I,
    ),
    "greece": re.compile(r"希腊|\b(?:greece|greek|hellenic|attica)\b", re.I),
    "france": re.compile(r"法国|\b(?:france|french|île-de-france|ile-de-france)\b", re.I),
    "uk": re.compile(
        r"英国|英格兰|苏格兰|威尔士|\b(?:united kingdom|u\.k\.|great britain|britain|british|england|scotland|wales|northern ireland|cambridgeshire)\b",
        re.I,
    ),
    "japan": re.compile(r"日本|\b(?:japan|japanese|hokkaido|kyushu|honshu)\b", re.I),
    "germany": re.compile(r"德国|\b(?:germany|german|bavaria|deutschland)\b", re.I),
    "italy": re.compile(r"意大利|\b(?:italy|italian|italia|tuscany|lombardy)\b", re.I),
    "canada": re.compile(
        r"加拿大|\b(?:canada|canadian|"
        + "|".join(re.escape(p) for p in _CANADIAN_PROVINCES)
        + r")\b",
        re.I,
    ),
    "australia": re.compile(
        r"澳大利亚|\b(?:australia|australian|"
        + "|".join(re.escape(s) for s in _AUSTRALIAN_STATES)
        + r")\b",
        re.I,
    ),
    "india": re.compile(r"印度|\b(?:india|indian)\b", re.I),
    "brazil": re.compile(r"巴西|\b(?:brazil|brazilian|brasil)\b", re.I),
    "russia": re.compile(r"俄罗斯|\b(?:russia|russian|russian federation)\b", re.I),
    "south_korea": re.compile(r"韩国|\b(?:south korea|republic of korea|korean)\b", re.I),
    "spain": re.compile(r"西班牙|\b(?:spain|spanish|españa)\b", re.I),
    "netherlands": re.compile(r"荷兰|\b(?:netherlands|dutch|holland)\b", re.I),
    "switzerland": re.compile(r"瑞士|\b(?:switzerland|swiss)\b", re.I),
    "singapore": re.compile(r"新加坡|\b(?:singapore|singaporean)\b", re.I),
    "egypt": re.compile(r"埃及|\b(?:egypt|egyptian)\b", re.I),
    "turkey": re.compile(r"土耳其|\b(?:turkey|turkish|türkiye)\b", re.I),
}

# Target-side country signals: includes unambiguous major cities/landmarks in the user's requirement
# so a request for "北京公园" or "帕特农神庙" infers expected country China / Greece cleanly.
_TARGET_COUNTRY_PATTERNS: dict[str, re.Pattern[str]] = {
    **_CANDIDATE_COUNTRY_PATTERNS,
    "china": re.compile(
        r"中国|中华|北京|上海|广州|深圳|杭州|南京|西安|成都|重庆|武汉|天津|苏州|山西|香港|台湾|"
        r"\b(?:china|chinese|prc|beijing|peking|shanghai|guangzhou|shenzhen|hangzhou|nanjing|xi'?an|chengdu|chongqing|wuhan|shanxi|hong kong|taiwan)\b",
        re.I,
    ),
    "greece": re.compile(r"希腊|雅典|帕特农|\b(?:greece|greek|athens|attica|acropolis|parthenon|hellenic)\b", re.I),
    "france": re.compile(r"法国|巴黎|普瓦西|\b(?:france|french|paris|île-de-france|ile-de-france|poissy|lyon|marseille)\b", re.I),
    "uk": re.compile(
        r"英国|英格兰|苏格兰|威尔士|伦敦|牛津|剑桥|\b(?:united kingdom|u\.k\.|great britain|britain|british|england|scotland|wales|northern ireland|london|oxford|cambridgeshire)\b",
        re.I,
    ),
    "japan": re.compile(r"日本|东京|京都|大阪|\b(?:japan|japanese|tokyo|kyoto|osaka|hokkaido)\b", re.I),
    "germany": re.compile(r"德国|柏林|慕尼黑|\b(?:germany|german|berlin|munich|bavaria|hamburg)\b", re.I),
    "italy": re.compile(r"意大利|罗马|威尼斯|佛罗伦萨|米兰|\b(?:italy|italian|rome|venice|florence|milan|tuscany)\b", re.I),
}

# Pre-compiled sub-national administrative region patterns (enables same-country disambiguation,
# e.g. Springfield, Illinois vs Springfield, Oregon; Cambridge, Massachusetts vs Cambridge, Ontario)
_SUBREGION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(rf"\b{re.escape(name)}\b", re.I))
    for name in (*_US_STATES, *_CANADIAN_PROVINCES, *_UK_NATIONS, *_AUSTRALIAN_STATES)
)

# General bilingual aliases for major world cities / regions when Wikidata does not resolve a composite Chinese phrase
_BILINGUAL_PLACE_ALIASES: dict[str, tuple[str, ...]] = {
    "北京": ("beijing", "peking"),
    "上海": ("shanghai",),
    "广州": ("guangzhou", "canton"),
    "深圳": ("shenzhen",),
    "杭州": ("hangzhou",),
    "南京": ("nanjing",),
    "西安": ("xian", "xi'an"),
    "成都": ("chengdu",),
    "重庆": ("chongqing",),
    "武汉": ("wuhan",),
    "香港": ("hong kong",),
    "台北": ("taipei",),
    "东京": ("tokyo",),
    "京都": ("kyoto",),
    "巴黎": ("paris",),
    "伦敦": ("london",),
    "雅典": ("athens",),
    "罗马": ("rome",),
    "纽约": ("new york",),
    "剑桥": ("cambridge",),
    "牛津": ("oxford",),
    "帕特农": ("parthenon",),
    "故宫": ("forbidden city", "palace museum"),
    "长城": ("great wall",),
    "佛光寺": ("foguang",),
    "萨伏伊": ("savoye",),
    "埃菲尔": ("eiffel",),
    "新加坡": ("singapore",),
}

# Concrete entity types where candidate metadata MUST explicitly mention the target entity (B15 fix)
_NAMED_ENTITY_TYPES: frozenset[str] = frozenset({
    "place",
    "location",
    "city",
    "country",
    "region",
    "building",
    "landmark",
    "monument",
    "site",
    "person",
    "organization",
    "institution",
    "work",
    "river",
    "mountain",
    "species",
    "park",
})

# Generic descriptive words that MUST NOT count as matching a specific named entity
_GENERIC_ENTITY_STOPWORDS: frozenset[str] = frozenset({
    "city", "urban", "park", "parks", "green", "garden", "building", "buildings", "architecture",
    "tower", "house", "villa", "temple", "church", "bridge", "street", "road", "square", "center",
    "centre", "museum", "university", "college", "school", "institute", "hospital", "station",
    "photo", "photograph", "image", "picture", "view", "aerial", "satellite", "map", "chart",
    "diagram", "heat", "island", "effect", "climate", "temperature", "surface", "roof", "water",
    "forest", "river", "mountain", "lake", "national", "modern", "ancient", "famous", "world",
    "the", "and", "for", "with", "from", "near", "north", "south", "east", "west", "central",
    "new", "old", "big", "great", "grand",
})

_GENERIC_CJK_STOPWORDS: tuple[str, ...] = (
    "城市公园", "城市绿地", "公园绿地", "城市热岛", "热岛效应", "世界著名", "建筑外观",
    "实景照片", "航拍图", "卫星图", "遥感图", "立体绿化", "屋顶花园",
    "城市", "公园", "绿地", "建筑", "大楼", "广场", "街道", "中心", "博物馆",
    "大学", "学院", "实景", "照片", "图片", "外观", "全景", "景观", "案例", "示例",
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


def _detect_countries(text: str, *, entity: WdEntity | None = None, candidate_side: bool = False) -> set[str]:
    patterns = _CANDIDATE_COUNTRY_PATTERNS if candidate_side else _TARGET_COUNTRY_PATTERNS
    found = {country for country, pat in patterns.items() if pat.search(text)}
    if entity is not None:
        for qid in entity.country_qids:
            c = _WD_COUNTRY_QIDS.get(qid)
            if c:
                found.add(c)
    return found


def _detect_subregions(text: str) -> set[str]:
    return {name for name, pat in _SUBREGION_PATTERNS if pat.search(text)}


def check_geographic_entity_conflict(
    f: CommonsFile,
    rq: AssetRequirement,
    *,
    entity: WdEntity | None = None,
    entity_label: str = "",
    entity_desc: str = "",
) -> str | None:
    """General country and sub-national administrative conflict check (B14 fix).

    Compares expected country/subregion signals from the requirement target + Wikidata P17/description
    against the candidate's Commons title, description, and Categories.
    """
    ent_label = entity.label if entity else entity_label
    ent_desc = entity.description if entity else entity_desc
    target_text = " ".join(
        [
            *(t.name for t in rq.targets),
            rq.purpose,
            *rq.query_hints,
            ent_label,
            ent_desc,
        ]
    )
    cand_text = " | ".join([f.title, f.name, f.description, *f.categories])

    # 1) Country-level conflict (e.g., China vs USA/Illinois, France vs USA/Texas, UK vs USA/Massachusetts, Greece vs USA/Arkansas)
    want_countries = _detect_countries(target_text, entity=entity, candidate_side=False)
    cand_countries = _detect_countries(cand_text, candidate_side=True)
    if want_countries and cand_countries and not (want_countries & cand_countries):
        return (
            f"geographic/entity conflict: target expects country {sorted(want_countries)}, "
            f"but Commons metadata/categories indicate {sorted(cand_countries)}"
        )

    # 2) Same-country sub-national conflict (e.g., Springfield, Illinois vs Springfield, Oregon)
    want_subregions = _detect_subregions(target_text)
    cand_subregions = _detect_subregions(cand_text)
    if want_subregions and cand_subregions and not (want_subregions & cand_subregions):
        return (
            f"geographic/entity conflict: target expects subregion {sorted(want_subregions)}, "
            f"but Commons metadata/categories indicate {sorted(cand_subregions)}"
        )

    return None


def _extract_target_entity_tokens(rq: AssetRequirement, entity: WdEntity | None = None) -> tuple[set[str], set[str]]:
    """Return (latin_tokens_lower, cjk_tokens) identifying the named target entity."""
    latin_tokens: set[str] = set()
    cjk_tokens: set[str] = set()

    def _harvest(phrase: str) -> None:
        raw = (phrase or "").strip()
        if not raw:
            return
        # Harvest known bilingual place/landmark aliases embedded in CJK composite names
        for zh_key, en_aliases in _BILINGUAL_PLACE_ALIASES.items():
            if zh_key in raw:
                cjk_tokens.add(zh_key)
                for a in en_aliases:
                    latin_tokens.add(a.lower())
        # Harvest Latin proper tokens (>= 3 chars, non-generic)
        for tok in re.findall(r"[A-Za-z][A-Za-z'\-]{2,}", raw):
            low = tok.lower()
            if low not in _GENERIC_ENTITY_STOPWORDS:
                latin_tokens.add(low)
        # Harvest CJK entity core after stripping generic suffixes
        cleaned = raw
        for stop in _GENERIC_CJK_STOPWORDS:
            cleaned = cleaned.replace(stop, " ")
        for seg in re.findall(r"[\u3400-\u9fff]{2,}", cleaned):
            cjk_tokens.add(seg)

    if entity is not None:
        _harvest(entity.label)
        _harvest(entity.label_en)
        _harvest(entity.label_zh)
        for al in entity.aliases:
            _harvest(al)

    if rq.targets:
        _harvest(rq.targets[0].name)

    return latin_tokens, cjk_tokens


def check_target_entity_match(
    f: CommonsFile,
    rq: AssetRequirement,
    *,
    entity: WdEntity | None = None,
) -> str | None:
    """Require candidate metadata to explicitly reference the named target entity (B15 fix).

    Applies when the requirement targets a concrete named entity (place/building/person/etc.)
    or resolved Wikidata entity. Prevents falling back to an unrelated "somewhat similar" photo
    (e.g. selecting a New York satellite image when targeting Beijing).
    """
    if not rq.targets:
        return None
    primary = rq.targets[0]
    is_named_target = (primary.entity_type.lower() in _NAMED_ENTITY_TYPES) or (entity is not None)
    if not is_named_target:
        return None

    latin_tokens, cjk_tokens = _extract_target_entity_tokens(rq, entity=entity)
    if not latin_tokens and not cjk_tokens:
        return None

    # Exclude generic country-only tokens when a more specific city/landmark token exists,
    # so "United States" alone cannot satisfy "Springfield, Illinois" with a New York photo!
    country_words = {
        "china", "chinese", "usa", "united", "states", "american", "france", "french",
        "greece", "greek", "britain", "british", "england", "japan", "japanese",
        "germany", "german", "italy", "italian", "canada", "canadian", "australia", "australian",
        "india", "indian", "russia", "russian", "brazil", "brazilian", "spain", "spanish",
    }
    state_words = set(_US_STATES) | set(_CANADIAN_PROVINCES) | set(_UK_NATIONS) | set(_AUSTRALIAN_STATES)
    specific_latin = {t for t in latin_tokens if t not in country_words and t not in state_words}
    check_latin = specific_latin if specific_latin else latin_tokens

    cand_text = " | ".join([f.title, f.name, f.description, *f.categories])
    cand_low = cand_text.lower()

    for tok in check_latin:
        # Strict word-boundary match so partial prefix tokens cannot false-match target names
        if re.search(rf"\b{re.escape(tok)}\b", cand_low):
            return None
    for tok in cjk_tokens:
        if tok in cand_text:
            return None

    expected_preview = sorted(check_latin)[:4] + sorted(cjk_tokens)[:3]
    return (
        f"target entity mismatch: candidate metadata does not mention target {primary.name!r} "
        f"(expected one of {expected_preview})"
    )


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
