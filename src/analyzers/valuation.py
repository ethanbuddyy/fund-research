"""市场估值分析：CAPE、巴菲特指标、股权风险溢价

优先使用 valuation_collector 抓取的真实 Shiller CAPE / 标普500 P/E（来源 multpl）。
真实数据不可用时，回退到基于点位的近似估算，并把 valuation_source 标记为
'estimated'，让上层和用户清楚知道这是近似值而非真实估值。
"""
import pandas as pd
import numpy as np
from ..utils.database import read_table
from ..utils.config import load_config


def calculate_valuation_metrics() -> dict:
    sp500_df = read_table("market_data", "symbol = ? ORDER BY date", ("^GSPC",))
    treasury_df = read_table("macro_data", "series_id = ? ORDER BY date DESC LIMIT 12", ("GS10",))
    gdp_df = read_table("macro_data", "series_id = ? ORDER BY date DESC LIMIT 8", ("GDPC1",))

    # ① 真实 CAPE / PE 优先
    real_cape, cape_series = _read_real_metric("cape")
    real_pe, _ = _read_real_metric("sp500_pe")

    if real_cape is not None:
        cape = real_cape
        cape_source = "real"
    else:
        cape = _estimate_cape(sp500_df)
        cape_source = "estimated"

    if real_pe is not None:
        sp500_pe = real_pe
        pe_source = "real"
    else:
        sp500_pe = _estimate_pe(sp500_df)
        pe_source = "estimated"

    nominal_gdp_df = read_table("macro_data", "series_id = ? ORDER BY date DESC LIMIT 8", ("GDP",))
    equity_cap_df  = read_table("macro_data", "series_id = ? ORDER BY date DESC LIMIT 8", ("NCBEILQ027S",))
    buffett_indicator, buffett_source = _calc_buffett_indicator(equity_cap_df, nominal_gdp_df, sp500_df)
    treasury_yield = _get_latest_value(treasury_df) or 4.5
    equity_risk_premium = max(0, (1 / sp500_pe * 100) - treasury_yield) if sp500_pe > 0 else 0

    valuation_score, valuation_level, cape_pct = _cape_score(cape, cape_series, sp500_df, cape_source)

    # 真实/近似的综合标记
    if cape_source == "real" and pe_source == "real":
        valuation_source = "real"
    elif cape_source == "real" or pe_source == "real":
        valuation_source = "partial"
    else:
        valuation_source = "estimated"

    return {
        "cape": round(cape, 2),
        "cape_source": cape_source,
        "cape_percentile": cape_pct,
        "sp500_pe": round(sp500_pe, 2),
        "pe_source": pe_source,
        "buffett_indicator": round(buffett_indicator, 3),
        "buffett_source": buffett_source,
        "treasury_10y": round(treasury_yield, 2),
        "equity_risk_premium": round(equity_risk_premium, 2),
        "valuation_level": valuation_level,
        "valuation_score": valuation_score,
        "valuation_source": valuation_source,
    }


def _read_real_metric(metric: str):
    """读取真实估值序列，返回 (最新值 or None, 升序DataFrame)。"""
    try:
        df = read_table("valuation_data", "metric = ? ORDER BY date", (metric,))
    except Exception:
        return None, pd.DataFrame()
    if df is None or df.empty:
        return None, pd.DataFrame()
    latest = float(df.iloc[-1]["value"])
    return latest, df


def _cape_score(cape: float, cape_series: pd.DataFrame, sp500_df: pd.DataFrame,
                cape_source: str) -> tuple[int, str, float]:
    """
    CAPE 分位数评分。
    - 若有真实 CAPE 历史序列（≥60个月），用真实历史分位数（非循环）。
    - 否则用固定参考分布（1990后 Shiller 实际分位：p25=21,p50=26,p75=31,p90=36）。
    返回 (score, level, percentile)。
    """
    P25, P50, P75, P90 = 21.0, 26.0, 31.0, 36.0

    if cape_source == "real" and cape_series is not None and len(cape_series) >= 60:
        # 用真实 CAPE 历史计算分位点（彻底消除“用价格反推CAPE”的循环论证）
        vals = cape_series["value"].astype(float).values
        P25, P50, P75, P90 = np.percentile(vals, [25, 50, 75, 90])
        pct = float((vals < cape).mean() * 100)  # 当前CAPE在真实历史中的百分位
    else:
        pct = float(np.clip((cape - P25) / (P90 - P25) * 65 + 25, 5, 99))

    if cape >= P90:
        return 1, "极度高估", round(pct, 1)
    elif cape >= P75:
        return 3, "高估", round(pct, 1)
    elif cape >= P50:
        return 5, "偏高", round(pct, 1)
    elif cape >= P25:
        return 7, "合理", round(pct, 1)
    else:
        return 9, "低估", round(pct, 1)


def _estimate_cape(sp500_df: pd.DataFrame) -> float:
    """近似Shiller CAPE（仅在真实数据不可用时使用，会被标记为 estimated）。
    校准基准：S&P500 5000点 ≈ CAPE 30（2024年实际水平）。
    """
    if sp500_df.empty:
        return 30.0
    sp500_df = sp500_df.sort_values("date").dropna(subset=["close"])
    if sp500_df.empty:
        return 30.0
    current = float(sp500_df.iloc[-1]["close"])
    cape = 30.0 + (current - 5000) / 1000 * 3.0
    return round(min(max(cape, 12), 50), 1)


def _estimate_pe(sp500_df: pd.DataFrame) -> float:
    """近似标普500 P/E（仅在真实数据不可用时使用）。"""
    if sp500_df.empty:
        return 22.0
    sp500_df = sp500_df.sort_values("date").dropna(subset=["close"])
    if sp500_df.empty:
        return 22.0
    current = float(sp500_df.iloc[-1]["close"])
    pe = 22.0 + (current - 5000) / 1000 * 2.0
    return round(min(max(pe, 10), 40), 1)


def _calc_buffett_indicator(equity_cap_df: pd.DataFrame, nominal_gdp_df: pd.DataFrame,
                            sp500_df: pd.DataFrame) -> tuple[float, str]:
    """巴菲特指标：美股权益总市值 / 名义GDP。
    优先使用 FRED 真实数据（NCBEILQ027S / GDP，均为十亿美元）；
    两个序列均可用时返回 ('real', value)，否则退回点位近似。
    """
    if not equity_cap_df.empty and not nominal_gdp_df.empty:
        equity_cap_df  = equity_cap_df.sort_values("date").dropna(subset=["value"])
        nominal_gdp_df = nominal_gdp_df.sort_values("date").dropna(subset=["value"])
        if not equity_cap_df.empty and not nominal_gdp_df.empty:
            equity_val = float(equity_cap_df.iloc[-1]["value"])   # 十亿美元
            gdp_val    = float(nominal_gdp_df.iloc[-1]["value"])  # 十亿美元，SAAR已年化
            if gdp_val > 0 and equity_val > 0:
                return round(equity_val / gdp_val, 3), "real"

    # 回退：基于标普500点位的近似估算
    if sp500_df.empty:
        return 1.85, "estimated"
    sp500_df  = sp500_df.sort_values("date")
    sp500_level = float(sp500_df.iloc[-1]["close"])
    bi = 1.85 + (sp500_level - 5000) / 1000 * 0.18
    return round(min(max(bi, 0.5), 4.0), 2), "estimated"


def _get_latest_value(df: pd.DataFrame) -> float | None:
    if df.empty:
        return None
    df = df.sort_values("date")
    val = df.iloc[-1]["value"]
    return float(val) if pd.notna(val) else None
