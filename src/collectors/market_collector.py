"""采集美股及全球市场数据（yfinance，无需API Key）"""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from ..utils.config import load_config
from ..utils.database import upsert_dataframe


def collect_market_data() -> dict[str, pd.DataFrame]:
    cfg = load_config()
    all_symbols = []

    for category in ["us", "global", "volatility", "commodities"]:
        for item in cfg["market_indices"].get(category, []):
            all_symbols.append(item)

    for item in cfg.get("sector_etfs", []):
        all_symbols.append(item)

    from ..utils import provenance

    results = {}
    mode = provenance.REAL
    detail = "yfinance"
    try:
        import yfinance as yf
        start = (datetime.now() - timedelta(days=365 * 3)).strftime("%Y-%m-%d")

        for item in all_symbols:
            symbol = item["symbol"]
            name = item["name"]
            try:
                ticker = yf.Ticker(symbol)
                hist = ticker.history(start=start, auto_adjust=True)
                if hist.empty:
                    continue
                hist = hist.reset_index()
                hist.columns = [c.lower() for c in hist.columns]
                hist["date"] = pd.to_datetime(hist["date"]).dt.strftime("%Y-%m-%d")
                hist["symbol"] = symbol
                hist["name"] = name
                df = hist[["symbol", "name", "date", "open", "high", "low", "close", "volume"]].copy()
                df = df.dropna(subset=["close"])
                results[symbol] = df
                print(f"[OK] 市场数据 {symbol}({name}): {len(df)} 条")
            except Exception as e:
                print(f"[WARN] {symbol} 获取失败: {e}")

    except ImportError:
        print("[WARN] yfinance未安装，使用模拟市场数据")
        results = _generate_mock_market(all_symbols)
        mode, detail = provenance.MOCK, "yfinance 未安装"

    # 真实路径下若一条都没取到（网络异常），也降级标记为模拟
    if mode == provenance.REAL and not results:
        results = _generate_mock_market(all_symbols)
        mode, detail = provenance.MOCK, "yfinance 全部获取失败"

    # 信号模型关键标的：缺失任一降级为 PARTIAL
    if mode == provenance.REAL:
        _CRITICAL_SYMBOLS = {"^GSPC", "^VIX"}
        missing_sym = _CRITICAL_SYMBOLS - set(results.keys())
        if missing_sym:
            print(f"[WARN] 关键市场标的缺失: {', '.join(sorted(missing_sym))}，趋势/情绪因子将回退默认值")
            mode = provenance.PARTIAL
            detail = f"yfinance（缺失关键标的: {', '.join(sorted(missing_sym))}）"

    _save_market(results)
    provenance.record("market", mode, sum(len(df) for df in results.values()), detail)
    return results


def _save_market(results: dict):
    rows = []
    for symbol, df in results.items():
        rows.append(df)
    if rows:
        combined = pd.concat(rows, ignore_index=True)
        upsert_dataframe(combined, "market_data", ["symbol", "date"])
        print(f"[DB] 市场数据已保存 {len(combined)} 条")


def _generate_mock_market(symbols: list) -> dict:
    np.random.seed(int(datetime.now().strftime("%Y%m%d")))
    dates = pd.date_range(end=datetime.now(), periods=252, freq="B")
    date_strs = dates.strftime("%Y-%m-%d").tolist()
    results = {}
    base_prices = {
        "^GSPC": 5000, "^IXIC": 16000, "^DJI": 40000,
        "^VIX": 18, "GC=F": 2300, "CL=F": 80,
    }
    for item in symbols:
        symbol = item["symbol"]
        base = base_prices.get(symbol, 100)
        returns = np.random.randn(252) * 0.01
        prices = base * np.cumprod(1 + returns)
        results[symbol] = pd.DataFrame({
            "symbol": symbol,
            "name": item["name"],
            "date": date_strs,
            "open": prices * 0.999,
            "high": prices * 1.005,
            "low": prices * 0.995,
            "close": prices,
            "volume": np.random.randint(1e6, 1e9, 252).astype(float),
        })
    return results


def get_latest_prices(symbols: list[str]) -> dict[str, float]:
    from ..utils.database import read_table
    prices = {}
    for symbol in symbols:
        df = read_table("market_data", "symbol = ? ORDER BY date DESC LIMIT 1", (symbol,))
        if not df.empty:
            prices[symbol] = float(df.iloc[0]["close"])
    return prices
