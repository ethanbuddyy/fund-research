"""主链路集成测试 —— 直接调用生产函数（区别于复刻逻辑的副本测试）。

覆盖三条核心链路，仅在「数据库 / 网络」边界打桩：
  - scorer.score_all_funds            （评分引擎，真实三段式相对排名）
  - portfolio.build_portfolio_recommendation （组合构建 + 仓位口径一致性）
  - backtester.engine.run_backtest    （走向前回测主循环）
  - backtester.engine.calc_metrics    （绩效指标，纯函数真实调用）

设计要点：用 monkeypatch 把模块级 read_table / upsert_dataframe / load_config /
_fetch_sp500_full 替换为内存假数据，使断言作用于「真实生产代码路径」，
生产逻辑回归时测试会真正变红。
"""
import numpy as np
import pandas as pd
import pytest


# ────────────────────────────────────────────────────────────────────
# 公共：构造内存假表
# ────────────────────────────────────────────────────────────────────

def _fund_list_df():
    return pd.DataFrame([
        {"fund_code": "513100", "fund_name": "纳斯达克100ETF(华夏)", "fund_type": "ETF",
         "benchmark": "纳斯达克100", "expense_ratio": 0.006},
        {"fund_code": "513500", "fund_name": "标普500ETF(南方)", "fund_type": "ETF",
         "benchmark": "标普500", "expense_ratio": 0.006},
        {"fund_code": "164906", "fund_name": "华宝标普油气LOF", "fund_type": "LOF",
         "benchmark": "标普油气", "expense_ratio": 0.0072},
    ])


def _fund_performance_df():
    return pd.DataFrame([
        {"fund_code": "513100", "return_6m": 12.0, "return_1y": 30.0, "return_3y": 60.0,
         "sharpe_ratio": 1.3, "max_drawdown": -18.0, "volatility": 22.0},
        {"fund_code": "513500", "return_6m": 8.0, "return_1y": 20.0, "return_3y": 45.0,
         "sharpe_ratio": 1.1, "max_drawdown": -14.0, "volatility": 17.0},
        {"fund_code": "164906", "return_6m": -5.0, "return_1y": -8.0, "return_3y": 5.0,
         "sharpe_ratio": 0.2, "max_drawdown": -35.0, "volatility": 40.0},
    ])


_SCORING_CFG = {
    "scoring_weights": {
        "performance": 0.30, "risk_adjusted": 0.25, "strategy_match": 0.20,
        "cost_efficiency": 0.15, "consistency": 0.10,
    },
    "strategy_params": {
        "cost_filter": {"preferred_expense_ratio": 0.005, "max_expense_ratio": 0.015},
    },
}


# ────────────────────────────────────────────────────────────────────
# 1. score_all_funds —— 真实评分引擎
# ────────────────────────────────────────────────────────────────────

class TestScoreAllFundsReal:
    def _patch(self, monkeypatch):
        import src.recommender.scorer as scorer

        def fake_read_table(table, where="", params=()):
            if table == "fund_list":
                return _fund_list_df()
            if table == "fund_performance":
                return _fund_performance_df()
            if table == "fund_holdings":
                return pd.DataFrame(columns=["fund_code", "date", "stock_ratio",
                                             "bond_ratio", "cash_ratio"])
            return pd.DataFrame()

        captured = {}

        def fake_upsert(df, table, unique_cols):
            captured["df"] = df.copy()
            captured["table"] = table

        monkeypatch.setattr(scorer, "read_table", fake_read_table)
        monkeypatch.setattr(scorer, "upsert_dataframe", fake_upsert)
        monkeypatch.setattr(scorer, "load_config", lambda: _SCORING_CFG)
        return scorer, captured

    def test_returns_ranked_dataframe(self, monkeypatch):
        scorer, captured = self._patch(monkeypatch)
        out = scorer.score_all_funds({"composite_signal": "标配稳健"})

        assert not out.empty
        assert list(out["total_score"]) == sorted(out["total_score"], reverse=True), \
            "结果应按 total_score 降序排列"
        # 全部分值落在 0–100
        assert out["total_score"].between(0, 100).all()
        # 信号是已知档位之一
        assert set(out["signal"]).issubset({"买入", "增持", "持有", "观望", "回避"})
        # 写库被调用且目标表正确
        assert captured["table"] == "fund_scores"

    def test_strong_fund_outranks_weak_fund(self, monkeypatch):
        scorer, _ = self._patch(monkeypatch)
        out = scorer.score_all_funds({"composite_signal": "标配稳健"})
        scores = dict(zip(out["fund_code"], out["total_score"]))
        # 高收益低回撤的 513100/513500 应显著强于高回撤高波动的 164906
        assert scores["513500"] > scores["164906"]
        assert scores["513100"] > scores["164906"]

    def test_empty_fund_list_returns_empty(self, monkeypatch):
        import src.recommender.scorer as scorer
        monkeypatch.setattr(scorer, "read_table", lambda *a, **k: pd.DataFrame())
        monkeypatch.setattr(scorer, "upsert_dataframe", lambda *a, **k: None)
        monkeypatch.setattr(scorer, "load_config", lambda: _SCORING_CFG)
        assert scorer.score_all_funds({"composite_signal": "标配稳健"}).empty


