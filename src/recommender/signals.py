"""综合市场信号生成"""
import pandas as pd
import numpy as np
from datetime import datetime
from ..utils.database import upsert_dataframe, read_table
from ..analyzers.macro_analyzer import analyze_macro_cycle
from ..analyzers.valuation import calculate_valuation_metrics
from ..collectors.news_collector import get_market_sentiment
from ..utils.config import load_config
from ..analyzers.narrative import generate_narrative
from ..domain.scoring import (
    classify_signal, credit_score_from_spread, trend_score_from_deviation, apply_user_profile,
)
from ..domain.factor_config import FACTOR_WEIGHTS
from ..domain.types import MarketSignal


def _credit_score() -> float:
    df = read_table("macro_data", "series_id = ? ORDER BY date DESC LIMIT 1", ("BAMLH0A0HYM2",))
    if df.empty:
        return 5.0
    return credit_score_from_spread(float(df.iloc[0]["value"]))


def _trend_score() -> float:
    df = read_table("market_data", "symbol = ? ORDER BY date DESC LIMIT 300", ("^GSPC",))
    if len(df) < 60:
        return 5.0
    df = df.sort_values("date")
    prices = df["close"].astype(float)
    current = float(prices.iloc[-1])
    window = min(252, len(prices))
    if window < 252:
        print(f"[WARN] 趋势因子：SP500 数据仅 {window} 条（不足252），使用 {window} 日均线代替年线")
    ma = float(prices.tail(window).mean())
    return trend_score_from_deviation((current - ma) / ma)


