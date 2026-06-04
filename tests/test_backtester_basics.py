"""单元测试：回测引擎的关键数学逻辑"""
import pytest


# ── 60/40 基准计算验证 ────────────────────────────────────────
RF_ANNUAL = 0.02  # 与 engine.py 保持一致


def _b6040(sp500_ret: float) -> float:
    """复现 engine.py 中修复后的 60/40 基准公式"""
    return sp500_ret * 0.6 + (RF_ANNUAL / 12) * 0.4


class TestB6040Benchmark:
    def test_flat_sp500_still_earns_cash_return(self):
        """SP500 零收益时，60/40 组合应仍有现金收益"""
        result = _b6040(0.0)
        expected_cash = (RF_ANNUAL / 12) * 0.4
        assert abs(result - expected_cash) < 1e-10

    def test_negative_sp500_offset_by_cash(self):
        """SP500 下跌时，现金部分应部分对冲损失"""
        sp500_monthly = -0.05
        result = _b6040(sp500_monthly)
        # 纯 SP500 = -0.05，60/40 应大于 -0.05 * 0.6（因为有现金正收益）
        assert result > sp500_monthly * 0.6

    def test_positive_sp500_less_than_pure_equity(self):
        """SP500 上涨时，60/40 收益应低于纯股票（但高于零）"""
        sp500_monthly = 0.03
        result = _b6040(sp500_monthly)
        assert 0 < result < sp500_monthly

    def test_annual_rf_2pct_monthly_component(self):
        """验证月度无风险利率正确从年化折算"""
        monthly_rf = RF_ANNUAL / 12
        assert abs(monthly_rf - 0.02 / 12) < 1e-12

    def test_formula_symmetry(self):
        """60% + 40% 权重应正确分配"""
        sp500 = 0.10
        result = _b6040(sp500)
        manual = sp500 * 0.6 + (RF_ANNUAL / 12) * 0.4
        assert abs(result - manual) < 1e-15


# ── 换手率计算验证 ────────────────────────────────────────────

def _turnover(prev: set, cur: set) -> float:
    if not prev:
        return 1.0
    n_total = max(len(cur | prev), 1)
    n_changed = len(cur.symmetric_difference(prev))
    return n_changed / n_total


class TestTurnoverCalc:
    def test_first_period_is_full_turnover(self):
        assert _turnover(set(), {"A", "B", "C"}) == 1.0

    def test_no_change_zero_turnover(self):
        funds = {"A", "B", "C"}
        assert _turnover(funds, funds) == 0.0

    def test_full_replacement(self):
        prev = {"A", "B"}
        cur = {"C", "D"}
        # 4 个不同基金全部换，changed=4, total=4
        assert _turnover(prev, cur) == 1.0

    def test_partial_change(self):
        prev = {"A", "B", "C"}
        cur = {"A", "B", "D"}  # 换掉1只
        result = _turnover(prev, cur)
        assert 0 < result < 1.0
