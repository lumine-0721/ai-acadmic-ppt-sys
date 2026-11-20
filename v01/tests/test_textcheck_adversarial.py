"""Adversarial counterexample set for the deterministic checks.

BAD_*: deliberately bad model outputs that MUST be flagged.
BENIGN_*: ordinary phrasing that must NOT be flagged.
Extend this file (never loosen it) when a real model run shows a miss or a false alarm.
"""

import pytest

from pptv01 import textcheck as t

# ---- unsourced numbers / quantities / conclusions: must be flagged
BAD_CLAIMS = [
    "研究表明热岛使死亡率上升23%",
    "死亡率上升百分之二十三",
    "百分之五十的城市受影响",
    "约三成城市存在明显热岛",
    "近七成居民受到影响",
    "升温2倍",
    "用电量是郊区的两倍",
    "气温可高出3摄氏度",
    "升温约 2.5℃",
    "夜间最高可差 5 °C",
    "该塔高 324 米",
    "建筑面积超过十万平方米",
    "年排放约五亿吨二氧化碳",
    "投资约300万元",
    "增长了十二个百分点",
    "占总量的五分之一",
    "Studies show that cities are hotter",
    "Research has shown a 12 % rise",
    "数据显示城市更热",
    "调查发现多数人感到不适",
    "报告显示用电负荷大增",
    "事实证明绿化有效",
    "该结论已被证实",
    "毫无疑问，这是最主要的成因",
    "票价上涨了3倍以上",
    "平均每年减少 40 吨排放",
    "时速达到350公里",
]

BENIGN_CLAIMS = [
    "城区气温相对周边偏高",
    "形成原因与地表材料有关",
    "一成不变的看法需要打破",
    "二元对立的叙事要避免",
    "1998年的一项研究",
    "近30年的气温记录（待检索）",
    "三座代表性建筑",
    "每一页只讲一个点",
    "第三部分讨论应对",
    "建成于十九世纪",
    "五个主要成因",
    "一张可解释的对比图",
    "需要核实具体数值",
    "使用元数据标注来源",
    "中心城区与郊区的对比",
    "完成度较高的草图",
    "世界遗产名录中的建筑",
    "不同时代的建筑风格",
]


@pytest.mark.parametrize("text", BAD_CLAIMS)
def test_bad_claims_flagged(text):
    assert t.unsourced_claims(text), f"missed: {text}"


@pytest.mark.parametrize("text", BENIGN_CLAIMS)
def test_benign_claims_not_flagged(text):
    assert t.unsourced_claims(text) == [], f"false alarm: {text}: {t.unsourced_claims(text)}"


def test_numbers_the_user_wrote_are_not_flagged():
    assert t.unsourced_claims("预算 50万元 以内", "我有50万元预算，帮我做创业计划") == []
    assert t.unsourced_claims("预算 80万元", "我有50万元预算") != []


# ---- page counts
@pytest.mark.parametrize("text,expect", [
    ("两三页", {2, 3}), ("三五页就够", {3, 5}), ("3~5页", {3, 5}), ("三到五页", {3, 5}), ("5至8页", {5, 8}),
    ("3-5 pages", {3, 5}), ("共十页", {10}), ("一共 12 张幻灯片", {12}), ("20 slides", {20}),
    ("二十一页", {21}), ("一页或两页", {1, 2}),
])
def test_page_counts_parsed(text, expect):
    assert t.page_mentions(text) == expect


@pytest.mark.parametrize("text", [
    "一页纸讲清楚", "把它浓缩到一页纸上", "每一页都要有图", "这一页讲定义", "单页只放一个结论", "一页只讲一个点",
    "一页最多三句话", "下一页再展开", "页面布局要简洁", "第3页放成因图", "页码从封面之后开始",
])
def test_not_a_page_count_constraint(text):
    assert t.page_mentions(text) == set(), text


@pytest.mark.parametrize("text", ["十几页", "几十页的篇幅", "十来页", "二十余页"])
def test_vague_counts_flagged_when_invented(text):
    assert t.ungrounded_counts(text, "帮我做个PPT")
    assert not t.ungrounded_counts(text, text)


def test_page_limit_not_from_one_page_paper():
    assert t.page_limit("请用一页纸讲清楚热岛效应") is None
    assert t.page_limit("做一份不超过五页的介绍") == ("max", 5)
    assert t.page_limit("三到五页的介绍") is None  # a range is not a single hard number


# ---- durations
@pytest.mark.parametrize("text,expect", [
    ("我要讲10分钟", {10}), ("大约十五分钟", {15}), ("半小时", {30}), ("1小时", {60}), ("15-20 minutes", {15, 20}),
    ("10分钟内讲完", {10}),
])
def test_total_durations(text, expect):
    assert t.duration_mentions(text) == expect


@pytest.mark.parametrize("text", [
    "约5分钟讲一个点", "每页2分钟", "每个要点讲1分钟", "平均每张 30 秒", "一页约3分钟", "1分钟/页", "每页大约两分钟",
])
def test_per_page_pace_is_not_total_duration(text):
    assert t.duration_mentions(text) == set(), text


def test_invented_total_duration_still_flagged():
    assert t.ungrounded_counts("整场约20分钟", "帮我做个PPT")
    assert not t.ungrounded_counts("约10分钟", "我明天要讲10分钟的芯片")
