"""采集真实市场估值数据（Shiller CAPE、标普500 P/E）。

数据源优先级：
  1. multpl.com 月度表（Shiller CAPE + S&P500 P/E，无需 API Key）
  2. Shiller 官方 Excel（Yale，CAPE 历史数据原始来源，multpl 失败时使用）
  3. yfinance SPY trailingPE（仅有 PE，无 CAPE，最后备用）

两个路径均失败时降级为 estimated（valuation.py 会回退基于点位的近似估算）。
"""
import re
import io
import pandas as pd
from datetime import datetime
from ..utils.database import get_connection
from ..utils import provenance

_MULTPL = {
    "cape":     "https://www.multpl.com/shiller-pe/table/by-month",
    "sp500_pe": "https://www.multpl.com/s-p-500-pe-ratio/table/by-month",
}

# Shiller 官方数据（CAPE 历史序列原始来源，月度，1871-至今）
_SHILLER_XLS = "http://www.econ.yale.edu/~shiller/data/ie_data.xls"

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; fund-research/1.0)"}

# multpl 月度表：<td>Mon D, YYYY</td><td> &#x2002; NN.NN</td>
# &#x2002; 是 en-space HTML 实体，须先跳过所有实体（&#...;）和空白，再捕获数字
_ROW_RE = re.compile(
    r"<td[^>]*>\s*([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})\s*</td>\s*"
    r"<td[^>]*>(?:&#[^;]+;|\s)*([\d.]+)",
    re.IGNORECASE,
)


def collect_valuation_data() -> dict:
    """抓取真实 CAPE / PE 月度序列，存入 valuation_data 表。

    数据源优先级：
      1. multpl.com（CAPE + PE 月度历史）
      2. Shiller 官方 Excel（CAPE 原始来源，multpl 无 CAPE 时补位）
      3. yfinance SPY trailingPE（仅当 PE 缺失时，单点备用）

    返回 {metric: rows_saved}。
    """
    try:
        import requests
        has_requests = True
    except ImportError:
        has_requests = False

    saved = {}

    # ① multpl.com
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

    # ② Shiller 官方 Excel（当 CAPE 未从 multpl 取到时）
    if "cape" not in saved and has_requests:
        shiller_saved = _collect_cape_via_shiller_xls()
        saved.update(shiller_saved)

    # ③ yfinance 备用 PE（当 PE 仍缺失时）
    if "sp500_pe" not in saved:
        yf_saved = _collect_pe_via_yfinance()
        saved.update(yf_saved)

    # 判断整体可信度
    has_cape = "cape" in saved
    has_pe = "sp500_pe" in saved
    # 仅累加整型行数：Shiller 兜底路径会塞入字符串 "_cape_source"，需排除
    total_rows = sum(v for v in saved.values() if isinstance(v, int))

    if has_cape and has_pe:
        # 判断 CAPE 来源决定标签
        source_used = str(saved.get("_cape_source", "multpl.com"))
        provenance.record("valuation", provenance.REAL, total_rows, source_used)
    elif has_cape or has_pe:
        parts: list[str] = []
        if has_cape:
            parts.append(str(saved.get("_cape_source", "cape")))
        if has_pe:
            parts.append("yfinance(PE)")
        provenance.record("valuation", provenance.PARTIAL, total_rows, " + ".join(parts))
    else:
        provenance.record("valuation", provenance.PARTIAL, 0, "估值源不可达，回退点位近似")

    saved.pop("_cape_source", None)
    return saved


def _collect_cape_via_shiller_xls() -> dict:
    """从 Robert Shiller 官方 Excel 获取月度 CAPE 历史（原始来源，1871-至今）。

    Sheet "Data" 中 D 列为日期（小数年，如 1871.01），E 列为实际价格，
    P 列（或名为 'CAPE'/'Cyclically Adjusted Price'）为 CAPE。
    返回 {"cape": rows_saved, "_cape_source": "Shiller XLS"} 或 {}。
    """
    try:
        import requests
        resp = requests.get(_SHILLER_XLS, headers=_HEADERS, timeout=30)
        resp.raise_for_status()
        xls = pd.read_excel(io.BytesIO(resp.content), sheet_name="Data", header=7)
        # 列名清洗：去除换行/多余空格
        xls.columns = [str(c).strip().replace("\n", " ") for c in xls.columns]

        # 定位日期列（小数年，如 2024.01）和 CAPE 列
        date_col = xls.columns[0]  # 第一列：Date (decimal year)
        # CAPE 列名在不同版本中可能是 'CAPE' 或 'Cyclically Adjusted Price Earnings Ratio'
        cape_col = next(
            (c for c in xls.columns if "CAPE" in c.upper() or "CYCLICALLY" in c.upper()),
            None,
        )
        if cape_col is None:
            print("[WARN] Shiller XLS: 找不到 CAPE 列，跳过")
            return {}

        df = xls[[date_col, cape_col]].copy()
        df.columns = ["date_dec", "cape"]
        df = df.dropna(subset=["date_dec", "cape"])
        df = df[pd.to_numeric(df["cape"], errors="coerce").notna()]
        df["cape"] = df["cape"].astype(float)
        df = df[df["cape"] > 0]

        # 小数年转换为 YYYY-MM-DD
        # Shiller 格式：1871.01 = 1月，1871.02 = 2月，…，1871.12 = 12月
        # 小数部分是月份编号（×100），不是年内分数（×12）
        def _dec_to_date(d):
            try:
                d = float(d)
                year = int(d)
                month = round((d - year) * 100)
                month = max(1, min(12, month))
                return f"{year:04d}-{month:02d}-01"
            except Exception:
                return None

        df["date"] = df["date_dec"].apply(_dec_to_date)
        df = df.dropna(subset=["date"])
        series = list(zip(df["date"], df["cape"]))
        series.sort(key=lambda x: x[0], reverse=True)  # 最新在前

        if not series:
            print("[WARN] Shiller XLS: 解析后无有效数据")
            return {}

        _save_valuation("cape", series, source="Shiller XLS")
        print(f"[OK] 估值数据 cape: {len(series)} 条（Shiller XLS，最新 {series[0][0]}={series[0][1]:.2f}）")
        return {"cape": len(series), "_cape_source": "Shiller XLS"}
    except Exception as e:
        print(f"[WARN] Shiller XLS 获取失败: {e}")
        return {}


def _safe_pe_float(raw) -> float | None:
    """把 yfinance 偶发返回的非数字 PE（"N/A" 字符串、dict、None）健壮地转为 float。
    无法转换时返回 None 并告警，绝不抛 ValueError/TypeError 中断采集。"""
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        print(f"[WARN] yfinance PE 值无法转为浮点数: {raw!r}，跳过")
        return None


def _collect_pe_via_yfinance() -> dict:
    """通过 yfinance 获取 SPY 的 trailingPE 作为 sp500_pe 的单点备用。"""
    try:
        import yfinance as yf
        spy = yf.Ticker("SPY")
        info = spy.info
        _pe_raw = info.get("trailingPE") or info.get("forwardPE")
        pe = _safe_pe_float(_pe_raw)
        if pe and pe > 0:
            today = datetime.now().strftime("%Y-%m-%d")
            _save_valuation("sp500_pe", [(today, pe)], source="yfinance")
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
