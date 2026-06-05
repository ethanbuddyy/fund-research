"""domain/labels 共享判定的单测 —— 两个报告渲染器都依赖这一层。

这些阈值是业务口径（VIX 偏高线、信用偏紧线、趋势强弱线）。把它们钉死，
防止有人改动阈值时不自知地改变两份报告的结论。
"""
import pytest

from src.domain import labels as L


class TestVix:
    @pytest.mark.parametrize("v,expected", [
        (None, False), (0, False), (18, False), (25, False),
        (25.0001, True), (30, True), ("bad", False),
    ])
    def test_vix_elevated(self, v, expected):
        assert L.vix_elevated(v) is expected

    @pytest.mark.parametrize("v,expected", [
        (None, False), (14.99, False), (15, True), (20, True),
        (25, True), (25.01, False),
    ])
    def test_vix_neutral(self, v, expected):
        assert L.vix_neutral(v) is expected


class TestCredit:
    @pytest.mark.parametrize("v,expected", [
        (None, False), (3.5, True), (3.49, True), (3.51, False), (6, False),
    ])
    def test_credit_tight(self, v, expected):
        assert L.credit_tight(v) is expected

    @pytest.mark.parametrize("v,expected", [
        (None, False), (5.99, False), (6.0, True), (8, True),
    ])
    def test_credit_loose(self, v, expected):
        assert L.credit_loose(v) is expected


class TestTrend:
    @pytest.mark.parametrize("v,expected", [
        (None, "中性趋势"), (3.5, "弱趋势"), (3.4, "弱趋势"),
        (5, "中性趋势"), (6.5, "强趋势"), (7, "强趋势"),
    ])
    def test_trend_label(self, v, expected):
        assert L.trend_label(v) == expected

    @pytest.mark.parametrize("v,expected", [
        (None, False), (6.49, False), (6.5, True), (8, True),
    ])
    def test_trend_strong(self, v, expected):
        assert L.trend_strong(v) is expected


class TestConstantsAreSingleSource:
    def test_thresholds_match_predicates(self):
        """常量改动会同时改变两个渲染器：确保预测函数确实用的是这些常量。"""
        assert L.vix_elevated(L.VIX_ELEVATED + 0.01) is True
        assert L.vix_elevated(L.VIX_ELEVATED) is False
        assert L.credit_tight(L.CREDIT_TIGHT) is True
        assert L.trend_strong(L.TREND_STRONG) is True
