"""基金绩效分析：收益率、夏普比率、最大回撤"""
import pandas as pd
import numpy as np
from ..utils.database import read_table, upsert_dataframe, get_connection


def _get_rf_rate() -> float:
    """读取 DB 最新联储基准利率（年化小数），不可用时退回 0.04（4%，当前利率环境参考值）。"""
    try:
        df = read_table("macro_data", "series_id = ? ORDER BY date DESC LIMIT 1", ("FEDFUNDS",))
        if not df.empty:
            return float(df.iloc[0]["value"]) / 100.0
    except Exception:
        pass
    return 0.04


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
        # 逐年收益单独存入 fund_turnover 表（按年份行存储）
        _save_year_returns(results)

        # fund_performance 只存标量字段
        scalar_cols = ["fund_code", "return_1m", "return_3m", "return_6m",
                       "return_1y", "return_3y", "return_5y",
                       "annualized_return", "sharpe_ratio", "max_drawdown", "volatility"]
        perf_rows = [{k: r[k] for k in scalar_cols if k in r} for r in results]
        df = pd.DataFrame(perf_rows)
        upsert_dataframe(df, "fund_performance", ["fund_code"])
        print(f"[OK] 基金绩效分析完成: {len(df)} 只")
        return df
    return pd.DataFrame()


def _save_year_returns(results: list):
    """将逐年收益写入 fund_year_returns 表。"""
    _ensure_year_returns_table()
    conn = get_connection()
    try:
        for r in results:
            code = r.get("fund_code", "")
            yr_map = r.get("year_returns") or {}
            for year_str, ret in yr_map.items():
                conn.execute(
                    """INSERT INTO fund_year_returns (fund_code, year, return_pct)
                       VALUES (?, ?, ?)
                       ON CONFLICT(fund_code, year) DO UPDATE SET return_pct=excluded.return_pct""",
                    (code, int(year_str), ret),
                )
        conn.commit()
    finally:
        conn.close()


def _ensure_year_returns_table():
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fund_year_returns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fund_code TEXT NOT NULL,
                year INTEGER NOT NULL,
                return_pct REAL,
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(fund_code, year)
            )
        """)
        conn.commit()
    finally:
        conn.close()


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

    # 夏普比率（无风险利率：优先读 DB 最新联储基准利率，不可用时退回 2%）
    rf_annual = _get_rf_rate()
    rf_daily = rf_annual / 252
    excess = daily_rets - rf_daily
    sharpe = (excess.mean() / excess.std() * np.sqrt(252)) if excess.std() > 0 else 0

    # 最大回撤
    rolling_max = nav_series.cummax()
    drawdown = (nav_series - rolling_max) / rolling_max
    max_drawdown = float(drawdown.min()) * 100

    # 波动率
    volatility = float(daily_rets.std() * np.sqrt(252)) * 100

    # 逐年收益（按自然年计算）
    year_returns: dict[str, float] = {}
    nav_df["date_dt"] = pd.to_datetime(nav_df["date"])
    for year, grp in nav_df.groupby(nav_df["date_dt"].dt.year):
        grp = grp.sort_values("date_dt")
        if len(grp) < 2:
            continue
        first_nav = float(grp["nav"].iloc[0])
        last_nav = float(grp["nav"].iloc[-1])
        if first_nav > 0:
            year_returns[str(year)] = round((last_nav / first_nav - 1) * 100, 2)

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
        "year_returns": year_returns,
    }
