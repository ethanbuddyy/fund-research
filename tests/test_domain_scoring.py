"""单元测试：domain/scoring.py 纯函数边界值与分档逻辑"""
import pytest
from src.domain.scoring import (
    classify_signal,
    consistency_score,
    credit_score_from_spread,
    trend_score_from_deviation,
    cost_score,
)


class TestClassifySignal:
    def test_above_7_is_aggressive(self):
        sig, core, sat, cash = classify_signal(7.0)
        assert sig == "重仓进取"
        assert core == 0.70
        assert abs(core + sat + cash - 1.0) < 1e-9

    def test_between_5_and_7_is_balanced(self):
        sig, core, sat, cash = classify_signal(6.0)
        assert sig == "标配稳健"
        assert abs(core + sat + cash - 1.0) < 1e-9

    def test_between_3_and_5_is_defensive(self):
        sig, core, sat, cash = classify_signal(4.0)
        assert sig == "谨慎防守"
        assert abs(core + sat + cash - 1.0) < 1e-9

    def test_below_3_is_max_defensive(self):
        sig, core, sat, cash = classify_signal(2.9)
        assert sig == "减仓防守"
        assert abs(core + sat + cash - 1.0) < 1e-9

    def test_boundary_exactly_5(self):
        sig, *_ = classify_signal(5.0)
        assert sig == "标配稳健"

    def test_boundary_exactly_3(self):
        sig, *_ = classify_signal(3.0)
        assert sig == "谨慎防守"

    def test_allocation_sums_to_1_all_tiers(self):
        for score in [0.0, 2.5, 3.0, 5.0, 7.0, 10.0]:
            _, core, sat, cash = classify_signal(score)
            assert abs(core + sat + cash - 1.0) < 1e-9, f"score={score} 仓位之和不为1"


class TestConsistencyScore:
    def test_all_positive_returns_gives_high_score(self):
        score = consistency_score([0.10, 0.15, 0.08, 0.12])
        assert score >= 7.0

    def test_all_negative_returns_gives_low_score(self):
        score = consistency_score([-0.05, -0.10, -0.08])
        assert score < 5.0

    def test_insufficient_data_returns_neutral(self):
        assert consistency_score([]) == 5.0
        assert consistency_score([0.10]) == 5.0

    def test_none_values_are_ignored(self):
        score = consistency_score([0.10, None, 0.15])
        assert 0.0 <= score <= 10.0

    def test_result_in_range(self):
        for data in [[0.5, -0.5, 0.5, -0.5], [0.1] * 10, [-0.2] * 5]:
            score = consistency_score(data)
            assert 0.0 <= score <= 10.0, f"data={data} score={score} 超出范围"


class TestCreditScoreFromSpread:
    def test_tight_spread_high_score(self):
        assert credit_score_from_spread(2.5) == 8.0

    def test_wide_spread_low_score(self):
        assert credit_score_from_spread(9.0) == 2.0

    def test_boundary_at_3(self):
        assert credit_score_from_spread(3.0) == 6.5

    def test_boundary_at_4(self):
        assert credit_score_from_spread(4.0) == 5.0

    def test_boundary_at_5_5(self):
        assert credit_score_from_spread(5.5) == 3.5

    def test_boundary_at_8(self):
        assert credit_score_from_spread(8.0) == 2.0


class TestTrendScoreFromDeviation:
    def test_strong_uptrend_high_score(self):
        assert trend_score_from_deviation(0.10) == 8.0

    def test_strong_downtrend_low_score(self):
        assert trend_score_from_deviation(-0.10) == 2.0

    def test_flat_neutral(self):
        assert trend_score_from_deviation(0.0) == 5.0

    def test_boundary_at_0_08(self):
        assert trend_score_from_deviation(0.08) == 6.5

    def test_boundary_at_minus_0_08(self):
        assert trend_score_from_deviation(-0.08) == 2.0


class TestCostScore:
    _cfg = {
        "strategy_params": {
            "cost_filter": {
                "preferred_expense_ratio": 0.005,
                "max_expense_ratio": 0.015,
            }
        }
    }

    def test_below_preferred_gives_10(self):
        assert cost_score(0.003, self._cfg) == 10.0

    def test_at_preferred_gives_10(self):
        assert cost_score(0.005, self._cfg) == 10.0

    def test_at_max_gives_5(self):
        score = cost_score(0.015, self._cfg)
        assert abs(score - 5.0) < 1e-9

    def test_above_max_penalizes(self):
        assert cost_score(0.020, self._cfg) < 5.0

    def test_result_non_negative(self):
        assert cost_score(0.10, self._cfg) >= 0.0
