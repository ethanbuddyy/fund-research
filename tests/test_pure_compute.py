"""阶段3 回归：数据加载与纯计算分离。

纯函数 compute_market_signal / score_funds / select_portfolio 只靠内存数据即可
测试，不碰 SQLite / 配置文件 / AI。覆盖交接单「五、建议测试」中的：
  #6 select_portfolio 在纯内存输入下可重复得到同一结果。
  #8 AI 关闭时，纯量化路径行为与重构前一致（确定性、与 classify_signal 同源）。
"""
import pandas as pd
import pytest

from src.domain.scoring import POSITION_TIERS, classify_signal
from src.domain.factor_config import FACTOR_WEIGHTS


# ────────────────────────────────────────────────────────────────────
# compute_market_signal —— 纯函数：确定性、与 classify_signal 同源
# ────────────────────────────────────────────────────────────────────

def _signal_inputs(score: float):
    """构造各因子分均为 score 的内存 inputs；因子权重和为 1 → composite_raw==score。"""
    return {
        "date": "2026-06-09",
        "data_source": "real",
        "data_quality": {},
        "macro": {"cycle_score": score, "fed_direction_score": 0.0, "cycle": "扩张"},
        "valuation": {"valuation_score": score, "valuation_level": "中性"},
        "sentiment": {"score": (10 - score) * 10, "label": "中性"},  # → contrarian == score
        "trend_score": score,
        "credit_score": score,
        "global_macro": {"available": False, "regions": {}},
        "global_macro_score": score,
        "narrative": {"insights": ["x"]},
    }


class TestComputeMarketSignal:
    def test_factor_weights_sum_to_one(self):
        # 测试前提：六因子权重和为 1，否则下面的 composite_raw==score 不成立
        assert sum(FACTOR_WEIGHTS.values()) == pytest.approx(1.0)

    def test_aggressive_signal_uses_position_tiers(self):
        from src.recommender.signals import compute_market_signal
        sig = compute_market_signal(_signal_inputs(8.0), config={})

        assert sig["timing_score"] == pytest.approx(8.0)
        assert sig["composite_signal"] == "重仓进取"
        core, sat, cash = POSITION_TIERS["重仓进取"]
        assert (sig["core_allocation"], sig["satellite_allocation"],
                sig["cash_allocation"]) == (core, sat, cash)
        assert sig["signal_color"] == "green"
        # 与 classify_signal 同源
        name, c, s, h = classify_signal(sig["timing_score"])
        assert (name, c, s, h) == ("重仓进取", core, sat, cash)

    def test_defensive_signal(self):
        from src.recommender.signals import compute_market_signal
        sig = compute_market_signal(_signal_inputs(2.0), config={})
        assert sig["composite_signal"] == "减仓防守"
        assert (sig["core_allocation"], sig["satellite_allocation"],
                sig["cash_allocation"]) == POSITION_TIERS["减仓防守"]

    def test_deterministic(self):
        from src.recommender.signals import compute_market_signal
        a = compute_market_signal(_signal_inputs(6.0), config={})
        b = compute_market_signal(_signal_inputs(6.0), config={})
        assert a == b

    def test_user_profile_applied_from_config(self):
        from src.recommender.signals import compute_market_signal
        cfg = {"user_profile": {"risk_tolerance": "conservative",
                                "investment_horizon_years": 2}}
        sig = compute_market_signal(_signal_inputs(8.0), config=cfg)
        assert sig["user_profile_applied"] is True
        total = (sig["core_allocation"] + sig["satellite_allocation"]
                 + sig["cash_allocation"])
        assert total == pytest.approx(1.0, abs=0.01)
        # 保守 + 短期限 → 权益应低于默认重仓进取的 0.95
        assert sig["core_allocation"] + sig["satellite_allocation"] < 0.95

    def test_no_user_profile_keeps_classify_allocation(self):
        from src.recommender.signals import compute_market_signal
        sig = compute_market_signal(_signal_inputs(8.0), config={})
        assert sig["user_profile_applied"] is False
        assert sig["user_profile"] is None


# ────────────────────────────────────────────────────────────────────
# score_funds —— 纯函数：内存输入、确定性、降序
# ────────────────────────────────────────────────────────────────────

