"""基金综合评分引擎"""
import pandas as pd
import numpy as np
from ..utils.database import read_table, upsert_dataframe
from ..utils.config import load_config


def score_all_funds(market_signal: dict) -> pd.DataFrame:
    cfg = load_config()
    weights = cfg.get("scoring_weights", {})
    w_perf = weights.get("performance", 0.25)
    w_risk = weights.get("risk_adjusted", 0.20)
    w_strategy = weights.get("strategy_match", 0.20)
    w_timing = weights.get("market_timing", 0.20)
    w_cost = weights.get("cost_efficiency", 0.15)

    funds = read_table("fund_list")
    perf_df = read_table("fund_performance")

    if funds.empty:
        return pd.DataFrame()

    merged = funds.merge(perf_df, on="fund_code", how="left") if not perf_df.empty else funds.copy()

    results = []
    for _, row in merged.iterrows():
        code = str(row["fund_code"])
        name = str(row.get("fund_name", code))

        perf_score = _calc_performance_score(row)
        risk_score = _calc_risk_score(row)
        strategy_score = _calc_strategy_score(row, market_signal)
        timing_score = market_signal.get("timing_score", 5)
        cost_score = _calc_cost_score(row, cfg)

        total = (
            perf_score * w_perf
            + risk_score * w_risk
            + strategy_score * w_strategy
            + timing_score * w_timing
            + cost_score * w_cost
        ) * 10  # 转为百分制

        signal, recommendation = _generate_signal(total, market_signal)

        results.append({
            "fund_code": code,
            "fund_name": name,
            "total_score": round(total, 1),
            "performance_score": round(perf_score * 10, 1),
            "risk_score": round(risk_score * 10, 1),
            "strategy_score": round(strategy_score * 10, 1),
            "timing_score": round(timing_score * 10, 1),
            "cost_score": round(cost_score * 10, 1),
            "signal": signal,
            "recommendation": recommendation,
        })

    if results:
        df = pd.DataFrame(results).sort_values("total_score", ascending=False)
        upsert_dataframe(df, "fund_scores", ["fund_code"])
        return df
    return pd.DataFrame()


def _calc_performance_score(row) -> float:
    weighted_sum = 0.0
    total_weight = 0.0
    for col, weight in [("return_1y", 0.4), ("return_3y", 0.35), ("return_6m", 0.25)]:
        val = row.get(col)
        if pd.notna(val) and val is not None:
            target = 20 if "1y" in col or "3y" in col else 10
            s = min(10, max(0, (float(val) / target) * 10))
            weighted_sum += s * weight
            total_weight += weight
    return weighted_sum / total_weight if total_weight > 0 else 5.0


def _calc_risk_score(row) -> float:
    sharpe = float(row.get("sharpe_ratio") or 0)
    max_dd = float(row.get("max_drawdown") or -20)
    vol = float(row.get("volatility") or 20)

    # 夏普比率：1.5以上满分
    sharpe_s = min(10, max(0, sharpe / 1.5 * 10))
    # 最大回撤：回撤越小越好，-10%满分，-50%得0分
    dd_s = min(10, max(0, (1 - abs(max_dd) / 50) * 10))
    # 波动率：15%以下满分
    vol_s = min(10, max(0, (1 - vol / 40) * 10))

    return sharpe_s * 0.4 + dd_s * 0.35 + vol_s * 0.25


def _calc_strategy_score(row, market_signal: dict) -> float:
    fund_type = str(row.get("fund_type", ""))
    fund_name = str(row.get("fund_name", ""))
    composite = market_signal.get("composite_signal", "标配稳健")

    # 在进取信号下，成长型ETF加分
    is_growth = any(kw in fund_name for kw in ["纳斯达克", "科技", "100"])
    is_index = any(kw in fund_type for kw in ["ETF", "指数"]) or "500" in fund_name
    is_active = "主动" in fund_type

    if composite == "重仓进取":
        if is_growth:
            return 9.0
        elif is_index:
            return 7.5
        else:
            return 6.0
    elif composite == "标配稳健":
        if is_index:
            return 8.0
        elif is_growth:
            return 7.0
        else:
            return 6.5
    else:  # 减仓防守
        if is_index:
            return 7.0
        elif is_active:
            return 5.0
        else:
            return 6.0


def _calc_cost_score(row, cfg: dict) -> float:
    er = float(row.get("expense_ratio") or 0.012)
    params = cfg.get("strategy_params", {}).get("bogle", {})
    pref = params.get("preferred_expense_ratio", 0.005)
    max_er = params.get("max_expense_ratio", 0.015)

    if er <= pref:
        return 10.0
    elif er <= max_er:
        return 10 - (er - pref) / (max_er - pref) * 5
    else:
        return max(0, 5 - (er - max_er) * 100)


def _generate_signal(score: float, market_signal: dict) -> tuple[str, str]:
    composite = market_signal.get("composite_signal", "标配稳健")

    if score >= 75:
        if composite == "减仓防守":
            return "持有", "高质量基金，当前市场建议标配持有"
        return "买入", f"综合评分优秀（{score:.0f}分），建议积极配置"
    elif score >= 60:
        if composite == "重仓进取":
            return "增持", f"评分良好（{score:.0f}分），进取市场环境下适当加仓"
        return "持有", f"评分良好（{score:.0f}分），维持标配仓位"
    elif score >= 45:
        return "观望", f"评分一般（{score:.0f}分），等待更好时机"
    else:
        return "回避", f"评分偏低（{score:.0f}分），建议暂不配置"
