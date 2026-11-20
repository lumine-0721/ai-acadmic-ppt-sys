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


_PAGE_UNIT = r"(?:页|张幻灯片|slides?|pages?)"
_PAGE_RE = re.compile(rf"(?<![第\d.每这那该本单另下上前后同某整逐])({NUM})(?:\s*(?:-|~|～|到|至|—)\s*({NUM}))?\s*{_PAGE_UNIT}", re.I)
_MIN_UNIT = r"(?:分钟|分鐘|min(?:ute)?s?\b)"
_HOUR_UNIT = r"(?:小时|小時|hours?\b|hrs?\b)"
_DUR_MIN_RE = re.compile(rf"({NUM})(?:\s*(?:-|~|～|到|至|—)\s*({NUM}))?\s*{_MIN_UNIT}", re.I)
_DUR_HOUR_RE = re.compile(rf"({NUM})(?:\s*(?:-|~|～|到|至|—)\s*({NUM}))?\s*个?\s*{_HOUR_UNIT}", re.I)
_HALF_HOUR_RE = re.compile(r"半\s*个?\s*小时|half an hour")

# explicit page-structure locks (page number / fixed section pages)
_LOCK_RE = re.compile(
    r"第\s*" + NUM + r"\s*[页张]|\bS\d{3}\b|\bslide\s*\d+\b|\bpage\s*\d+\b|"
    r"封面页|目录页|致谢页|结束页|封底页|Q\s*&\s*A\s*页",
    re.I,
)

_STAT_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:%|％|个百分点|percent)", re.I)
_CLAIM_PHRASES = ("研究表明", "研究证明", "已被证实", "已经证实", "数据显示", "据统计", "统计显示",
                  "实验证明", "科学证明", "证明了", "毫无疑问", "无可争议",
                  "studies show", "research shows", "it is proven", "proven that")


def page_mentions(text: str) -> set[float]:
    out: set[float] = set()
    for m in _PAGE_RE.finditer(text):
        for g in m.groups():
            if g is not None:
                n = parse_number(g)
                if n is not None:
                    out.add(n)
    return out


def duration_mentions(text: str) -> set[float]:
    """Durations normalised to minutes."""
    out: set[float] = set()
    for m in _DUR_MIN_RE.finditer(text):
        for g in m.groups():
            if g is not None and (n := parse_number(g)) is not None:
                out.add(n)
    for m in _DUR_HOUR_RE.finditer(text):
        for g in m.groups():
            if g is not None and (n := parse_number(g)) is not None:
                out.add(n * 60)
    if _HALF_HOUR_RE.search(text):
        out.add(30.0)
    return out


def ungrounded_counts(candidate: str, utterance: str) -> list[str]:
    """Page counts / durations in ``candidate`` that the user never stated."""
    problems: list[str] = []
    ok_pages = page_mentions(utterance)
    for n in sorted(page_mentions(candidate) - ok_pages):
        problems.append(f"invented page count {n:g} (user did not state it)")
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


def unsourced_claims(candidate: str) -> list[str]:
    problems = [f"unsourced statistic {m.group(0)!r}" for m in _STAT_RE.finditer(candidate)]
    low = candidate.lower()
    problems += [f"conclusion-style claim {p!r}" for p in _CLAIM_PHRASES if p in low]
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