# ────────────────────────────────────────────────────────────────────
# 2. build_portfolio_recommendation —— 仓位口径一致性（问题 #5 回归）
# ────────────────────────────────────────────────────────────────────

class TestBuildPortfolioReal:
    def _fund_scores_df(self):
        return pd.DataFrame([
            {"fund_code": "513100", "fund_name": "纳斯达克100ETF(华夏)", "total_score": 82.0,
             "signal": "买入", "performance_score": 8.0, "risk_score": 7.5,
             "strategy_score": 7.0, "consistency_score": 6.5, "cost_score": 9.0},
            {"fund_code": "513500", "fund_name": "标普500ETF(南方)", "total_score": 78.0,
             "signal": "买入", "performance_score": 7.5, "risk_score": 7.8,
             "strategy_score": 7.2, "consistency_score": 6.8, "cost_score": 9.0},
            {"fund_code": "164906", "fund_name": "华宝标普油气LOF", "total_score": 55.0,
             "signal": "观望", "performance_score": 4.0, "risk_score": 3.5,
             "strategy_score": 5.0, "consistency_score": 4.0, "cost_score": 8.0},
        ])

    def _patch(self, monkeypatch, tmp_path):
        import src.recommender.portfolio as portfolio

        def fake_read_table(table, where="", params=()):
            if table == "fund_scores":
                return self._fund_scores_df()
            if table == "fund_list":
                return _fund_list_df()
            return pd.DataFrame()

        monkeypatch.setattr(portfolio, "read_table", fake_read_table)
        monkeypatch.setattr(portfolio, "load_config",
                            lambda: {"rebalancing": {"score_threshold": 10},
                                     "ai_analysis": {"enabled": False}})
        monkeypatch.setattr(portfolio, "_get_latest_navs", lambda codes: {})
        monkeypatch.setattr(portfolio, "_SNAPSHOT_PATH", tmp_path / "snap.json")
        return portfolio

    def test_allocation_reflects_signal(self, monkeypatch, tmp_path):
        """问题 #5 回归：组合的核心/卫星/现金比例必须与传入 signal 的档位一致。

        这是止损覆盖能正确传导的前提——pipeline 在 build 之前覆盖 signal 仓位，
        build 必须如实采用之。
        """
        portfolio = self._patch(monkeypatch, tmp_path)
        signal = {"composite_signal": "减仓防守", "core_allocation": 0.35,
                  "satellite_allocation": 0.15, "cash_allocation": 0.50}
        result = portfolio.build_portfolio_recommendation(signal)

        assert result["core_allocation_pct"] == 35.0
        assert result["satellite_allocation_pct"] == 15.0
        assert result["cash_allocation_pct"] == 50.0
        assert result["composite_signal"] == "减仓防守"

    def test_core_weights_sum_to_core_allocation(self, monkeypatch, tmp_path):
        portfolio = self._patch(monkeypatch, tmp_path)
        signal = {"composite_signal": "标配稳健", "core_allocation": 0.60,
                  "satellite_allocation": 0.30, "cash_allocation": 0.10}
        result = portfolio.build_portfolio_recommendation(signal)

        assert len(result["core_funds"]) >= 1
        core_weight_sum = sum(f["weight"] for f in result["core_funds"])
        assert core_weight_sum == pytest.approx(60.0, abs=0.5)
        # 核心仓应是宽基（513100/513500），不应误选油气 LOF
        core_codes = {f["fund_code"] for f in result["core_funds"]}
        assert "164906" not in core_codes

    def test_empty_scores_returns_empty_portfolio(self, monkeypatch, tmp_path):
        import src.recommender.portfolio as portfolio
        monkeypatch.setattr(portfolio, "read_table", lambda *a, **k: pd.DataFrame())
        monkeypatch.setattr(portfolio, "load_config",
                            lambda: {"rebalancing": {}, "ai_analysis": {"enabled": False}})
        result = portfolio.build_portfolio_recommendation(
            {"composite_signal": "标配稳健"})
        assert result["core_funds"] == []
        assert result["satellite_funds"] == []


# ────────────────────────────────────────────────────────────────────
# 3. run_backtest —— 走向前回测主循环（真实调用）
# ────────────────────────────────────────────────────────────────────

