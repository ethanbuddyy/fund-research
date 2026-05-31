"""宏观经济周期分析"""
import pandas as pd
import numpy as np
from ..utils.database import read_table


def analyze_macro_cycle() -> dict:
    """判断当前经济周期阶段并返回分析结果"""
    gdp_df = read_table("macro_data", "series_id = ? ORDER BY date DESC LIMIT 5", ("GDPC1",))
    cpi_df = read_table("macro_data", "series_id = ? ORDER BY date DESC LIMIT 12", ("CPIAUCSL",))
    rate_df = read_table("macro_data", "series_id = ? ORDER BY date DESC LIMIT 12", ("FEDFUNDS",))
    unemploy_df = read_table("macro_data", "series_id = ? ORDER BY date DESC LIMIT 12", ("UNRATE",))
    treasury_10y = read_table("macro_data", "series_id = ? ORDER BY date DESC LIMIT 12", ("GS10",))
    treasury_2y = read_table("macro_data", "series_id = ? ORDER BY date DESC LIMIT 12", ("GS2",))

    gdp_growth = _calc_yoy(gdp_df)
    inflation = _calc_yoy(cpi_df)
    fed_rate = _get_latest(rate_df)
    unemployment = _get_latest(unemploy_df)
    yield_10y = _get_latest(treasury_10y)
    yield_2y = _get_latest(treasury_2y)
    yield_curve = (yield_10y or 4.2) - (yield_2y or 4.8)  # 正值=正常，负值=倒挂

    # 经济周期判断（四阶段模型）
    cycle = _determine_cycle(gdp_growth, inflation, fed_rate, unemployment, yield_curve)

    # 美联储方向信号（降息方向加分，加息方向减分；仅看6个月变化，不看绝对水平）
    fed_direction_score = _fed_direction_score(rate_df)

    # 政策环境
    effective_rate = fed_rate if fed_rate is not None else 5.3
    if effective_rate > 4.0:
        policy_env = "紧缩"
        policy_note = f"联储基准利率 {effective_rate:.2f}%，处于限制性水平"
    elif effective_rate > 2.5:
        policy_env = "中性"
        policy_note = f"联储基准利率 {effective_rate:.2f}%，接近中性利率"
    else:
        policy_env = "宽松"
        policy_note = f"联储基准利率 {effective_rate:.2f}%，货币政策宽松"

    return {
        "cycle": cycle["phase"],
        "cycle_score": cycle["score"],
        "fed_direction_score": fed_direction_score,
        "cycle_description": cycle["description"],
        "gdp_growth": round(gdp_growth, 2) if gdp_growth else None,
        "inflation": round(inflation, 2) if inflation else None,
        "fed_rate": round(fed_rate, 2) if fed_rate else None,
        "unemployment": round(unemployment, 2) if unemployment else None,
        "yield_curve": round(yield_curve, 2),
        "yield_inverted": yield_curve < 0,
        "policy_env": policy_env,
        "policy_note": policy_note,
    }


def _determine_cycle(gdp_growth, inflation, fed_rate, unemployment, yield_curve) -> dict:
    g = gdp_growth if gdp_growth is not None else 2.5
    inf = inflation if inflation is not None else 3.0
    rate = fed_rate if fed_rate is not None else 5.3
    unemp = unemployment if unemployment is not None else 4.1

    # 评分体系判断周期
    if g > 2.5 and inf < 3.5 and unemp < 4.5:
        return {"phase": "扩张", "score": 8, "description": "经济稳健增长，通胀可控，就业良好，利于权益资产"}
    elif g > 1.5 and inf >= 3.5:
        return {"phase": "高峰", "score": 5, "description": "增长放缓，通胀偏高，利率压力大，需控制风险"}
    elif g < 1.5 and rate > 3.0 and yield_curve < 0:
        return {"phase": "收缩", "score": 3, "description": "经济降温，利率高企，收益率曲线倒挂，防守为主"}
    elif g < 0 or unemp > 5.5:
        return {"phase": "衰退", "score": 2, "description": "经济衰退，失业上升，逢低布局机会出现"}
    else:
        return {"phase": "复苏", "score": 6, "description": "经济温和复苏，通胀回落，货币政策趋松，机会渐现"}


def _fed_direction_score(rate_df: pd.DataFrame) -> float:
    """
    美联储利率方向评分（与绝对水平无关，只看6个月变化方向）。
    降息周期 → +1.5（流动性宽松利好权益）
    按兵不动 →  0.0
    加息周期 → -1.5（流动性收紧利空权益）
    """
    if rate_df is None or len(rate_df) < 6:
        return 0.0
    rate_df = rate_df.sort_values("date")
    current = float(rate_df.iloc[-1]["value"])
    prior   = float(rate_df.iloc[-6]["value"])
    delta   = current - prior
    if   delta < -0.25: return +1.5
    elif delta >  0.25: return -1.5
    else:               return  0.0


def _calc_yoy(df: pd.DataFrame) -> float | None:
    if df is None or len(df) < 2:
        return None
    df = df.sort_values("date")
    latest = float(df.iloc[-1]["value"]) if pd.notna(df.iloc[-1]["value"]) else None
    prior = float(df.iloc[0]["value"]) if pd.notna(df.iloc[0]["value"]) else None
    if latest and prior and prior != 0:
        return (latest / prior - 1) * 100
    return None


def _get_latest(df: pd.DataFrame) -> float | None:
    if df is None or df.empty:
        return None
    df = df.sort_values("date")
    val = df.iloc[-1]["value"]
    return float(val) if pd.notna(val) else None
