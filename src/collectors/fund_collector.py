"""采集国内QDII基金数据（akshare → CSV种子 → 随机模拟）"""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from ..utils.database import upsert_dataframe, read_table
from ..utils.fund_universe import CORE_QDII_FUNDS, EXPENSE_RATIO_BY_CODE

_DATA_DIR = Path(__file__).parent.parent.parent / "data"
_NAV_CSV = _DATA_DIR / "fund_nav_seed.csv"
_LIST_CSV = _DATA_DIR / "fund_list_seed.csv"


def collect_fund_data() -> tuple[list, dict]:
    from ..utils import provenance
    fund_list = []
    nav_history = {}

    # 1) 尝试 akshare
    try:
        import akshare as ak
        fund_list = _collect_via_akshare(ak)
        nav_history = _collect_nav_history(ak, [f["fund_code"] for f in fund_list[:20]])
        _save_fund_list(fund_list)
        _save_nav_history(nav_history)
        nav_rows = sum(len(v) for v in nav_history.values())
        if nav_rows > 0:
            provenance.record("fund", provenance.REAL, nav_rows, "akshare")
        else:
            provenance.record("fund", provenance.MOCK, 0, "akshare 无净值历史")
        return fund_list, nav_history
    except ImportError:
        print("[WARN] akshare未安装，尝试CSV种子数据")
    except Exception as e:
        print(f"[WARN] akshare异常: {e}，尝试CSV种子数据")

    # 2) 本地CSV种子（真实历史净值）
    fund_list, nav_history = _load_from_csv_seed()
    nav_rows = sum(len(v) for v in nav_history.values())
    if nav_rows > 0:
        print(f"[OK] CSV种子加载: {len(fund_list)} 只基金，{nav_rows} 条净值记录")
        nav_history = _patch_recent_via_yfinance(fund_list, nav_history)
        _save_fund_list(fund_list)
        _save_nav_history(nav_history)
        provenance.record("fund", provenance.REAL, sum(len(v) for v in nav_history.values()),
                          "CSV种子+yfinance补全")
        return fund_list, nav_history

    # 3) 兜底：随机模拟（数据无效，仅供界面展示）
    print("[WARN] 未找到CSV种子净值，使用随机模拟数据（运行 tools/download_seed_data.py 获取真实数据）")
    if not fund_list:
        fund_list = _build_core_list()
    nav_history = _generate_mock_nav(fund_list)
    _save_fund_list(fund_list)
    _save_nav_history(nav_history)
    provenance.record("fund", provenance.MOCK, sum(len(v) for v in nav_history.values()),
                      "无CSV种子，随机模拟")
    return fund_list, nav_history


def _collect_via_akshare(ak) -> list:
    try:
        df = ak.fund_open_fund_info_em(symbol="QDII")
        funds = []
        for _, row in df.iterrows():
            code = str(row.get("基金代码", ""))
            funds.append({
                "fund_code": code,
                "fund_name": str(row.get("基金简称", "")),
                "fund_type": "QDII",
                "manager": str(row.get("基金经理人", "")),
                "company": str(row.get("基金公司", "")),
                "nav": float(row.get("单位净值", 0) or 0),
                "nav_date": str(row.get("净值日期", "")),
                # 真实费率：核心库已知则回填，未知留 None（不要清零，否则成本分恒满分）
                "expense_ratio": EXPENSE_RATIO_BY_CODE.get(code),
            })
        print(f"[OK] akshare QDII基金列表: {len(funds)} 只")
        # 合并核心基金确保覆盖（带真实费率与基准）
        existing_codes = {f["fund_code"] for f in funds}
        for cf in CORE_QDII_FUNDS:
            if cf["fund_code"] not in existing_codes:
                funds.append({
                    "fund_code": cf["fund_code"],
                    "fund_name": cf["fund_name"],
                    "fund_type": cf["fund_type"],
                    "manager": "", "company": "",
                    "nav": 1.0, "nav_date": "",
                    "expense_ratio": cf["expense_ratio"],
                    "benchmark": cf.get("benchmark", ""),
                })
        return funds
    except Exception as e:
        print(f"[WARN] akshare QDII列表获取失败: {e}")
        return _build_core_list()


