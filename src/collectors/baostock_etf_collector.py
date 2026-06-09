"""场内 ETF/LOF QDII 基金采集器（yfinance 行情接口）

CORE_QDII_FUNDS 中 fund_type 含 ETF / LOF / ETF联接 / 增强指数 的基金，
无法通过天天基金 pingzhongdata 接口拿到净值，需要从 A 股行情（后复权收盘价）采集。

沪市 ETF → 代码.SS（如 513100.SS）
深市 ETF → 代码.SZ（如 159941.SZ）

写入表：
  fund_list        —— 基本信息（若不存在则插入）
  fund_nav_history —— 以后复权收盘价模拟净值
  fund_performance —— 由后续 fund_analyzer.analyze_all_funds() 统一重算
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from ..utils.database import upsert_dataframe, read_table
from ..utils.fund_universe import CORE_QDII_FUNDS

_ETF_TYPES = {"ETF", "LOF", "ETF联接", "增强指数"}

# 已知深交所上市的 ETF 代码（159 开头均为深市）
def _yf_ticker(fund_code: str) -> str:
    if fund_code.startswith("159") or fund_code.startswith("16"):
        return f"{fund_code}.SZ"
    return f"{fund_code}.SS"


def collect_etf_nav(
    start_date: str | None = None,
    end_date: str | None = None,
    verbose: bool = True,
) -> int:
    """用 yfinance 采集 CORE_QDII_FUNDS 中 ETF/LOF 类型基金的后复权日收盘价。"""
    try:
        import yfinance as yf
    except ImportError:
        print("[etf_collector] yfinance 未安装，跳过 ETF 采集（pip install yfinance）")
        return 0

    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")
    if start_date is None:
        start_date = (datetime.now() - timedelta(days=365 * 5)).strftime("%Y-%m-%d")

    targets = [f for f in CORE_QDII_FUNDS if f.get("fund_type", "") in _ETF_TYPES]
    if not targets:
        return 0

    existing_fl = read_table("fund_list")
    existing_codes = set(existing_fl["fund_code"].astype(str).tolist()) if not existing_fl.empty else set()

    success = 0
    all_nav_rows: list[dict] = []
    fund_list_rows: list[dict] = []

    for f in targets:
        code      = str(f["fund_code"])
        ticker    = _yf_ticker(code)
        name      = f["fund_name"]
        fund_type = f.get("fund_type", "ETF")

        try:
            df = yf.download(
                ticker,
                start=start_date,
                end=end_date,
                auto_adjust=True,   # 后复权
                progress=False,
            )
            if df.empty:
                if verbose:
                    print(f"[etf_collector] {code} {name}: 无数据")
                continue

            # 兼容 yfinance ≥0.2 多级列名
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            df = df[["Close"]].copy()
            df.index = pd.to_datetime(df.index).tz_localize(None)
            df.index.name = "date"
            df = df.reset_index()
            df["date"]         = df["date"].dt.strftime("%Y-%m-%d")
            df["nav"]          = df["Close"].astype(float).round(4)
            df["acc_nav"]      = df["nav"]
            df["daily_return"] = df["nav"].pct_change() * 100
            df["fund_code"]    = code

            nav_rows = df[["fund_code", "date", "nav", "acc_nav", "daily_return"]].dropna(subset=["nav"]).to_dict("records")
            all_nav_rows.extend(nav_rows)

            if code not in existing_codes:
                fund_list_rows.append({
                    "fund_code":    code,
                    "fund_name":    name,
                    "fund_type":    fund_type,
                    "benchmark":    f.get("benchmark", ""),
                    "expense_ratio": f.get("expense_ratio"),
                    "updated_at":   datetime.now().strftime("%Y-%m-%d"),
                })
                existing_codes.add(code)

            success += 1
            if verbose:
                print(f"[etf_collector] {code} {name}: {len(nav_rows)} 条 ({df['date'].iloc[0]}~{df['date'].iloc[-1]})")

        except Exception as e:
            if verbose:
                print(f"[etf_collector] {code} {name}: 失败 {e}")

    if all_nav_rows:
        upsert_dataframe(pd.DataFrame(all_nav_rows), "fund_nav_history", ["fund_code", "date"])
    if fund_list_rows:
        upsert_dataframe(pd.DataFrame(fund_list_rows), "fund_list", ["fund_code"])

    if verbose:
        print(f"[etf_collector] 完成：{success}/{len(targets)} 只 ETF 采集成功，{len(all_nav_rows)} 条净值写入")

    # ── 天天基金兜底：对 yfinance 无法覆盖的基金补采净值 ───────────
    fetched_codes = {r["fund_code"] for r in all_nav_rows}
    missing_funds = [f for f in targets if f["fund_code"] not in fetched_codes]
    if missing_funds:
        missing_codes = [str(f["fund_code"]) for f in missing_funds]
        if verbose:
            print(f"[etf_collector] yfinance 未覆盖 {len(missing_funds)} 只，转用天天基金兜底: {missing_codes}")

        # 先确保这些基金在 fund_list 中（analyze_all_funds 依赖此表）
        _ensure_fund_list(missing_funds, existing_codes)

        try:
            from .eastmoney_collector import collect_eastmoney
            collect_eastmoney(missing_codes)
        except Exception as e:
            if verbose:
                print(f"[etf_collector] 天天基金兜底失败: {e}")

        # 检验净值时效：数据停留在 2年前以上则警告
        _warn_stale_nav(missing_codes, verbose)

    return success


def _ensure_fund_list(funds: list[dict], existing_codes: set):
    """把尚未在 fund_list 中的基金写入，使 analyze_all_funds() 能覆盖到。"""
    new_rows = []
    for f in funds:
        if f["fund_code"] not in existing_codes:
            new_rows.append({
                "fund_code":    f["fund_code"],
                "fund_name":    f["fund_name"],
                "fund_type":    f.get("fund_type", ""),
                "benchmark":    f.get("benchmark", ""),
                "expense_ratio": f.get("expense_ratio"),
                "updated_at":   datetime.now().strftime("%Y-%m-%d"),
            })
            existing_codes.add(f["fund_code"])
    if new_rows:
        upsert_dataframe(pd.DataFrame(new_rows), "fund_list", ["fund_code"])


def _warn_stale_nav(codes: list[str], verbose: bool):
    """对净值最新日期超过2年的基金发出警告（基金可能已清盘/改名）。"""
    try:
        from ..utils.database import get_connection
        conn = get_connection()
        threshold = (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")
        for code in codes:
            row = conn.execute(
                "SELECT MAX(date) FROM fund_nav_history WHERE fund_code=?", (code,)
            ).fetchone()
            latest = row[0] if row and row[0] else None
            if latest and latest < threshold:
                print(f"[etf_collector] ⚠️  {code} 净值数据截至 {latest}（超2年未更新，基金可能已清盘/改名）")
        conn.close()
    except Exception:
        pass
