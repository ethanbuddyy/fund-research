"""综合市场信号生成"""
import pandas as pd
import numpy as np
from datetime import datetime
from ..utils.database import upsert_dataframe, read_table
from ..analyzers.macro_analyzer import analyze_macro_cycle
from ..analyzers.valuation import calculate_valuation_metrics
from ..collectors.news_collector import get_market_sentiment
from ..utils.config import load_config
from ..analyzers.masters import graham, buffett, bogle, siegel, lynch


def _trend_score() -> float:
    """
    SP500 价格趋势评分：当前价格 vs 12个月（252日）移动均线。
    强劲上升趋势 → 8（动量正向，持有合理）
    温和上升趋势 → 6
    温和下降趋势 → 4
    强劲下降趋势 → 2（趋势反转，防守优先）
    """
    df = read_table("market_data", "symbol = ? ORDER BY date DESC LIMIT 300", ("^GSPC",))
    if len(df) < 60:
        return 5.0  # 数据不足时中性
    df = df.sort_values("date")
    prices = df["close"].astype(float)
    current = float(prices.iloc[-1])
    ma252 = float(prices.tail(252).mean())
    deviation = (current - ma252) / ma252  # 偏离幅度

    if   deviation >  0.08: return 8.0   # 显著高于年线：趋势强劲
    elif deviation >  0.02: return 6.5   # 略高于年线：趋势温和
    elif deviation > -0.02: return 5.0   # 贴近年线：中性
    elif deviation > -0.08: return 3.5   # 略低于年线：趋势偏弱
    else:                   return 2.0   # 显著低于年线：趋势反转


def generate_market_signal(save: bool = True) -> dict:
    cfg = load_config()

    macro = analyze_macro_cycle()
    valuation = calculate_valuation_metrics()
    sentiment = get_market_sentiment()

    # 各维度得分
    macro_score      = macro.get("cycle_score", 5)
    fed_direction    = macro.get("fed_direction_score", 0.0)   # 新增：利率方向
    valuation_score  = valuation.get("valuation_score", 5)
    sentiment_score  = sentiment.get("score", 50) / 10         # 0-100 → 0-10
    contrarian       = 10 - sentiment_score                    # 逆向情绪
    trend_score      = _trend_score()                          # 新增：价格趋势

    # 宏观分叠加利率方向修正（上限10，下限1）
    macro_adj = float(np.clip(macro_score + fed_direction, 1, 10))

    # 新权重：宏观20% + 估值25% + 逆向情绪20% + 趋势35%
    composite_raw = (
        macro_adj       * 0.20
        + valuation_score * 0.25
        + contrarian      * 0.20
        + trend_score     * 0.35
    )

    # 综合信号（阈值基于新权重重新校准：分数中枢约5.0）
    if composite_raw >= 7.0:
        composite_signal = "重仓进取"
        core_alloc = 0.70
        satellite_alloc = 0.25
        cash_alloc = 0.05
        signal_color = "green"
    elif composite_raw >= 5.0:
        composite_signal = "标配稳健"
        core_alloc = 0.60
        satellite_alloc = 0.30
        cash_alloc = 0.10
        signal_color = "blue"
    elif composite_raw >= 3.0:
        composite_signal = "谨慎防守"
        core_alloc = 0.50
        satellite_alloc = 0.20
        cash_alloc = 0.30
        signal_color = "orange"
    else:
        composite_signal = "减仓防守"
        core_alloc = 0.35
        satellite_alloc = 0.15
        cash_alloc = 0.50
        signal_color = "red"

    # 大师共识分析
    fund_list_data = _get_fund_list()
    graham_analysis = graham.analyze(valuation, cfg)
    buffett_analysis = buffett.analyze(valuation, sentiment, cfg)
    bogle_analysis = bogle.analyze(fund_list_data, cfg)
    lynch_analysis = lynch.analyze(cfg)
    siegel_analysis = siegel.analyze(valuation, cfg)

    master_avg_score = (
        graham_analysis["score"] + buffett_analysis["score"]
        + bogle_analysis["score"] + lynch_analysis["score"]
        + siegel_analysis["score"]
    ) / 5

    signal = {
        "date": datetime.now().strftime("%Y-%m-%d"),
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
        "timing_score": composite_raw,
        "trend_score": round(trend_score, 2),
        "fed_direction": fed_direction,
        "macro_adj": round(macro_adj, 2),
        "macro": macro,
        "valuation": valuation,
        "sentiment": sentiment,
        "masters": {
            "graham": graham_analysis,
            "buffett": buffett_analysis,
            "bogle": bogle_analysis,
            "lynch": lynch_analysis,
            "siegel": siegel_analysis,
            "avg_score": round(master_avg_score, 2),
        },
    }

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
    }
    df = pd.DataFrame([row])
    upsert_dataframe(df, "market_signals", ["date"])