def _collect_nav_history(ak, fund_codes: list) -> dict:
    nav_data = {}

    for code in fund_codes:
        try:
            df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
            if df is not None and not df.empty:
                df = df.rename(columns={"净值日期": "date", "单位净值": "nav", "日增长率": "daily_return"})
                df["fund_code"] = code
                df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
                df["acc_nav"] = df.get("累计净值", df["nav"])
                nav_data[code] = df[["fund_code", "date", "nav", "acc_nav", "daily_return"]].dropna(subset=["nav"])
        except Exception as e:
            print(f"[WARN] 基金 {code} 净值历史采集失败: {e}")
    return nav_data


def _build_core_list() -> list:
    funds = []
    for cf in CORE_QDII_FUNDS:
        funds.append({
            "fund_code": cf["fund_code"],
            "fund_name": cf["fund_name"],
            "fund_type": cf["fund_type"],
            "manager": "",
            "company": "",
            "nav": 1.0,
            "nav_date": datetime.now().strftime("%Y-%m-%d"),
            "expense_ratio": cf["expense_ratio"],  # 真实费率
            "benchmark": cf.get("benchmark", ""),
        })
    return funds


def _generate_mock_nav(fund_list: list) -> dict:
    np.random.seed(int(datetime.now().strftime("%Y%m%d")))
    nav_data = {}
    dates = pd.date_range(end=datetime.now(), periods=365 * 3, freq="B")
    date_strs = dates.strftime("%Y-%m-%d").tolist()
    for fund in fund_list[:15]:
        code = fund["fund_code"]
        base = np.random.uniform(1.0, 3.0)
        returns = np.random.randn(len(dates)) * 0.012
        prices = base * np.cumprod(1 + returns)
        nav_data[code] = pd.DataFrame({
            "fund_code": code,
            "date": date_strs,
            "nav": prices,
            "acc_nav": prices,
            "daily_return": returns * 100,
        })
    return nav_data


def _save_fund_list(fund_list: list):
    if not fund_list:
        return
    df = pd.DataFrame(fund_list)
    df = df[[c for c in ["fund_code", "fund_name", "fund_type", "manager", "company",
                          "nav", "nav_date", "expense_ratio", "benchmark"] if c in df.columns]]
    df["fund_code"] = df["fund_code"].astype(str)
    upsert_dataframe(df, "fund_list", ["fund_code"])
    print(f"[DB] 基金列表已保存 {len(df)} 只")


def _save_nav_history(nav_history: dict):
    if not nav_history:
        return
    all_rows = []
    for code, df in nav_history.items():
        all_rows.append(df)
    combined = pd.concat(all_rows, ignore_index=True)
    cols = [c for c in ["fund_code", "date", "nav", "acc_nav", "daily_return"] if c in combined.columns]
    upsert_dataframe(combined[cols], "fund_nav_history", ["fund_code", "date"])
    print(f"[DB] 基金净值历史已保存 {len(combined)} 条")


def _load_from_csv_seed() -> tuple[list, dict]:
    """从本地CSV种子文件加载基金列表和净值历史"""
    fund_list = []
    nav_history = {}

    # 加载基金列表
    if _LIST_CSV.exists():
        df = pd.read_csv(_LIST_CSV, dtype={"fund_code": str})
        fund_list = df.to_dict("records")
    else:
        fund_list = _build_core_list()

    # 加载净值历史
    if not _NAV_CSV.exists():
        return fund_list, nav_history

    nav_df = pd.read_csv(_NAV_CSV, dtype={"fund_code": str})
    nav_df["date"] = pd.to_datetime(nav_df["date"]).dt.strftime("%Y-%m-%d")
    nav_df["nav"] = pd.to_numeric(nav_df["nav"], errors="coerce")
    nav_df["acc_nav"] = pd.to_numeric(nav_df["acc_nav"], errors="coerce")
    nav_df["daily_return"] = pd.to_numeric(nav_df["daily_return"], errors="coerce")
    nav_df = nav_df.dropna(subset=["nav"])

    for code, group in nav_df.groupby("fund_code"):
        nav_history[code] = group.reset_index(drop=True)

    return fund_list, nav_history


