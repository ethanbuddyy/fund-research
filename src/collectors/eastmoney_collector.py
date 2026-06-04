"""天天基金(东方财富) pingzhongdata 直连采集器。

接口：https://fund.eastmoney.com/pingzhongdata/{code}.js （免费、无需Key、需Referer）
一次拿到：单位净值全历史、累计净值、资产配置(股/债/现金占比)、重仓股代码、
         基金经理详情、换手率、管理费/托管费分项。

用途：
  1) 用真实净值全历史覆盖 fund_nav_history；
  2) 资产配置/重仓股写入 fund_holdings；
  3) 基金经理详情写入 fund_manager；
  4) 换手率写入 fund_turnover；
  5) 管理费/托管费更新 fund_list.mgmt_fee / custody_fee。

非官方接口，东财改版可能导致字段变动；所有解析独立容错，失败即跳过。
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
    mgr_ok = 0
    turn_ok = 0
    nav_total = 0
    import time
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

            mgr_list = _parse_managers(js, code)
            if mgr_list:
                _save_managers(mgr_list)
                mgr_ok += 1

            turnover = _parse_turnover(js, code)
            if turnover:
                _save_turnover(turnover)
                turn_ok += 1

            fees = _parse_fee_split(js, code)
            if fees:
                _save_fee_split(fees)

        except Exception as e:
            print(f"[WARN] eastmoney {code} 失败: {e}")
            continue
        time.sleep(0.3)

    if nav_ok:
        provenance.record("fund", provenance.REAL, nav_total, f"eastmoney pingzhongdata({nav_ok}只)")
    if hold_ok:
        provenance.record("fund_holdings", provenance.REAL, hold_ok, "eastmoney pingzhongdata")
    elif not nav_ok:
        provenance.record("fund_holdings", provenance.PARTIAL, 0, "pingzhongdata 不可达")

    print(f"[OK] eastmoney: 净值 {nav_ok}只/{nav_total}条，持仓 {hold_ok}只，经理 {mgr_ok}只，换手率 {turn_ok}只")
    return {
        "funds": len(fund_codes), "nav_funds": nav_ok, "nav_rows": nav_total,
        "holding_funds": hold_ok, "manager_funds": mgr_ok, "turnover_funds": turn_ok,
    }


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


# ── 基金经理详情 ─────────────────────────────────────────────────

def _parse_managers(js: str, code: str) -> list[dict]:
    """解析 Data_currentFundManager → [{fund_code, manager_id, name, work_start_date, ...}]。"""
    raw = _extract_var(js, "Data_currentFundManager")
    if not isinstance(raw, list) or not raw:
        return []

    result = []
    for m in raw:
        if not isinstance(m, dict):
            continue
        name = m.get("name", "").strip()
        if not name:
            continue

        # power 字段：综合评分（avr = 0-100）；profit 字段：任期收益 vs 同类
        power = m.get("power") or {}
        avg_ann = _safe_float_str(power.get("avr_per_form") or power.get("avr"))
        # profit.series[0].data[0].y = 任期累计收益%，[1].y = 同类平均
        profit = m.get("profit") or {}
        tenure_ret = None
        try:
            tenure_ret = float(profit["series"][0]["data"][0]["y"])
        except (KeyError, IndexError, TypeError, ValueError):
            pass
        r1y = _safe_float_str(power.get("y1_per_form"))
        r3y = _safe_float_str(power.get("y3_per_form") or (tenure_ret if tenure_ret else None))
        r5y = _safe_float_str(power.get("y5_per_form"))

        # 在管基金列表
        cur_funds = m.get("currentFund") or []
        managed = ",".join(
            f"{f.get('id','')}/{f.get('name','')}"
            for f in cur_funds if isinstance(f, dict)
        )[:500]

        result.append({
            "fund_code":            code,
            "manager_id":           str(m.get("id", "")),
            "name":                 name,
            "work_start_date":      m.get("workTime", ""),
            "total_assets_managed": str(m.get("fundSize", "")),
            # avg_annual_return 字段：复用存东财综合评分（0-100）
            "avg_annual_return":    _safe_float_str(power.get("avr")),
            "return_1y":            r1y,
            # return_3y 字段：复用存任期累计收益%
            "return_3y":            tenure_ret,
            "return_5y":            r5y,
            "managed_funds":        managed,
            "description":          (m.get("description") or "")[:300],
        })
    return result


def _safe_float_str(v) -> float | None:
    """将字符串或数字安全转为 float，失败返回 None。"""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _save_managers(mgr_list: list[dict]):
    conn = get_connection()
    try:
        for m in mgr_list:
            conn.execute(
                """INSERT INTO fund_manager
                   (fund_code, manager_id, name, work_start_date, total_assets_managed,
                    avg_annual_return, return_1y, return_3y, return_5y,
                    managed_funds, description, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
                   ON CONFLICT(fund_code, name) DO UPDATE SET
                     manager_id=excluded.manager_id,
                     work_start_date=excluded.work_start_date,
                     total_assets_managed=excluded.total_assets_managed,
                     avg_annual_return=excluded.avg_annual_return,
                     return_1y=excluded.return_1y,
                     return_3y=excluded.return_3y,
                     return_5y=excluded.return_5y,
                     managed_funds=excluded.managed_funds,
                     description=excluded.description,
                     updated_at=excluded.updated_at""",
                (m["fund_code"], m["manager_id"], m["name"], m["work_start_date"],
                 m["total_assets_managed"], m["avg_annual_return"],
                 m["return_1y"], m["return_3y"], m["return_5y"],
                 m["managed_funds"], m["description"]),
            )
        conn.commit()
    finally:
        conn.close()


# ── 换手率 ───────────────────────────────────────────────────────

def _parse_turnover(js: str, code: str) -> list[dict]:
    """尝试解析 hsltList 换手率数据 → [{fund_code, year, turnover_rate}]。

    pingzhongdata 中 hsltList 格式通常为 [[年份字符串, 换手率值], ...]
    或 [{year:..., value:...}] 形式，容错两种格式。
    """
    raw = _extract_var(js, "hsltList")
    if not isinstance(raw, list) or not raw:
        return []

    result = []
    for item in raw:
        try:
            if isinstance(item, list) and len(item) >= 2:
                year_raw, rate_raw = item[0], item[1]
            elif isinstance(item, dict):
                year_raw = item.get("year") or item.get("x") or item.get("name")
                rate_raw = item.get("value") or item.get("y")
            else:
                continue

            year = int(str(year_raw)[:4])
            rate = float(rate_raw)
            if 1990 <= year <= 2100 and rate >= 0:
                result.append({"fund_code": code, "year": year, "turnover_rate": round(rate, 4)})
        except (TypeError, ValueError, IndexError):
            continue
    return result


def _save_turnover(turnover: list[dict]):
    conn = get_connection()
    try:
        for t in turnover:
            conn.execute(
                """INSERT INTO fund_turnover (fund_code, year, turnover_rate, source, updated_at)
                   VALUES (?, ?, ?, 'eastmoney', datetime('now'))
                   ON CONFLICT(fund_code, year) DO UPDATE SET
                     turnover_rate=excluded.turnover_rate,
                     source='eastmoney',
                     updated_at=excluded.updated_at""",
                (t["fund_code"], t["year"], t["turnover_rate"]),
            )
        conn.commit()
    finally:
        conn.close()


# ── 管理费/托管费分项 ─────────────────────────────────────────────

def _parse_fee_split(js: str, code: str) -> dict | None:
    """尝试从 feeInfo / Data_feeInfo 变量解析管理费率和托管费率。

    东财 pingzhongdata 中费率信息格式不统一，尝试多个变量名，静默失败。
    返回 {fund_code, mgmt_fee, custody_fee} 或 None。
    """
    for var_name in ("feeInfo", "Data_feeInfo", "Data_managerFee"):
        raw = _extract_var(js, var_name)
        if not isinstance(raw, dict):
            continue

        mgmt = None
        custody = None

        # 尝试常见字段名
        for key in ("manageFee", "manage_fee", "管理费率"):
            v = raw.get(key)
            if v is not None:
                mgmt = _parse_rate_str(v)
                break

        for key in ("trustFee", "trust_fee", "托管费率"):
            v = raw.get(key)
            if v is not None:
                custody = _parse_rate_str(v)
                break

        if mgmt is not None or custody is not None:
            return {"fund_code": code, "mgmt_fee": mgmt, "custody_fee": custody}

    # 尝试从 JS 文本正则直接匹配（兜底）
    for pattern, fee_type in [
        (r"管理费率[：:]\s*([\d.]+)%", "mgmt"),
        (r"托管费率[：:]\s*([\d.]+)%", "custody"),
    ]:
        m = re.search(pattern, js)
        if m:
            val = float(m.group(1)) / 100
            if fee_type == "mgmt":
                return {"fund_code": code, "mgmt_fee": val, "custody_fee": None}
            else:
                return {"fund_code": code, "mgmt_fee": None, "custody_fee": val}

    return None


def _parse_rate_str(v) -> float | None:
    """将 '0.75%' 或 0.0075 或 '0.0075' 转为小数形式。"""
    if v is None:
        return None
    try:
        s = str(v).strip().rstrip("%")
        f = float(s)
        # 如果值 > 0.1 说明传的是百分比形式（如 0.75），需除以 100
        return f / 100 if f > 0.1 else f
    except (TypeError, ValueError):
        return None


def _save_fee_split(fees: dict):
    """将管理费/托管费写回 fund_list 表（仅更新非 NULL 值）。"""
    code = fees.get("fund_code")
    mgmt = fees.get("mgmt_fee")
    custody = fees.get("custody_fee")
    if not code or (mgmt is None and custody is None):
        return

    conn = get_connection()
    try:
        if mgmt is not None and custody is not None:
            conn.execute(
                "UPDATE fund_list SET mgmt_fee=?, custody_fee=? WHERE fund_code=?",
                (mgmt, custody, code),
            )
        elif mgmt is not None:
            conn.execute("UPDATE fund_list SET mgmt_fee=? WHERE fund_code=?", (mgmt, code))
        else:
            conn.execute("UPDATE fund_list SET custody_fee=? WHERE fund_code=?", (custody, code))
        conn.commit()
    finally:
        conn.close()
