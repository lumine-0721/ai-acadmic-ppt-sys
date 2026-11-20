"""Deterministic text checks shared by stage A/B post-validation.

Goal: catch invented page counts / durations / page-structure locks / unsourced
statistics. These are heuristics (regex), not a semantic judge; they are meant
to be strict on the cheap, unambiguous cases.
"""

from __future__ import annotations

import re

_CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9}

NUM = r"(?:\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百]+)"


def parse_number(s: str) -> float | None:
    s = s.strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", s):
        return float(s)
    if not s or not all(c in _CN_DIGIT or c in "十百" for c in s):
        return None
    total, cur = 0, 0
    if "百" in s:
        h, _, rest = s.partition("百")
        total += (_CN_DIGIT.get(h, 1) if h else 1) * 100
        s = rest
        if not s:
            return float(total)
    if "十" in s:
        t, _, rest = s.partition("十")
        total += (_CN_DIGIT.get(t, 1) if t else 1) * 10
        s = rest
    if s:
        if len(s) != 1 or s not in _CN_DIGIT:
            return None
        cur = _CN_DIGIT[s]
    return float(total + cur)


# page unit; "页纸/页面/页码/页眉" are not page counts ("一页纸讲清楚")
_PAGE_UNIT = r"(?:页(?![纸面码眉])|张幻灯片|slides?|pages?)"
_RANGE_SEP = r"(?:\s*(?:-|~|～|到|至|—|或|、)\s*)"
_PAGE_RE = re.compile(
    rf"(?<![第\d.每这那该本单另下上前后同某整逐])({NUM})(?:{_RANGE_SEP}({NUM}))?\s*{_PAGE_UNIT}", re.I)
# per-page phrasing: "一页只讲一个点", "一页最多三句话" is not a page count
_ONE_PAGE_PER_PAGE = re.compile(r"^\s*(?:只|仅|最多|至多|不超过|一个|一条|一张|一句|讲|说|放)")
_VAGUE_PAGE_RE = re.compile(r"(?:十几|几十|十来|二十来|十余|二十余|数十|数百)\s*(?:页|张幻灯片)")

_MIN_UNIT = r"(?:分钟|分鐘|min(?:ute)?s?\b)"
_HOUR_UNIT = r"(?:小时|小時|hours?\b|hrs?\b)"
_DUR_MIN_RE = re.compile(rf"({NUM})(?:{_RANGE_SEP}({NUM}))?\s*{_MIN_UNIT}", re.I)
_DUR_HOUR_RE = re.compile(rf"({NUM})(?:{_RANGE_SEP}({NUM}))?\s*个?\s*{_HOUR_UNIT}", re.I)
_HALF_HOUR_RE = re.compile(r"半\s*个?\s*小时|half an hour")
# "每页2分钟", "约5分钟讲一个点", "1分钟/页": a per-unit pace, not the total duration
_DUR_BEFORE_PER = re.compile(r"(?:每(?:页|张|个|条|一|点|屏)[^，。；,.;、]{0,8}|单页|一页|一个点|一个要点)\s*(?:约|大约|最多|至多|不超过|平均|花)?\s*$")
_DUR_AFTER_PER = re.compile(r"^(?:左右)?\s*(?:(?:讲|说|介绍|聊|花)\s*(?:一|1|每)|一个|一页|一张|一条|一屏|每|/\s*(?:页|张|个))")

# explicit page-structure locks (page number / fixed section pages)
_LOCK_RE = re.compile(
    r"第\s*" + NUM + r"\s*[页张]|\bS\d{3}\b|\bslide\s*\d+\b|\bpage\s*\d+\b|"
    r"封面页|目录页|致谢页|结束页|封底页|Q\s*&\s*A\s*页",
    re.I,
)

# ---- unsourced numbers / quantities
_NUMV = r"(?:\d+(?:[.,]\d+)?|[零〇一二两三四五六七八九十百千万]+)"
_STAT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("percentage", re.compile(r"\d+(?:\.\d+)?\s*(?:%|％|个百分点|percent\b|per cent\b)", re.I)),
    ("percentage", re.compile(rf"百分之\s*{_NUMV}")),
    ("percentage", re.compile(rf"{_NUMV}\s*个百分点")),
    # 三成 / 约三成城市 (not 形成, 一成不变, 三成立)
    ("fraction", re.compile(rf"(?<![第\d.]){_NUMV}\s*成(?![不立为就功长熟型品本员果绩效败因分套都])")),
    ("fraction", re.compile(rf"{_NUMV}\s*分之\s*{_NUMV}")),
    ("multiple", re.compile(rf"(?<![第\d.]){_NUMV}\s*倍")),
    ("multiple", re.compile(r"\d+(?:\.\d+)?\s*[x×]\b")),
    ("quantity", re.compile(
        rf"(?<![第\d.]){_NUMV}\s*(?:摄氏度|华氏度|℃|°\s*[CF]|公里|千米|公顷|平方公里|平方米|立方米|毫米|厘米|公斤|千克|吨|"
        rf"千瓦时|千瓦|兆瓦|吉瓦|美元|欧元|人民币|万元|亿元|万人|亿人|万吨|亿吨|ppm|km|kg|kwh|gw|mw)", re.I)),
    ("quantity", re.compile(rf"(?<![第\d.]){_NUMV}\s*(?:米(?![饭粉糠色])|元(?![对素论化组函数]))")),
]
_CLAIM_PHRASES = (
    "研究表明", "研究证明", "研究发现", "研究显示", "调查显示", "调查发现", "报告显示", "数据表明", "数据显示",
    "数据证明", "实验表明", "实验证明", "科学证明", "事实证明", "统计表明", "统计显示", "据统计",
    "已被证实", "已经证实", "被证实", "证实了", "证明了", "毫无疑问", "无可争议", "不容置疑",
    "studies show", "study shows", "studies have shown", "research shows", "research has shown",
    "studies found", "research found", "according to research", "it is proven", "proven that",
    "has been shown", "have been shown",
)


