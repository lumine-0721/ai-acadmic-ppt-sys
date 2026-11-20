import pytest

from pptv01 import textcheck as t


@pytest.mark.parametrize("s,expect", [
    ("共 10 页", {10}), ("约12张幻灯片", {12}), ("十五页", {15}), ("5-8页", {5, 8}), ("20 slides", {20}),
    ("每一页都要有图", set()), ("这一页讲定义", set()), ("下一页", set()), ("第3页", set()), ("二十一页", {21}),
])
def test_page_mentions(s, expect):
    assert t.page_mentions(s) == expect


@pytest.mark.parametrize("s,expect", [
    ("10分钟", {10}), ("十分钟", {10}), ("半小时", {30}), ("1小时", {60}), ("15-20 minutes", {15, 20}), ("两小时", {120}),
])
def test_duration_mentions(s, expect):
    assert t.duration_mentions(s) == expect


@pytest.mark.parametrize("s,expect", [
    ("不超过5页", ("max", 5)), ("最多八页", ("max", 8)), ("5页以内", ("max", 5)), ("做一份2页的介绍", ("exact", 2)),
    ("做个PPT", None),
])
def test_page_limit(s, expect):
    assert t.page_limit(s) == expect