_SCORING_CFG = {
    "scoring_weights": {
        "performance": 0.30, "risk_adjusted": 0.25, "strategy_match": 0.20,
        "cost_efficiency": 0.15, "consistency": 0.10,
    },
    "strategy_params": {
        "cost_filter": {"preferred_expense_ratio": 0.005, "max_expense_ratio": 0.015},
    },
}


def _funds_df():
    return pd.DataFrame([
        {"fund_code": "513100", "fund_name": "纳斯达克100ETF", "fund_type": "ETF",
         "benchmark": "纳斯达克100", "expense_ratio": 0.006},
        {"fund_code": "513500", "fund_name": "标普500ETF", "fund_type": "ETF",
         "benchmark": "标普500", "expense_ratio": 0.006},
        {"fund_code": "164906", "fund_name": "华宝标普油气LOF", "fund_type": "LOF",
         "benchmark": "标普油气", "expense_ratio": 0.0072},
    ])


def _perf_df():
    return pd.DataFrame([
        {"fund_code": "513100", "return_6m": 12.0, "return_1y": 30.0, "return_3y": 60.0,
         "sharpe_ratio": 1.3, "max_drawdown": -18.0, "volatility": 22.0},
        {"fund_code": "513500", "return_6m": 8.0, "return_1y": 20.0, "return_3y": 45.0,
         "sharpe_ratio": 1.1, "max_drawdown": -14.0, "volatility": 17.0},
        {"fund_code": "164906", "return_6m": -5.0, "return_1y": -8.0, "return_3y": 5.0,
         "sharpe_ratio": 0.2, "max_drawdown": -35.0, "volatility": 40.0},
    ])


class TestScoreFundsPure:
    def test_ranked_and_bounded(self):
        from src.recommender.scorer import score_funds
        out = score_funds(_funds_df(), _perf_df(), {},
                          {"composite_signal": "标配稳健"}, _SCORING_CFG)
        assert not out.empty
        assert list(out["total_score"]) == sorted(out["total_score"], reverse=True)
        assert out["total_score"].between(0, 100).all()

    def test_deterministic(self):
        from src.recommender.scorer import score_funds
        a = score_funds(_funds_df(), _perf_df(), {},
                        {"composite_signal": "标配稳健"}, _SCORING_CFG)
        b = score_funds(_funds_df(), _perf_df(), {},
                        {"composite_signal": "标配稳健"}, _SCORING_CFG)
        pd.testing.assert_frame_equal(a.reset_index(drop=True), b.reset_index(drop=True))

    def test_empty_funds_returns_empty(self):
        from src.recommender.scorer import score_funds
        out = score_funds(pd.DataFrame(), pd.DataFrame(), {},
                          {"composite_signal": "标配稳健"}, _SCORING_CFG)
        assert out.empty


# ────────────────────────────────────────────────────────────────────
# select_portfolio —— 纯函数：内存输入下可重复（#6）+ 仓位反映信号
# ────────────────────────────────────────────────────────────────────

def _scores_df():
    return pd.DataFrame([
        {"fund_code": "513100", "fund_name": "纳斯达克100ETF", "total_score": 82.0,
         "signal": "买入", "performance_score": 8.0, "risk_score": 7.5,
         "strategy_score": 7.0, "consistency_score": 6.5, "cost_score": 9.0},
        {"fund_code": "513500", "fund_name": "标普500ETF", "total_score": 78.0,
         "signal": "买入", "performance_score": 7.5, "risk_score": 7.8,
         "strategy_score": 7.2, "consistency_score": 6.8, "cost_score": 9.0},
        {"fund_code": "164906", "fund_name": "华宝标普油气LOF", "total_score": 55.0,
         "signal": "观望", "performance_score": 4.0, "risk_score": 3.5,
         "strategy_score": 5.0, "consistency_score": 4.0, "cost_score": 8.0},
    ])


_SELECT_CFG = {"rebalancing": {"score_threshold": 10}}