def _synthetic_nav(codes, start="2023-01-02", end="2024-06-30", seed=42):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, end)
    rows = []
    for i, code in enumerate(codes):
        # 各基金不同的温和上行趋势 + 噪声，保证净值有区分度
        drift = 0.0003 + i * 0.0001
        steps = rng.normal(drift, 0.01, len(dates))
        nav = 1.0 * np.exp(np.cumsum(steps))
        for d, v in zip(dates, nav):
            rows.append({"fund_code": code, "date": d.strftime("%Y-%m-%d"),
                         "nav": round(float(v), 4)})
    return pd.DataFrame(rows)


def _synthetic_market(start="2023-01-02", end="2024-06-30"):
    dates = pd.bdate_range(start, end)
    rows = []
    for d in dates:
        rows.append({"symbol": "^VIX", "date": d.strftime("%Y-%m-%d"), "close": 18.0})
    return pd.DataFrame(rows)


def _synthetic_sp500(start="2023-01-02", end="2024-06-30", seed=7):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, end)
    steps = rng.normal(0.0004, 0.009, len(dates))
    vals = 4000.0 * np.exp(np.cumsum(steps))
    return pd.Series(vals, index=pd.to_datetime(dates), name="close")


class TestRunBacktestReal:
    def _patch(self, monkeypatch):
        import src.backtester.engine as engine

        codes = ["513100", "513500", "164906"]
        nav_df = _synthetic_nav(codes)
        market_df = _synthetic_market()
        fund_list_df = _fund_list_df()  # 无 inception_date 列 → 跳过幸存者修正分支

        def fake_read_table(table, where="", params=()):
            if table == "fund_nav_history":
                return nav_df.copy()
            if table == "market_data":
                return market_df.copy()
            if table == "macro_data":
                return pd.DataFrame(columns=["series_id", "date", "value"])
            if table == "fund_list":
                return fund_list_df.copy()
            if table == "global_macro":
                return pd.DataFrame(columns=["region", "indicator", "date", "value"])
            if table == "valuation_data":
                return pd.DataFrame(columns=["metric", "date", "value"])
            return pd.DataFrame()

        monkeypatch.setattr(engine, "read_table", fake_read_table)
        monkeypatch.setattr(engine, "load_config", lambda: dict(_SCORING_CFG))
        monkeypatch.setattr(engine, "_fetch_sp500_full", lambda market_db: _synthetic_sp500())
        return engine

    def test_backtest_produces_valid_result(self, monkeypatch):
        engine = self._patch(monkeypatch)
        result = engine.run_backtest()

        assert "error" not in result, f"回测不应报错: {result.get('error')}"
        assert result["n_periods"] >= 4
        # 关键累计净值序列存在
        for col in ("strat_cum", "sp500_cum", "b6040_cum", "ewbh_cum"):
            assert col in result["df"].columns
        # 每期信号都是已知档位
        assert set(result["df"]["signal"]).issubset(
            {"重仓进取", "标配稳健", "谨慎防守", "减仓防守"})

    def test_strat_metrics_are_finite(self, monkeypatch):
        engine = self._patch(monkeypatch)
        result = engine.run_backtest()
        m = result["strat_metrics"]
        for k in ("annualized_return", "sharpe_ratio", "max_drawdown", "volatility"):
            assert np.isfinite(m[k]), f"{k} 应为有限值，得到 {m[k]}"

    def test_too_short_range_returns_error(self, monkeypatch):
        engine = self._patch(monkeypatch)
        result = engine.run_backtest(start_date="2024-05-01", end_date="2024-06-01")
        assert "error" in result


# ────────────────────────────────────────────────────────────────────
# 4. calc_metrics —— 真实纯函数（替代 test_backtester_basics 的公式副本）
# ────────────────────────────────────────────────────────────────────

class TestCalcMetricsReal:
    def test_positive_series_positive_return(self):
        from src.backtester.engine import calc_metrics
        rets = pd.Series([0.02, 0.01, 0.03, -0.01, 0.02, 0.01])
        m = calc_metrics(rets, "测试")
        assert m["total_return"] > 0
        assert m["n_months"] == 6
        assert 0 <= m["win_rate"] <= 100

    def test_short_series_returns_zeros(self):
        from src.backtester.engine import calc_metrics
        m = calc_metrics(pd.Series([0.01, 0.02]), "短")
        assert m["annualized_return"] == 0
        assert m["sharpe_ratio"] == 0

    def test_max_drawdown_non_positive(self):
        from src.backtester.engine import calc_metrics
        rets = pd.Series([0.05, -0.10, 0.03, -0.08, 0.04, 0.02])
        m = calc_metrics(rets, "回撤")
        assert m["max_drawdown"] <= 0
