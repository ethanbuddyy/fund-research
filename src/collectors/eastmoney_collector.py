"""天天基金(东方财富) pingzhongdata 直连采集器。

接口：https://fund.eastmoney.com/pingzhongdata/{code}.js （免费、无需Key、需Referer）
一次拿到：单位净值全历史、累计净值、资产配置(股/债/现金占比)、重仓股代码、基金经理。

用途：
  1) 用真实净值全历史覆盖 fund_nav_history（即使 akshare 缺失或之前是模拟数据）；
  2) 资产配置/重仓股写入 fund_holdings，为策略分类提供真实行业暴露背景。

非官方接口，东财改版可能导致字段变动；所有解析独立容错，失败即跳过该字段/该基金，
并通过 provenance 标记，绝不让单只基金的异常中断整体流程。
"""
import re
import json
from datetime import datetime, timezone, timedelta
from ..utils.database import get_connection
from ..utils import provenance
from ..utils.fund_universe import CORE_QDII_FUNDS

_URL = "https://fund.eastmoney.com/pingzhongdata/{code}.js"
_HEADERS = {
    "Referer": "https://fundf10.eastmoney.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


def collect_eastmoney(fund_codes: list = None) -> dict:
    """抓取各基金 pingzhongdata，富集真实净值与持仓。返回汇总统计。"""
    try:
        import requests
    except ImportError:
        provenance.record("fund_holdings", provenance.MOCK, 0, "requests 未安装")
        return {"funds": 0}

    if fund_codes is None:
        fund_codes = [f["fund_code"] for f in CORE_QDII_FUNDS]

    nav_ok = 0
    hold_ok = 0
    nav_total = 0
    for code in fund_codes:
        try:
            resp = requests.get(_URL.format(code=code), headers=_HEADERS, timeout=15)
            if resp.status_code != 200 or not resp.text:
                continue
            js = resp.text

            nav_rows = _parse_nav(js, code)
            if nav_rows:
                _save_nav(nav_rows)
                nav_ok += 1
                nav_total += len(nav_rows)

            holding = _parse_holdings(js, code)
            if holding:
                _save_holdings(holding)
                hold_ok += 1
        except Exception as e:
            print(f"[WARN] eastmoney {code} 失败: {e}")
            continue
        import time
        time.sleep(0.3)  # 礼貌延迟，避免限速

    if nav_ok:
        # 拿到真实净值 → 基金数据来源标记为真实
        provenance.record("fund", provenance.REAL, nav_total, f"eastmoney pingzhongdata({nav_ok}只)")
    if hold_ok:
        provenance.record("fund_holdings", provenance.REAL, hold_ok, "eastmoney pingzhongdata")
    elif not nav_ok:
        provenance.record("fund_holdings", provenance.PARTIAL, 0, "pingzhongdata 不可达")

    print(f"[OK] eastmoney: 净值 {nav_ok} 只/{nav_total} 条，持仓 {hold_ok} 只")
    return {"funds": len(fund_codes), "nav_funds": nav_ok, "nav_rows": nav_total, "holding_funds": hold_ok}


# ── 解析 ────────────────────────────────────────────────

def _extract_var(js: str, name: str):
    """从 `var NAME = <value>;` 中提取并 json 解析 value；失败返回 None。

    pingzhongdata 变量间以 `;/*注释*/var ...` 分隔（分号与下一个 var 之间夹着注释），
    故以 “分号 + 可选注释 + 下一个 var/结尾” 作为值的右边界。
    """
    m = re.search(
        r"var\s+" + re.escape(name) + r"\s*=\s*(.*?);\s*(?:/\*.*?\*/\s*)?(?=var\b|$)",
        js, re.S,
    )
    if not m:
        return None
    try:
        return json.loads(m.group(1).strip())
    except Exception:
        return None


def _ms_to_date(ms) -> str:
    """东财毫秒时间戳 → 交易日 YYYY-MM-DD（按北京时区）。"""
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(timezone(timedelta(hours=8)))
    return dt.strftime("%Y-%m-%d")


def _parse_nav(js: str, code: str) -> list:
    """返回 [(fund_code, date, nav, acc_nav, daily_return), ...]。"""
    trend = _extract_var(js, "Data_netWorthTrend")   # [{x,y,equityReturn,unitMoney}]
    if not isinstance(trend, list) or not trend:
        return []

    # 累计净值：[[x_ms, acc], ...] → {date: acc}
    acc_map = {}
    acc = _extract_var(js, "Data_ACWorthTrend")
    if isinstance(acc, list):
        for item in acc:
            try:
                acc_map[_ms_to_date(item[0])] = float(item[1])
            except (TypeError, ValueError, IndexError):
                continue

    rows = []
    for pt in trend:
        try:
            d = _ms_to_date(pt["x"])
            nav = float(pt["y"])
        except (TypeError, ValueError, KeyError):
            continue
        dr = pt.get("equityReturn")
        try:
            dr = float(dr) if dr not in (None, "") else None
        except (TypeError, ValueError):
            dr = None
        rows.append((code, d, nav, acc_map.get(d, nav), dr))
    return rows


def _parse_holdings(js: str, code: str) -> dict | None:
    """返回 {fund_code, date, stock_ratio, bond_ratio, cash_ratio, stock_codes, managers}。"""
    out = {"fund_code": code, "date": None, "stock_ratio": None,
           "bond_ratio": None, "cash_ratio": None, "stock_codes": None, "managers": None}
    got = False

    alloc = _extract_var(js, "Data_assetAllocation")  # {categories:[...], series:[{name,data},...]}
    if isinstance(alloc, dict):
        cats = alloc.get("categories") or []
        if cats:
            out["date"] = str(cats[-1])
        for s in alloc.get("series", []):
            name = s.get("name", "")
            data = s.get("data") or []
            if not data:
                continue
            last = data[-1]
            try:
                last = float(last)
            except (TypeError, ValueError):
                continue
            if "股票" in name:
                out["stock_ratio"] = last; got = True
            elif "债券" in name:
                out["bond_ratio"] = last; got = True
            elif "现金" in name:
                out["cash_ratio"] = last; got = True

    codes = _extract_var(js, "stockCodes")  # ["6005192", ...]
    if isinstance(codes, list) and codes:
        out["stock_codes"] = ",".join(str(c) for c in codes[:20])
        got = True

    mgrs = _extract_var(js, "Data_currentFundManager")  # [{name,...}]
    if isinstance(mgrs, list) and mgrs:
        names = [m.get("name", "") for m in mgrs if isinstance(m, dict) and m.get("name")]
        if names:
            out["managers"] = ",".join(names)
            got = True

    if not got:
        return None
    if out["date"] is None:
        out["date"] = datetime.now().strftime("%Y-%m-%d")
    return out


# ── 存储 ────────────────────────────────────────────────

def _save_nav(rows: list):
    conn = get_connection()
    try:
        conn.executemany(
            """INSERT INTO fund_nav_history (fund_code, date, nav, acc_nav, daily_return)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(fund_code, date) DO UPDATE SET
                 nav=excluded.nav, acc_nav=excluded.acc_nav, daily_return=excluded.daily_return""",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def _save_holdings(h: dict):
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO fund_holdings (fund_code, date, stock_ratio, bond_ratio, cash_ratio,
                                          stock_codes, managers, source, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'eastmoney', datetime('now'))
               ON CONFLICT(fund_code, date) DO UPDATE SET
                 stock_ratio=excluded.stock_ratio, bond_ratio=excluded.bond_ratio,
                 cash_ratio=excluded.cash_ratio, stock_codes=excluded.stock_codes,
                 managers=excluded.managers, updated_at=excluded.updated_at""",
            (h["fund_code"], h["date"], h["stock_ratio"], h["bond_ratio"], h["cash_ratio"],
             h["stock_codes"], h["managers"]),
        )
        conn.commit()
    finally:
        conn.close()
