"""采集真实市场估值数据（Shiller CAPE、标普500 P/E）。

数据源优先级：
  1. multpl.com 月度表（Shiller CAPE + S&P500 P/E，无需 API Key）
  2. yfinance SPY trailingPE（multpl.com 不可达时备用，仅有 PE，无 CAPE）

两个路径均失败时降级为 estimated（valuation.py 会回退基于点位的近似估算）。
"""
import re
import pandas as pd
from datetime import datetime
from ..utils.database import get_connection
from ..utils import provenance

_MULTPL = {
    "cape":     "https://www.multpl.com/shiller-pe/table/by-month",
    "sp500_pe": "https://www.multpl.com/s-p-500-pe-ratio/table/by-month",
}

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; fund-research/1.0)"}

# multpl 月度表的数据行：<td ...>Mon D, YYYY</td> ... <td ...>NN.NN</td>
_ROW_RE = re.compile(
    r"<td[^>]*>\s*([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})\s*</td>\s*"
    r"<td[^>]*>\s*([\d.]+)",
    re.IGNORECASE,
)


def collect_valuation_data() -> dict:
    """抓取真实 CAPE / PE 月度序列，存入 valuation_data 表。

    返回 {metric: rows_saved}。无网络/解析失败时尝试 yfinance 备用，
    两者均失败时返回空 dict 并记 partial。
    """
    try:
        import requests
        has_requests = True
    except ImportError:
        has_requests = False

    saved = {}

    if has_requests:
        for metric, url in _MULTPL.items():
            try:
                resp = requests.get(url, headers=_HEADERS, timeout=15)
                resp.raise_for_status()
                series = _parse_multpl_table(resp.text)
                if series:
                    _save_valuation(metric, series, source="multpl")
                    saved[metric] = len(series)
                    print(f"[OK] 估值数据 {metric}: {len(series)} 条"
                          f"（multpl，最新 {series[0][0]}={series[0][1]}）")
            except Exception as e:
                print(f"[WARN] 估值 {metric} 获取失败: {e}")

    # multpl.com 至少拿到 CAPE 和 PE 其中之一算作真实数据
    if "cape" in saved and "sp500_pe" in saved:
        provenance.record("valuation", provenance.REAL, sum(saved.values()), "multpl.com")
        return saved

    # 备用：yfinance SPY trailingPE（仅提供 PE，无历史 CAPE）
    if "sp500_pe" not in saved:
        yf_saved = _collect_pe_via_yfinance()
        saved.update(yf_saved)

    if saved:
        source_label = "multpl.com(CAPE) + yfinance(PE)" if "cape" in saved else "yfinance(PE only)"
        provenance.record("valuation", provenance.PARTIAL, sum(saved.values()), source_label)
    else:
        provenance.record("valuation", provenance.PARTIAL, 0, "估值源不可达，回退点位近似")
    return saved


def _collect_pe_via_yfinance() -> dict:
    """通过 yfinance 获取 SPY 的 trailingPE 作为 sp500_pe 的单点备用。"""
    try:
        import yfinance as yf
        spy = yf.Ticker("SPY")
        info = spy.info
        pe = info.get("trailingPE") or info.get("forwardPE")
        if pe and float(pe) > 0:
            today = datetime.now().strftime("%Y-%m-%d")
            _save_valuation("sp500_pe", [(today, float(pe))], source="yfinance")
            print(f"[OK] 估值数据 sp500_pe (yfinance 备用): {pe:.2f}")
            return {"sp500_pe": 1}
    except Exception as e:
        print(f"[WARN] yfinance 估值 fallback 失败: {e}")
    return {}


def _parse_multpl_table(html: str) -> list:
    """返回 [(date_str 'YYYY-MM-DD', value_float), ...]，按日期降序（最新在前）。"""
    rows = []
    for m in _ROW_RE.finditer(html):
        date_raw, val_raw = m.group(1), m.group(2)
        try:
            d = datetime.strptime(date_raw, "%b %d, %Y").strftime("%Y-%m-%d")
            rows.append((d, float(val_raw)))
        except ValueError:
            continue
    seen = set()
    uniq = []
    for d, v in sorted(rows, key=lambda x: x[0], reverse=True):
        if d not in seen:
            seen.add(d)
            uniq.append((d, v))
    return uniq


def _save_valuation(metric: str, series: list, source: str):
    conn = get_connection()
    try:
        conn.executemany(
            """INSERT INTO valuation_data (metric, date, value, source, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'))
               ON CONFLICT(metric, date) DO UPDATE SET
                 value=excluded.value, source=excluded.source, updated_at=excluded.updated_at""",
            [(metric, d, v, source) for d, v in series],
        )
        conn.commit()
    finally:
        conn.close()


def read_valuation_series(metric: str) -> pd.DataFrame:
    """读取某估值指标的历史序列（按日期升序）。"""
    conn = get_connection()
    try:
        return pd.read_sql_query(
            "SELECT date, value, source FROM valuation_data WHERE metric = ? ORDER BY date",
            conn, params=(metric,),
        )
    finally:
        conn.close()
