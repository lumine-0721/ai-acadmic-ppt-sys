"""cn2an as a normalisation pre-step: what it fixes, and the cn2an pitfalls we guard against (all measured on cn2an 0.5.24)."""
import cn2an
import pytest

from pptv01 import textcheck as t
from pptv01.numerals import canonical, normalize_numerals


@pytest.mark.parametrize("src,want", [
    ("该塔高三百二十四米", "该塔高324米"),
    ("死亡率上升百分之二十三", "死亡率上升23%"),
    ("增长二点五倍", "增长2.5倍"),
    ("投资三百二十万元", "投资320万元"),
    ("年排放约五亿吨二氧化碳", "年排放约5亿吨二氧化碳"),
    ("共一百二十页", "共120页"),
    ("讲二十分钟", "讲20分钟"),
    ("二〇二四年", "2024年"),
    ("一九九八年的研究", "1998年的研究"),
    ("两千公里", "2000公里"),
    ("一万二千人", "一万二千人"),            # 人 alone is not a unit we check -> untouched
])
def test_normalises_real_quantities(src, want):
    assert normalize_numerals(src) == want


# cn2an itself gets these wrong or raises -> we must leave them alone
@pytest.mark.parametrize("src", [
    "升温两三度", "三五页", "七八十页", "两三页", "十几页", "一百多米", "几十页", "数百吨", "约三成城市",
    "万一出现问题", "千万别忘了", "十分重要", "百姓生活", "第三页", "每三页", "唯一的办法",
    "一页纸讲清楚", "每页一分钟", "统一规划", "一成不变",
])
def test_ambiguous_or_non_numeric_runs_are_left_untouched(src):
    assert normalize_numerals(src) == src


def test_documented_cn2an_pitfalls_still_exist_so_the_guards_stay_needed():
    assert cn2an.cn2an("两三", "smart") == 23           # silent wrong answer
    assert cn2an.transform("升温两三度", "cn2an") == "升温23度"
    for bad in ("万一", "千万"):
        with pytest.raises(Exception):
            cn2an.cn2an(bad, "smart")


def test_normalisation_is_idempotent():
    for s in ["该塔高三百二十四米", "升温两三度", "死亡率上升百分之二十三", "2024年 12,000 人"]:
        assert normalize_numerals(normalize_numerals(s)) == normalize_numerals(s)


def test_canonical_form_unifies_formats():
    canonical("３２４ 米")      # full-width digits are out of scope; must not crash
    assert canonical("百分之二十三") == canonical("23％") == "23%"
    assert canonical("12,000吨") == canonical("12000吨")
    assert canonical("三百二十四米") == "324米"


# ---- what the fusion buys in textcheck
def test_chinese_numeral_claim_matches_arabic_figure_in_cited_note():
    note = "埃菲尔铁塔高324米，建于1889年。"
    assert t.unsourced_claims("该塔高三百二十四米", sourced_text=note) == []
    assert t.unsourced_claims("该塔高三百二十米", sourced_text=note)          # different number: still flagged
    assert t.unsourced_claims("死亡率上升百分之二十三", sourced_text="mortality rose 23% in the heatwave") == []
    assert t.unsourced_claims("投资三百二十万元", sourced_text="项目总投资320万元") == []


def test_decimal_chinese_numerals_are_now_caught():
    assert t.unsourced_claims("升温二点五摄氏度")        # was invisible to the \d-only percentage/decimal path
    assert t.unsourced_claims("增长二十三点五%")


def test_user_stated_figures_are_exempt_across_numeral_forms():
    assert t.unsourced_claims("该塔高三百二十四米", utterance="介绍高324米的埃菲尔铁塔") == []


def test_approximations_do_not_become_invented_numbers():
    assert t.page_mentions("两三页就够了") == {2.0, 3.0}      # existing range handling untouched, not 23
    assert 23.0 not in t.page_mentions("两三页就够了")
    assert t.duration_mentions("讲两三分钟") == set() or 23.0 not in t.duration_mentions("讲两三分钟")


def test_page_and_duration_still_read_big_and_decimal_numerals():
    assert t.page_mentions("共一百二十页") == {120.0}
    assert t.duration_mentions("讲二点五小时") == {150.0}
    assert t.parse_number("一千") == 1000.0 and t.parse_number("一万二") == 12000.0
    assert t.parse_number("万一") is None


def test_ungrounded_counts_with_chinese_forms():
    assert t.ungrounded_counts("共一百二十页", "做个PPT")
    assert not t.ungrounded_counts("共一百二十页", "做120页的PPT")
