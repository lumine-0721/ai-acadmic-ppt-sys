"""Chinese numeral normalisation (pre-step for textcheck), built on cn2an (MIT, https://github.com/Ailln/cn2an).

cn2an does the numeric semantics (千/万/亿/点/零). What it does NOT do is decide WHICH Chinese-numeral runs in prose
are numbers, and it silently mis-converts some of them (measured: 两三 -> 23, "升温两三度" -> "升温23度";
万一/千万 raise). So this module only converts a run when
  * it is followed by a quantity unit we check (页, 分钟, %, 米, 吨, 倍, 成 ...), or is the X of 百分之X, and
  * it is not an approximation (两三, 三五, 七八十, 十几, 一百多, 几十, 数百), an ordinal (第三), a year-like digit string
    that is not followed by 年, an idiom/word (万一, 千万别, 十分重要, 百姓), or something cn2an cannot parse.
Anything it will not convert is left untouched, so the regexes in textcheck still see the original text.
"""

from __future__ import annotations

import re

import cn2an

_DIGITS = "零〇一二两三四五六七八九"
_DIGIT_VAL = {"零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_RUN = re.compile(r"[零〇一二两三四五六七八九十百千万亿点]+")
_UNIT_AFTER = re.compile(
    r"^\s*(?:%|％|个百分点|页(?![纸面码眉])|张幻灯片|分钟|分鐘|小时|小時|个?小时|倍|成(?![不立为就功长熟型品本员果绩效败因分套都])|摄氏度|华氏度|℃|°|"
    r"公里|千米|公顷|平方|立方|毫米|厘米|公斤|千克|吨|千瓦|兆瓦|吉瓦|美元|欧元|人民币|元(?![对素论化组函数])|米(?![饭粉糠色])|"
    r"个百分点|万人|亿人|年)")
_APPROX_BEFORE = "第几数每各"
_APPROX_AFTER = "几来多余许"


def _fmt(v: float) -> str:
    if v >= 1e8 and v % 1e8 == 0:
        return f"{v / 1e8:g}亿"
    if v >= 1e4 and v % 1e4 == 0:
        return f"{v / 1e4:g}万"
    return f"{v:g}" if v != int(v) else str(int(v))


def run_value(run: str, after: str = "") -> float | None:
    """Numeric value of one Chinese-numeral run, or None if it is ambiguous / not safely a number."""
    if not run or run[0] in "万亿点" or run.endswith("点") or "点" in run and not re.search(r"点[零〇一二两三四五六七八九]", run):
        return None
    digits_only = all(c in _DIGITS for c in run)
    if digits_only and len(run) == 1:
        return None      # 一页 / 三个: the regexes in textcheck already read single digits; rewriting "一页" would break pace guards
    if digits_only and len(run) >= 2:
        # 两三 / 三五 / 七八 are approximations; 一九九八年 / 二〇二四年 are years read digit by digit
        if after.startswith("年") and len(run) in (2, 4) and "两" not in run:
            return float("".join(str(_DIGIT_VAL[c]) for c in run))
        return None
    if re.search(rf"[{_DIGITS}][{_DIGITS}]", run.split("点")[0]):       # 七八十, 三五百: two digits side by side
        return None
    try:
        v = cn2an.cn2an(run, "smart")
    except Exception:
        return None
    return float(v)


def convert_run(run: str, after: str = "") -> str | None:
    v = run_value(run, after)
    return None if v is None else _fmt(v)


def normalize_numerals(text: str) -> str:
    out, pos = [], 0

    # 百分之二十三 -> 23%
    def pct(m: re.Match) -> str:
        v = convert_run(m.group(1))
        return f"{v}%" if v is not None else m.group(0)
    text = re.sub(r"百分之([零〇一二两三四五六七八九十百千点]+)", pct, text)
    for m in _RUN.finditer(text):
        run, s, e = m.group(0), m.start(), m.end()
        before = text[s - 1] if s else ""
        after = text[e:e + 8]
        if before and before in _APPROX_BEFORE:
            continue
        if after[:1] and after[0] in _APPROX_AFTER:
            continue
        if not _UNIT_AFTER.match(after) and not (len(run) >= 2 and after.startswith("年")):
            continue
        v = convert_run(run, after)
        if v is None:
            continue
        out.append(text[pos:s]); out.append(v); pos = e
    out.append(text[pos:])
    return "".join(out)


_SPACE_COMMA = re.compile(r"(?<=\d)[,，\s]+(?=\d)")


def canonical(text: str) -> str:
    """Comparison form: numerals normalised, full-width %, thousands separators and spaces removed, lower-cased."""
    t = normalize_numerals(text).replace("％", "%").lower()
    t = _SPACE_COMMA.sub("", t)
    return re.sub(r"\s+", "", t)
