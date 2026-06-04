"""持仓健康诊断引擎单元测试"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from src.holdings.checker import (
    _parse_holdings,
    _compute_analytics,
    _verdict,
    parse_holdings_str,
    HoldingItem,
)


# ─────────────────────────────────────────────────────────────
# _parse_holdings
# ─────────────────────────────────────────────────────────────

class TestParseHoldings:
    def test_basic_normalization(self):
        raw = [{"fund_code": "A", "weight": 40}, {"fund_code": "B", "weight": 60}]
        items = _parse_holdings(raw)
        assert len(items) == 2
        assert abs(sum(i.weight for i in items) - 100.0) < 0.01

    def test_cash_special_code(self):
        raw = [{"fund_code": "cash", "weight": 30}, {"fund_code": "X", "weight": 70}]
        items = _parse_holdings(raw)
        cash = next(i for i in items if i.fund_code == "cash")
        assert cash.asset_class == "cash"
        assert cash.fund_name == "现金"

    def test_non_100_weights_normalized(self):
        raw = [{"fund_code": "A", "weight": 25}, {"fund_code": "B", "weight": 25}]
        items = _parse_holdings(raw)
        assert abs(sum(i.weight for i in items) - 100.0) < 0.01

    def test_zero_weight_raises(self):
        with pytest.raises(ValueError):
            _parse_holdings([{"fund_code": "A", "weight": 0}])

    def test_skips_empty_code(self):
        raw = [{"fund_code": "", "weight": 50}, {"fund_code": "A", "weight": 50}]
        items = _parse_holdings(raw)
        assert len(items) == 1
        assert items[0].fund_code == "A"


# ─────────────────────────────────────────────────────────────
# _compute_analytics
# ─────────────────────────────────────────────────────────────

class TestComputeAnalytics:
    def _make_signal(self, cash_alloc=0.10):
        return {"composite_signal": "标配稳健", "cash_allocation": cash_alloc}

    def test_hhi_single_class(self):
        items = [
            HoldingItem("A", 50, asset_class="broad_equity"),
            HoldingItem("B", 50, asset_class="broad_equity"),
        ]
        result = _compute_analytics(items, self._make_signal())
        # 两只全是 broad_equity → HHI = 1.0
        assert result["hhi"] == pytest.approx(1.0, abs=0.01)

    def test_hhi_perfect_split(self):
        items = [
            HoldingItem("A", 50, asset_class="broad_equity"),
            HoldingItem("B", 50, asset_class="bond"),
        ]
        result = _compute_analytics(items, self._make_signal())
        # 各占 50%，HHI = 0.5^2 + 0.5^2 = 0.5
        assert result["hhi"] == pytest.approx(0.5, abs=0.01)

    def test_cash_excluded_from_hhi(self):
        items = [
            HoldingItem(fund_code="cash", weight=50, asset_class="cash"),
            HoldingItem("A", 50, asset_class="broad_equity"),
        ]
        result = _compute_analytics(items, self._make_signal())
        # 非现金只有一类 → HHI=1.0
        assert result["hhi"] == pytest.approx(1.0, abs=0.01)

    def test_cash_pct_reported(self):
        items = [
            HoldingItem(fund_code="cash", weight=30, asset_class="cash"),
            HoldingItem("A", 70, asset_class="broad_equity"),
        ]
        result = _compute_analytics(items, self._make_signal(cash_alloc=0.10))
        assert result["cash_pct"] == pytest.approx(30.0, abs=0.1)
        assert result["recommended_cash_pct"] == pytest.approx(10.0, abs=0.1)

    def test_weighted_score_only_scored_funds(self):
        items = [
            HoldingItem("A", 60, asset_class="broad_equity",
                        score={"total_score": 80.0}, in_db=True),
            HoldingItem("B", 40, asset_class="broad_equity",
                        score=None, in_db=False),
        ]
        result = _compute_analytics(items, self._make_signal())
        # 只有 A 有评分，加权均分应等于 80
        assert result["weighted_score"] == pytest.approx(80.0, abs=0.1)


# ─────────────────────────────────────────────────────────────
# _verdict
# ─────────────────────────────────────────────────────────────

class TestVerdict:
    def _make_signal(self):
        return {"composite_signal": "标配稳健", "cash_allocation": 0.10}

    def _base_analytics(self, **overrides):
        base = {
            "asset_class_distribution": {"broad_equity": 100.0},
            "region_distribution": {"美国": 100.0},
            "cash_pct": 10.0,
            "recommended_cash_pct": 10.0,
            "weighted_score": 70.0,
            "weighted_strategy_score": 7.0,
            "weighted_expense_ratio": 0.8,
            "hhi": 0.35,
            "in_db_coverage_pct": 100.0,
        }
        base.update(overrides)
        return base

    def _base_gap(self, overlap=1):
        return {
            "overlap_count": overlap,
            "overlap_codes": ["A"] if overlap else [],
            "in_recommendation": [{"code": "A", "name": "基金A"}] if overlap else [],
            "missing_recommended": [],
            "not_in_recommendation": [],
        }

    def test_green_when_all_good(self):
        items = [HoldingItem("A", 100, asset_class="broad_equity", signal="买入")]
        v = _verdict(items, self._base_analytics(), self._base_gap(), self._make_signal())
        assert v["overall"] == "green"

    def test_red_on_high_hhi(self):
        a = self._base_analytics(hhi=0.85, asset_class_distribution={"broad_equity": 100.0})
        v = _verdict([], a, self._base_gap(), self._make_signal())
        assert v["overall"] == "red"
        assert any("集中" in iss for iss in v["issues"])

    def test_red_on_avoid_signal_heavy(self):
        items = [HoldingItem("A", 60, asset_class="broad_equity", signal="回避", fund_name="坏基金")]
        v = _verdict(items, self._base_analytics(), self._base_gap(), self._make_signal())
        assert v["overall"] == "red"

    def test_yellow_on_high_cash(self):
        a = self._base_analytics(cash_pct=50.0, recommended_cash_pct=10.0)
        v = _verdict([], a, self._base_gap(), self._make_signal())
        assert v["overall"] == "yellow"
        assert any("现金" in iss for iss in v["issues"])

    def test_yellow_on_no_overlap(self):
        a = self._base_analytics()
        items = [HoldingItem("X", 100, asset_class="broad_equity")]
        v = _verdict(items, a, self._base_gap(overlap=0), self._make_signal())
        assert v["overall"] == "yellow"

    def test_strength_on_good_score(self):
        a = self._base_analytics(weighted_score=75.0, hhi=0.3)
        v = _verdict([], a, self._base_gap(), self._make_signal())
        assert any("评分" in s for s in v["strengths"])

    def test_actions_suggested_on_red(self):
        a = self._base_analytics(hhi=0.9, asset_class_distribution={"broad_equity": 100.0})
        v = _verdict([], a, self._base_gap(), self._make_signal())
        assert len(v["actions"]) > 0


# ─────────────────────────────────────────────────────────────
# parse_holdings_str
# ─────────────────────────────────────────────────────────────

class TestParseHoldingsStr:
    def test_basic(self):
        result = parse_holdings_str("519915:40,050025:30,cash:30")
        assert len(result) == 3
        assert result[0] == {"fund_code": "519915", "weight": 40.0}
        assert result[2] == {"fund_code": "cash", "weight": 30.0}

    def test_spaces_stripped(self):
        result = parse_holdings_str(" A : 50 , B : 50 ")
        assert result[0]["fund_code"] == "A"
        assert result[0]["weight"] == 50.0

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError):
            parse_holdings_str("nocolon")