# yfinance ETF代理，仅用于补充CSV种子之后的近期数据
_QDII_TO_ETF = {
    "513100": "QQQ", "513500": "SPY", "159941": "QQQ",
    "513880": "EWJ", "513000": "EWJ", "015691": "EWJ",
    "513030": "EWG", "160218": "EWG",
    "164906": "XOP", "000934": "QQQ",
}


def _patch_recent_via_yfinance(fund_list: list, nav_history: dict) -> dict:
    """用yfinance补充CSV种子结束日期之后的近期净值（静默失败）。

    重要：QDII 净值以人民币计价，美国ETF代理以美元计价，必须用 USD/CNY 汇率
    换算到人民币口径后再拼接，否则忽略汇率敞口会污染净值。汇率不可得时
    宁可跳过拼接，也不拼接错误数据。
    """
    try:
        import yfinance as yf
    except ImportError:
        return nav_history

    today_str = datetime.now().strftime("%Y-%m-%d")

    # 先取 USD/CNY 汇率序列（覆盖全部待补区间）；取不到则放弃拼接，避免污染
    fx = _fetch_usdcny(yf)
    if fx is None or fx.empty:
        print("[yfinance] 无法获取USD/CNY汇率，跳过净值拼接（避免引入汇率误差）")
        return nav_history

    for fund in fund_list:
        code = str(fund["fund_code"])
        ticker = _QDII_TO_ETF.get(code)
        if not ticker:
            continue

        existing = nav_history.get(code)
        if existing is None or existing.empty:
            continue
        last_date = existing["date"].max()
        if last_date >= today_str:
            continue

        try:
            start = (datetime.strptime(last_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            df = yf.download(ticker, start=start, end=today_str, progress=False, auto_adjust=True)
            if df.empty:
                continue

            df = df[["Close"]].reset_index()
            df.columns = ["date", "close"]
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")

            # 用当日汇率把美元价换算为人民币口径（按日期对齐，缺失日向前填充）
            df["fx"] = df["date"].map(fx.to_dict())
            df["fx"] = df["fx"].ffill().bfill()
            df = df.dropna(subset=["fx"])
            if df.empty:
                continue
            df["close_cny"] = df["close"] * df["fx"]

            # 接续CSV最后一条净值（以人民币口径缩放）
            _last_nav_raw = existing.iloc[-1]["nav"]
            if not pd.notna(_last_nav_raw):
                print(f"[WARN] 基金 {code} CSV末行nav为NULL，跳过近期补全")
                continue
            last_nav = float(_last_nav_raw)
            base = float(df["close_cny"].iloc[0])
            if base <= 0:
                continue
            scale = last_nav / base
            df["nav"] = (df["close_cny"] * scale).round(4)
            df["acc_nav"] = df["nav"]
            df["daily_return"] = df["close_cny"].pct_change() * 100
            df["fund_code"] = code
            df = df[["fund_code", "date", "nav", "acc_nav", "daily_return"]].dropna(subset=["nav"])

            nav_history[code] = pd.concat([existing, df], ignore_index=True).drop_duplicates("date")
            print(f"[yfinance] {code} 补充近期数据 {len(df)} 条（汇率换算，{start} ~ {today_str}）")
        except Exception as e:
            print(f"[WARN] 基金 {code} yfinance净值补全失败（保留原有CSV数据）: {e}")

    return nav_history


def _fetch_usdcny(yf) -> "pd.Series | None":
    """获取 USD/CNY 日汇率，返回 {date_str: rate} 的 Series；失败返回 None。"""
    try:
        fx = yf.download("CNY=X", period="6mo", progress=False, auto_adjust=True)
        if fx is None or fx.empty:
            return None
        close = fx["Close"]
        # yfinance 可能返回 MultiIndex 列
        if hasattr(close, "columns"):
            close = close.iloc[:, 0]
        s = close.reset_index()
        s.columns = ["date", "rate"]
        s["date"] = pd.to_datetime(s["date"]).dt.strftime("%Y-%m-%d")
        return s.set_index("date")["rate"].astype(float)
    except Exception as e:
        print(f"[WARN] USD/CNY汇率获取失败，跳过近期净值补全: {e}")
        return None
