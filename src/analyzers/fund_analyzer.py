"""基金绩效分析：收益率、夏普比率、最大回撤"""
import pandas as pd
import numpy as np
from ..utils.database import read_table, upsert_dataframe


def analyze_all_funds() -> pd.DataFrame:
    funds = read_table("fund_list")
    if funds.empty:
        return pd.DataFrame()

    results = []
    for _, fund in funds.iterrows():
        code = str(fund["fund_code"])
        perf = _calc_performance(code)
        if perf:
            results.append(perf)

    if results:
        df = pd.DataFrame(results)
        upsert_dataframe(df, "fund_performance", ["fund_code"])
        print(f"[OK] 基金绩效分析完成: {len(df)} 只")
        return df
    return pd.DataFrame()


def _calc_performance(fund_code: str) -> dict | None:
    nav_df = read_table("fund_nav_history", "fund_code = ? ORDER BY date", (fund_code,))
    if len(nav_df) < 20:
        return None

    nav_df = nav_df.sort_values("date")
    nav_df["date"] = pd.to_datetime(nav_df["date"])
    nav_series = nav_df.set_index("date")["nav"].astype(float)

    today = nav_series.index[-1]

    def period_return(days):
        cutoff = today - pd.Timedelta(days=days)
        sub = nav_series[nav_series.index >= cutoff]
        if len(sub) < 2:
            return None
        return (float(sub.iloc[-1]) / float(sub.iloc[0]) - 1) * 100

    # 年化收益
    total_days = (nav_series.index[-1] - nav_series.index[0]).days
    total_return = (float(nav_series.iloc[-1]) / float(nav_series.iloc[0]) - 1)
    annualized = ((1 + total_return) ** (365 / max(total_days, 1)) - 1) * 100 if total_days > 30 else None

    # 日收益率序列
    daily_rets = nav_series.pct_change().dropna()

    # 夏普比率（假设无风险利率2%）
    rf_daily = 0.02 / 252
    excess = daily_rets - rf_daily
    sharpe = (excess.mean() / excess.std() * np.sqrt(252)) if excess.std() > 0 else 0

    # 最大回撤
    rolling_max = nav_series.cummax()
    drawdown = (nav_series - rolling_max) / rolling_max
    max_drawdown = float(drawdown.min()) * 100

    # 波动率
    volatility = float(daily_rets.std() * np.sqrt(252)) * 100

    return {
        "fund_code": fund_code,
        "return_1m": period_return(30),
        "return_3m": period_return(90),
        "return_6m": period_return(180),
        "return_1y": period_return(365),
        "return_3y": period_return(365 * 3),
        "return_5y": period_return(365 * 5),
        "annualized_return": round(annualized, 2) if annualized else None,
        "sharpe_ratio": round(sharpe, 3),
        "max_drawdown": round(max_drawdown, 2),
        "volatility": round(volatility, 2),
    }
