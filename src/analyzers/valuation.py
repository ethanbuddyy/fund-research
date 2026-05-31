"""市场估值分析：CAPE、巴菲特指标、股权风险溢价"""
import pandas as pd
import numpy as np
from ..utils.database import read_table
from ..utils.config import load_config


def calculate_valuation_metrics() -> dict:
    # 读取全量SP500历史（用于分位数估算）
    sp500_df = read_table("market_data", "symbol = ? ORDER BY date", ("^GSPC",))
    treasury_df = read_table("macro_data", "series_id = ? ORDER BY date DESC LIMIT 12", ("GS10",))
    gdp_df = read_table("macro_data", "series_id = ? ORDER BY date DESC LIMIT 8", ("GDPC1",))

    cape = _estimate_cape(sp500_df)
    sp500_pe = _estimate_pe(sp500_df)
    buffett_indicator = _estimate_buffett_indicator(sp500_df, gdp_df)
    treasury_yield = _get_latest_value(treasury_df) or 4.5
    equity_risk_premium = max(0, (1 / sp500_pe * 100) - treasury_yield) if sp500_pe > 0 else 0

    valuation_score, valuation_level, cape_pct = _relative_cape_score(cape, sp500_df)

    return {
        "cape": round(cape, 2),
        "cape_percentile": cape_pct,
        "sp500_pe": round(sp500_pe, 2),
        "buffett_indicator": round(buffett_indicator, 3),
        "treasury_10y": round(treasury_yield, 2),
        "equity_risk_premium": round(equity_risk_premium, 2),
        "valuation_level": valuation_level,
        "valuation_score": valuation_score,
    }


def _relative_cape_score(cape: float, sp500_df: pd.DataFrame) -> tuple[int, str, float]:
    """
    基于1990年后Shiller CAPE实际分布的分位数评分。
    固定参考分布：p25=21, p50=26, p75=31, p90=36（更贴近当代市场现实）。
    若 DB 中有足够历史数据则用滚动分位数修正。
    返回 (score, level, percentile)
    """
    # 固定历史参考分位数（基于1990-2024 Shiller CAPE实际数据）
    P25, P50, P75, P90 = 21.0, 26.0, 31.0, 36.0

    # 若本地有足量数据（≥120条，约6个月日线），用滚动分位数微调参考点
    if len(sp500_df) >= 120:
        sp500_df = sp500_df.sort_values("date")
        hist_capes = np.clip(
            30.0 + (sp500_df["close"].astype(float).values - 5000) / 1000 * 3.0, 12, 50
        )
        # 与固定参考均值混合（权重各50%），防止短期数据过拟合
        rp25, rp50, rp75, rp90 = np.percentile(hist_capes, [25, 50, 75, 90])
        P25 = (P25 + rp25) / 2
        P50 = (P50 + rp50) / 2
        P75 = (P75 + rp75) / 2
        P90 = (P90 + rp90) / 2

    # 计算大致百分位（用于展示）
    pct = float(np.clip((cape - P25) / (P90 - P25) * 65 + 25, 5, 99))

    if cape >= P90:
        return 1, "极度高估", pct
    elif cape >= P75:
        return 3, "高估",     pct
    elif cape >= P50:
        return 5, "偏高",     pct
    elif cape >= P25:
        return 7, "合理",     pct
    else:
        return 9, "低估",     pct


def _estimate_cape(sp500_df: pd.DataFrame) -> float:
    """近似Shiller CAPE：基于标普500当前点位校准估算
    校准基准：S&P500 5000点对应CAPE约30（2024年实际水平）
    """
    if sp500_df.empty:
        return 30.0
    sp500_df = sp500_df.sort_values("date")
    current = float(sp500_df.iloc[-1]["close"])
    # 线性校准：5000点≈CAPE30，每偏离1000点CAPE变动约3
    cape = 30.0 + (current - 5000) / 1000 * 3.0
    return round(min(max(cape, 12), 50), 1)


def _estimate_pe(sp500_df: pd.DataFrame) -> float:
    """估算标普500 P/E：基于当前点位校准
    校准基准：S&P500 5000点对应P/E约22（2024年实际水平）
    """
    if sp500_df.empty:
        return 22.0
    sp500_df = sp500_df.sort_values("date")
    current = float(sp500_df.iloc[-1]["close"])
    pe = 22.0 + (current - 5000) / 1000 * 2.0
    return round(min(max(pe, 10), 40), 1)


def _estimate_buffett_indicator(sp500_df: pd.DataFrame, gdp_df: pd.DataFrame) -> float:
    """美股总市值/GDP校准估算
    校准基准：S&P500 5000点对应巴菲特指标约1.85（2024年实际水平）
    系数：每偏离1000点，指标变动约0.18
    """
    if sp500_df.empty:
        return 1.85
    sp500_df = sp500_df.sort_values("date")
    sp500_level = float(sp500_df.iloc[-1]["close"])
    bi = 1.85 + (sp500_level - 5000) / 1000 * 0.18
    return round(min(max(bi, 0.5), 4.0), 2)


def _get_latest_value(df: pd.DataFrame) -> float | None:
    if df.empty:
        return None
    df = df.sort_values("date")
    val = df.iloc[-1]["value"]
    return float(val) if pd.notna(val) else None