def generate_market_signal(save: bool = True) -> MarketSignal:
    cfg = load_config()

    macro = analyze_macro_cycle()
    valuation = calculate_valuation_metrics()
    sentiment = get_market_sentiment()

    # 各维度得分
    macro_score      = macro.get("cycle_score", 5)
    fed_direction    = macro.get("fed_direction_score", 0.0)
    valuation_score  = valuation.get("valuation_score", 5)
    sentiment_score  = sentiment.get("score", 50) / 10         # 0-100 → 0-10
    contrarian       = 10 - sentiment_score                    # 逆向情绪
    trend_score      = _trend_score()                          # 价格趋势
    credit_score     = _credit_score()                         # 信用利差（独立因子）

    # 宏观分叠加利率方向修正（上限10，下限1）
    macro_adj = float(np.clip(macro_score + fed_direction, 1, 10))

    # 第6因子：全球宏观综合评分（QDII资产规模权重的跨区域加权）
    # global_macro 必须在此处赋值，供 compute_global_macro_score 和后续 signal dict 共用
    try:
        from ..analyzers.global_macro_analyzer import analyze_global_macro, compute_global_macro_score
        global_macro = analyze_global_macro()
        global_macro_score = compute_global_macro_score(global_macro)
    except Exception:
        global_macro = {"available": False, "regions": {}}
        global_macro_score = 5.0

    composite_raw = (
        macro_adj            * FACTOR_WEIGHTS["macro"]
        + valuation_score    * FACTOR_WEIGHTS["valuation"]
        + contrarian         * FACTOR_WEIGHTS["sentiment"]
        + trend_score        * FACTOR_WEIGHTS["trend"]
        + credit_score       * FACTOR_WEIGHTS["credit"]
        + global_macro_score * FACTOR_WEIGHTS["global_macro"]
    )

    composite_signal, core_alloc, satellite_alloc, cash_alloc = classify_signal(composite_raw)

    # 用户个人化调整（risk_tolerance / investment_horizon_years / 上下界）
    user_profile = cfg.get("user_profile") or {}
    user_profile_applied = False
    if user_profile:
        adj_core, adj_sat, adj_cash = apply_user_profile(
            core_alloc, satellite_alloc, cash_alloc, user_profile
        )
        if (adj_core, adj_sat, adj_cash) != (core_alloc, satellite_alloc, cash_alloc):
            print(
                f"[用户偏好] {user_profile.get('risk_tolerance','moderate')} / "
                f"{user_profile.get('investment_horizon_years',10)}年 → "
                f"核心 {core_alloc*100:.0f}%→{adj_core*100:.0f}%  "
                f"卫星 {satellite_alloc*100:.0f}%→{adj_sat*100:.0f}%  "
                f"现金 {cash_alloc*100:.0f}%→{adj_cash*100:.0f}%"
            )
        core_alloc, satellite_alloc, cash_alloc = adj_core, adj_sat, adj_cash
        user_profile_applied = True

    _signal_colors = {"重仓进取": "green", "标配稳健": "blue", "谨慎防守": "orange", "减仓防守": "red"}
    signal_color = _signal_colors[composite_signal]

    fund_list_data = _get_fund_list()
    narrative = generate_narrative(valuation, sentiment, fund_list_data, cfg)

    from ..utils import provenance
    data_source = provenance.overall_mode()

    signal: MarketSignal = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "data_source": data_source,                 # real / partial / mock
        "data_quality": provenance.read_all(),
        "macro_cycle": macro.get("cycle"),
        "valuation_level": valuation.get("valuation_level"),
        "sentiment_label": sentiment.get("label"),
        "composite_signal": composite_signal,
        "signal_color": signal_color,
        "cape": valuation.get("cape"),
        "sp500_pe": valuation.get("sp500_pe"),
        "vix": sentiment.get("vix"),
        "buffett_indicator": valuation.get("buffett_indicator"),
        "equity_risk_premium": valuation.get("equity_risk_premium"),
        "core_allocation": core_alloc,
        "satellite_allocation": satellite_alloc,
        "cash_allocation": cash_alloc,
        "user_profile_applied": user_profile_applied,
        "user_profile": {
            k: user_profile.get(k)
            for k in ("risk_tolerance", "investment_horizon_years", "max_equity_pct", "min_cash_pct")
        } if user_profile_applied else None,
        "timing_score": composite_raw,
        "trend_score": round(trend_score, 2),
        "credit_score": round(credit_score, 2),
        "global_macro_score": round(global_macro_score, 2),
        "fed_direction": fed_direction,
        "macro_adj": round(macro_adj, 2),
        "macro": macro,
        "global_macro": global_macro,
        "valuation": valuation,
        "sentiment": sentiment,
        "narrative": narrative,
    }

    # ── AI 阶段一：市场上下文分析（配置开关控制）──────────
    ai_analysis = None
    cfg_ai = cfg.get("ai_analysis", {})
    if cfg_ai.get("enabled", False):
        skip_mock = cfg_ai.get("skip_on_mock_data", True)
        if not (skip_mock and data_source == "mock"):
            try:
                from ..ai.phase1_market_analyzer import MarketContextAnalyzer
                ai_analysis = MarketContextAnalyzer().analyze(signal)
                if ai_analysis:
                    signal["narrative"] = {
                        "insights": [ai_analysis.get("market_narrative", "")],
                        "ai_enhanced": True,
                        "source": "claude_phase1",
                    }
            except Exception as e:
                print(f"[AI Phase1] 跳过: {e}")
    signal["ai_analysis"] = ai_analysis

    if save:
        _save_signal(signal)
    return signal


def _get_fund_list() -> list:
    from ..utils.database import read_table
    df = read_table("fund_list")
    if df.empty:
        return []
    return df.to_dict("records")


def _save_signal(signal: dict):
    row = {
        "date": signal["date"],
        "macro_cycle": signal["macro_cycle"],
        "valuation_level": signal["valuation_level"],
        "sentiment": signal.get("sentiment_label", ""),
        "composite_signal": signal["composite_signal"],
        "cape": signal.get("cape"),
        "sp500_pe": signal.get("sp500_pe"),
        "vix": signal.get("vix"),
        "buffett_indicator": signal.get("buffett_indicator"),
        "equity_risk_premium": signal.get("equity_risk_premium"),
        "core_allocation": signal["core_allocation"],
        "satellite_allocation": signal["satellite_allocation"],
        "cash_allocation": signal["cash_allocation"],
        "notes": f"data_source={signal.get('data_source', 'unknown')}",
    }
    df = pd.DataFrame([row])
    upsert_dataframe(df, "market_signals", ["date"])