class TestSelectPortfolioPure:
    def _signal(self):
        return {"composite_signal": "标配稳健", "core_allocation": 0.60,
                "satellite_allocation": 0.30, "cash_allocation": 0.10}

    def test_repeatable(self):
        """#6：同一内存输入重复调用得到完全相同的组合。"""
        from src.recommender.portfolio import select_portfolio
        a = select_portfolio(_scores_df(), _funds_df(), self._signal(), None, _SELECT_CFG)
        b = select_portfolio(_scores_df(), _funds_df(), self._signal(), None, _SELECT_CFG)
        assert a["core_funds"] == b["core_funds"]
        assert a["satellite_funds"] == b["satellite_funds"]
        assert a["top_picks"] == b["top_picks"]

    def test_allocation_reflects_signal_no_ai_no_snapshot(self):
        from src.recommender.portfolio import select_portfolio
        out = select_portfolio(_scores_df(), _funds_df(), self._signal(), None, _SELECT_CFG)
        assert out["core_allocation_pct"] == 60.0
        assert out["satellite_allocation_pct"] == 30.0
        assert out["cash_allocation_pct"] == 10.0
        # 纯函数不掺 AI、不掺快照落盘数据
        assert "ai_decision" not in out
        assert "snapshot_payload" not in out
        # 携带上期快照原文（首次运行为 None），供报告层对比
        assert out["previous_portfolio"] is None

    def test_previous_portfolio_threshold(self):
        """上期持有 164906，本期高分新基需超门槛才替换——验证门槛逻辑走纯函数。"""
        from src.recommender.portfolio import select_portfolio
        prev = {"core": {"513500": {"score": 78.0}}, "satellite": {"164906": {"score": 55.0}}}
        out = select_portfolio(_scores_df(), _funds_df(), self._signal(), prev, _SELECT_CFG)
        # 上期核心 513500 应被保留
        core_codes = {f["fund_code"] for f in out["core_funds"]}
        assert "513500" in core_codes

    def test_multiple_candidates_can_replace_multiple_incumbents(self):
        from src.recommender.portfolio import _select_funds

        pool = pd.DataFrame([
            {"fund_code": "C", "fund_name": "C", "fund_type": "指数", "total_score": 70.0},
            {"fund_code": "D", "fund_name": "D", "fund_type": "指数", "total_score": 65.0},
            {"fund_code": "A", "fund_name": "A", "fund_type": "指数", "total_score": 50.0},
            {"fund_code": "B", "fund_name": "B", "fund_type": "指数", "total_score": 40.0},
        ])

        picks = _select_funds(
            pool, 0.60, max_n=2,
            prev_scores={"A": 50.0, "B": 40.0},
            score_threshold=10.0, role="核心",
        )
        assert [p["fund_code"] for p in picks] == ["C", "D"]

    def test_no_eligible_funds_moves_unallocated_target_to_cash(self):
        from src.recommender.portfolio import select_portfolio

        scores = pd.DataFrame([{
            "fund_code": "ACTIVE", "fund_name": "全球主动精选", "total_score": 99.0,
        }])
        funds = pd.DataFrame([{
            "fund_code": "ACTIVE", "fund_name": "全球主动精选",
            "fund_type": "主动QDII", "expense_ratio": 0.01,
        }])

        out = select_portfolio(scores, funds, self._signal(), None, _SELECT_CFG)
        assert out["core_funds"] == []
        assert out["satellite_funds"] == []
        assert out["total_invested_pct"] == 0
        assert out["cash_allocation_pct"] == 100
        assert out["allocation_shortfall_pct"] == 90

    def test_active_fund_is_excluded_even_when_highest_scoring(self):
        """主动基金即使得分最高，也不能进入推荐或备选榜。"""
        from src.recommender.portfolio import select_portfolio

        scores = pd.concat([
            pd.DataFrame([{
                "fund_code": "270023", "fund_name": "广发全球精选",
                "total_score": 99.0, "signal": "买入",
                "performance_score": 10.0, "risk_score": 10.0,
                "strategy_score": 10.0, "consistency_score": 10.0,
                "cost_score": 10.0,
            }]),
            _scores_df(),
        ], ignore_index=True)
        funds = pd.concat([
            pd.DataFrame([{
                "fund_code": "270023", "fund_name": "广发全球精选",
                "fund_type": "主动QDII", "expense_ratio": 0.014,
            }]),
            _funds_df(),
        ], ignore_index=True)

        out = select_portfolio(scores, funds, self._signal(), None, _SELECT_CFG)
        all_codes = {
            f["fund_code"]
            for f in out["core_funds"] + out["satellite_funds"] + out["top_picks"]
        }
        assert "270023" not in all_codes
        assert out["index_only"] is True
