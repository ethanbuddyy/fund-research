"""阶段6 回归：收紧核心类型契约。

验证（运行期可断言的部分）：
  - MarketFacts（事实层，必填）与 MarketDecision（决策层，必填）分层且键不相交；
    MarketSignal = 两者必填 + AI/叙事/审查/止损 可选（交接单要求 #1、#2）。
  - PortfolioRecommendation 核心必填、扩展可选。
  - StopLossResult / PortfolioState / FundScore / PortfolioFund 契约存在且键齐备。
  - 行为不变量 #1：apply_stop_loss 只覆盖决策层，**不污染事实层**，且不原地修改入参。
mypy 层面的「必填校验」由 `mypy src/` 守护（构造点漏字段会报错）。
"""
import copy

from src.domain.scoring import POSITION_TIERS
from src.domain.types import (
    MarketFacts, MarketDecision, MarketSignal,
    PortfolioRecommendation, _PortfolioCore,
    StopLossResult, PortfolioState, FundScore, PortfolioFund,
)


# ────────────────────────────────────────────────────────────────────
# 类型结构：事实/决策分层、必填 vs 可选
# ────────────────────────────────────────────────────────────────────

class TestMarketSignalContract:
    def test_facts_and_decision_keys_disjoint(self):
        assert MarketFacts.__required_keys__.isdisjoint(MarketDecision.__required_keys__)

    def test_facts_required_core(self):
        for k in ("date", "data_source", "cape", "vix", "timing_score", "macro", "valuation"):
            assert k in MarketFacts.__required_keys__

    def test_decision_required_core(self):
        for k in ("composite_signal", "signal_color",
                  "core_allocation", "satellite_allocation", "cash_allocation"):
            assert k in MarketDecision.__required_keys__

    def test_marketsignal_requires_facts_and_decision(self):
        req = MarketSignal.__required_keys__
        assert MarketFacts.__required_keys__ <= req
        assert MarketDecision.__required_keys__ <= req

    def test_marketsignal_optionals(self):
        opt = MarketSignal.__optional_keys__
        for k in ("ai_analysis", "narrative", "stop_loss", "stop_loss_triggered",
                  "user_profile_applied", "user_profile"):
            assert k in opt, k
        # 核心字段不得滑入可选
        assert "composite_signal" not in opt
        assert "timing_score" not in opt


class TestPortfolioContract:
    def test_core_required(self):
        for k in ("composite_signal", "core_allocation_pct", "core_funds",
                  "satellite_funds", "total_invested_pct", "top_picks", "investment_notes"):
            assert k in _PortfolioCore.__required_keys__
            assert k in PortfolioRecommendation.__required_keys__

    def test_extensions_optional(self):
        opt = PortfolioRecommendation.__optional_keys__
        for k in ("score_threshold", "ai_decision", "adversarial_review",
                  "previous_portfolio", "snapshot_payload"):
            assert k in opt, k


class TestSmallContracts:
    def test_stop_loss_result_keys(self):
        for k in ("triggered", "portfolio_nav", "high_water_mark", "drawdown_pct",
                  "threshold_pct", "period_return_pct", "funds_tracked", "note"):
            assert k in StopLossResult.__optional_keys__

    def test_portfolio_state_required(self):
        assert PortfolioState.__required_keys__ == frozenset({"date", "core", "satellite"})

    def test_fund_contracts_have_keys(self):
        assert "total_score" in FundScore.__optional_keys__
        assert {"fund_code", "weight", "role"} <= PortfolioFund.__optional_keys__


# ────────────────────────────────────────────────────────────────────
# 行为不变量 #1：止损只覆盖决策层，不污染事实层
# ────────────────────────────────────────────────────────────────────

def _full_signal() -> dict:
    from src.recommender.signals import compute_market_signal
    inputs = {
        "date": "2026-06-09", "data_source": "real", "data_quality": {},
        "macro": {"cycle_score": 6.0, "fed_direction_score": 0.0, "cycle": "扩张"},
        "valuation": {"valuation_score": 6.0, "valuation_level": "中性"},
        "sentiment": {"score": 40, "label": "中性"},
        "trend_score": 6.0, "credit_score": 6.0,
        "global_macro": {"available": False, "regions": {}}, "global_macro_score": 6.0,
        "narrative": {"insights": ["x"]},
    }
    return compute_market_signal(inputs, {})


class TestStopLossDoesNotTouchFacts:
    def test_triggered_changes_only_decision(self):
        from src.recommender.signals import apply_stop_loss
        sig = _full_signal()
        before = copy.deepcopy(sig)

        out = apply_stop_loss(sig, {"triggered": True, "drawdown_pct": 20.0})

        # 事实层逐字不变
        for k in MarketFacts.__required_keys__:
            assert out[k] == before[k], f"事实字段 {k} 被止损污染"
        # 决策层被覆盖
        assert out["composite_signal"] == "减仓防守"
        assert (out["core_allocation"], out["satellite_allocation"],
                out["cash_allocation"]) == POSITION_TIERS["减仓防守"]
        assert out["signal_color"] == "red"
        # 纯函数：入参未被原地修改
        assert sig == before

    def test_not_triggered_preserves_everything(self):
        from src.recommender.signals import apply_stop_loss
        sig = _full_signal()
        before = copy.deepcopy(sig)
        out = apply_stop_loss(sig, {"triggered": False})
        for k in list(MarketFacts.__required_keys__) + list(MarketDecision.__required_keys__):
            assert out[k] == before[k], f"未触发却改动了 {k}"
        assert sig == before