def page_mentions(text: str) -> set[float]:
    out: set[float] = set()
    for m in _PAGE_RE.finditer(text):
        first = parse_number(m.group(1)) if m.group(1) else None
        if first == 1.0 and _ONE_PAGE_PER_PAGE.match(text[m.end():]):
            continue
        for g in m.groups():
            if g is None:
                continue
            n = parse_number(g)
            if n is not None:
                out.add(n)
            elif len(g) == 2 and all(c in _CN_DIGIT for c in g):   # 两三页 / 三五页 -> 2-3 / 3-5
                out.update(float(_CN_DIGIT[c]) for c in g)
    return out


def vague_page_counts(text: str) -> list[str]:
    return [m.group(0) for m in _VAGUE_PAGE_RE.finditer(text)]


def duration_mentions(text: str) -> set[float]:
    """Total durations normalised to minutes (per-page / per-point paces are ignored)."""
    out: set[float] = set()
    for rx, mult in ((_DUR_MIN_RE, 1), (_DUR_HOUR_RE, 60)):
        for m in rx.finditer(text):
            before = text[max(0, m.start() - 8): m.start()]
            after = text[m.end(): m.end() + 8]
            if _DUR_BEFORE_PER.search(before) or _DUR_AFTER_PER.match(after):
                continue
            for g in m.groups():
                if g is not None and (n := parse_number(g)) is not None:
                    out.add(n * mult)
    if _HALF_HOUR_RE.search(text):
        out.add(30.0)
    return out


def ungrounded_counts(candidate: str, utterance: str) -> list[str]:
    """Page counts / durations in ``candidate`` that the user never stated."""
    problems: list[str] = []
    ok_pages = page_mentions(utterance)
    for n in sorted(page_mentions(candidate) - ok_pages):
        problems.append(f"invented page count {n:g} (user did not state it)")
    if not vague_page_counts(utterance):
        for v in vague_page_counts(candidate):
            problems.append(f"invented page count {v!r} (user did not state it)")
    ok_dur = duration_mentions(utterance)
    for n in sorted(duration_mentions(candidate) - ok_dur):
        problems.append(f"invented duration {n:g} min (user did not state it)")
    return problems


def structure_locks(candidate: str, utterance: str) -> list[str]:
    return [
        f"locks page structure with {m.group(0)!r}"
        for m in _LOCK_RE.finditer(candidate)
        if m.group(0) not in utterance
    ]


def unsourced_claims(candidate: str, utterance: str = "") -> list[str]:
    """Numbers/quantities/conclusion phrases that no source backs. Text the user
    wrote themselves (``utterance``) is not flagged."""
    problems: list[str] = []
    seen: set[str] = set()
    for kind, rx in _STAT_PATTERNS:
        for m in rx.finditer(candidate):
            t = m.group(0).strip()
            if t in seen or (utterance and t in utterance):
                continue
            seen.add(t)
            problems.append(f"unsourced {kind} {t!r}")
    low = candidate.lower()
    for p in _CLAIM_PHRASES:
        if p in low and not (utterance and p in utterance.lower()):
            problems.append(f"conclusion-style claim {p!r}")
    return problems


def page_limit(utterance: str) -> tuple[str, int] | None:
    """User-stated page limit: ('max', n) for 不超过/最多/以内, ('exact', n) for plain N页."""
    m = re.search(rf"(?:不超过|最多|至多|不多于|少于|不要超过|up to|at most|no more than|max(?:imum)?(?: of)?)\s*({NUM})\s*{_PAGE_UNIT}", utterance, re.I)
    if m and (n := parse_number(m.group(1))) is not None:
        return ("max", int(n))
    m = re.search(rf"({NUM})\s*{_PAGE_UNIT}\s*(?:以内|之内|以下|or fewer|or less)", utterance, re.I)
    if m and (n := parse_number(m.group(1))) is not None:
        return ("max", int(n))
    pages = page_mentions(utterance)
    if len(pages) == 1 and _PAGE_RE.search(utterance):
        return ("exact", int(next(iter(pages))))
    return None
