"""按需采集单只基金数据：净值拉取、费率刷新、绩效重算。"""
import re
import requests
import pandas as pd
from datetime import datetime, timedelta

from ..utils.database import read_table, upsert_dataframe
from ..utils.fund_universe import CORE_QDII_FUNDS


_ETF_TYPES = {"ETF", "LOF", "ETF联接", "增强指数"}


def fetch_on_demand(fund_code: str) -> bool:
    """按需采集单只基金净值数据，并实时刷新费率。返回 True 表示有足够数据。"""
    refresh_expense_ratio(fund_code)

    nav = read_table("fund_nav_history", "fund_code = ? LIMIT 25", (fund_code,))
    if len(nav) >= 20:
        recompute_performance(fund_code)
        return True

    universe_map = {f["fund_code"]: f for f in CORE_QDII_FUNDS}
    fund_meta = universe_map.get(fund_code)
    fund_type = fund_meta.get("fund_type", "") if fund_meta else ""

    existing = set(read_table("fund_list")["fund_code"].astype(str).tolist())
    if fund_code not in existing and fund_meta:
        upsert_dataframe(pd.DataFrame([{
            "fund_code":  fund_code,
            "fund_name":  fund_meta["fund_name"],
            "fund_type":  fund_type,
            "benchmark":  fund_meta.get("benchmark", ""),
            "updated_at": datetime.now().strftime("%Y-%m-%d"),
        }]), "fund_list", ["fund_code"])

    if fund_type in _ETF_TYPES:
        print(f"  [按需采集] {fund_code} 从 yfinance 拉取历史净值...")
        _fetch_etf_nav(fund_code)
    else:
        print(f"  [按需采集] {fund_code} 从天天基金拉取历史净值...")
        try:
            from .eastmoney_collector import collect_eastmoney
            r = collect_eastmoney([fund_code])
            if r.get("nav_rows", 0):
                print(f"  [按需采集] {r['nav_rows']} 条净值（天天基金）")
        except Exception as e:
            print(f"  [按需采集] 天天基金失败: {e}")

    return recompute_performance(fund_code)


def refresh_expense_ratio(fund_code: str) -> None:
    """从天天基金 F10 页实时拉取管理费+托管费，更新 fund_list。"""
    try:
        url = f"https://fundf10.eastmoney.com/jjfl_{fund_code}.html"
        headers = {"Referer": "https://fundf10.eastmoney.com/",
                   "User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=8)
        if r.status_code != 200:
            return

        mgmt = re.search(r'管理费率</td><td[^>]*>([\d.]+)%', r.text)
        cust = re.search(r'托管费率</td><td[^>]*>([\d.]+)%', r.text)
        sale = re.search(r'销售服务费率</td><td[^>]*>([\d.]+)%', r.text)

        if mgmt and cust:
            total = float(mgmt.group(1)) + float(cust.group(1))
            if sale:
                total += float(sale.group(1))
            upsert_dataframe(pd.DataFrame([{
                "fund_code":     fund_code,
                "expense_ratio": round(total / 100, 6),
                "updated_at":    datetime.now().strftime("%Y-%m-%d"),
            }]), "fund_list", ["fund_code"])
            print(f"  [费率] {fund_code} 管理{mgmt.group(1)}%+托管{cust.group(1)}% = {total:.2f}% (已更新)")
    except Exception:
        pass  # 费率更新失败不阻断主流程


def recompute_performance(fund_code: str) -> bool:
    """重算绩效指标并写回 fund_performance。返回是否有足够数据。"""
    nav_check = read_table("fund_nav_history", "fund_code = ? LIMIT 25", (fund_code,))
    if len(nav_check) < 20:
        return False
    try:
        from ..analyzers.fund_analyzer import _calc_performance
        perf = _calc_performance(fund_code)
        if perf:
            upsert_dataframe(pd.DataFrame([perf]), "fund_performance", ["fund_code"])
    except Exception as e:
        # 绩效重算失败会让 fund_performance 停留在旧值而调用方仍收到 True，
        # 评分据此判断会用陈旧数据，必须可见。
        print(f"[WARN] {fund_code} 绩效重算失败（fund_performance 维持旧值）: {e}")
    return True


def _fetch_etf_nav(fund_code: str) -> None:
    try:
        from .baostock_etf_collector import _yf_ticker
        import yfinance as yf
        ticker = _yf_ticker(fund_code)
        df = yf.download(
            ticker,
            start=(datetime.now() - timedelta(days=365 * 5)).strftime("%Y-%m-%d"),
            auto_adjust=True, progress=False,
        )
        if not df.empty:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df[["Close"]].reset_index()
            df.columns = ["date", "nav"]
            df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.strftime("%Y-%m-%d")
            df["acc_nav"] = df["nav"]
            df["daily_return"] = df["nav"].pct_change() * 100
            df["fund_code"] = fund_code
            upsert_dataframe(
                df[["fund_code", "date", "nav", "acc_nav", "daily_return"]].dropna(subset=["nav"]),
                "fund_nav_history", ["fund_code", "date"],
            )
            print(f"  [按需采集] {len(df)} 条净值（yfinance）")
    except Exception as e:
        print(f"  [按需采集] yfinance 失败: {e}")
