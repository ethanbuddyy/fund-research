"""申购/赎回/管理/托管费采集器（东财 jbgk 页面）。

接口：https://fundf10.eastmoney.com/jbgk_{code}.html
数据：管理费率、托管费率、最高申购费率、最高赎回费率（均为标准费率）。

写入：
  fund_list.mgmt_fee / custody_fee  ← 管理费 + 托管费
  fund_fees                          ← 申购费 + 赎回费记录
"""
from typing import Optional
import re
import time
from ..utils.database import get_connection
from ..utils.fund_universe import CORE_QDII_FUNDS
from ..utils import provenance

_JBGK_URL = "https://fundf10.eastmoney.com/jbgk_{code}.html"
_HEADERS = {
    "Referer": "https://fundf10.eastmoney.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


def collect_fund_fees(fund_codes: Optional[list] = None) -> dict:
    """采集各基金费率，写入 fund_list 和 fund_fees 表。"""
    try:
        import requests
    except ImportError:
        print("[WARN] requests 未安装，跳过费率采集")
        return {"funds": 0}

    if fund_codes is None:
        fund_codes = [f["fund_code"] for f in CORE_QDII_FUNDS]

    ok = 0
    for code in fund_codes:
        try:
            fees = _fetch_fees(requests, code)
            if fees:
                _save_to_fund_list(code, fees)
                _save_to_fund_fees(code, fees)
                ok += 1
        except Exception as e:
            print(f"[WARN] 费率采集 {code} 失败: {e}")
        time.sleep(0.2)

    print(f"[OK] 申购/赎回/管理/托管费采集完成：{ok}/{len(fund_codes)} 只")
    if ok:
        provenance.record("fund_fees", provenance.REAL, ok, f"eastmoney jbgk({ok}只)")
    return {"funds": len(fund_codes), "ok": ok}


def _fetch_fees(requests_mod, code: str) -> dict | None:
    """抓取 jbgk 页面，解析各项费率。返回 dict 或 None（若全部解析失败）。"""
    resp = requests_mod.get(_JBGK_URL.format(code=code), headers=_HEADERS, timeout=15)
    if resp.status_code != 200:
        return None
    html = resp.text

    mgmt    = _extract_rate(html, "管理费率")
    custody = _extract_rate(html, "托管费率")
    # 申购费：优先取划线价后的优惠价，退而取标准价
    purchase = _extract_purchase_rate(html)
    redeem   = _extract_rate(html, "赎回费率")

    if all(v is None for v in (mgmt, custody, purchase, redeem)):
        return None

    return {
        "mgmt_fee":    mgmt,
        "custody_fee": custody,
        "purchase":    purchase,
        "redeem":      redeem,
    }


def _extract_rate(html: str, label: str) -> float | None:
    """从 '<th>管理费率</th><td>1.20%（每年）</td>' 提取数值（小数形式）。"""
    m = re.search(re.escape(label) + r'</th><td[^>]*>([\d.]+)%', html)
    if m:
        return float(m.group(1)) / 100
    return None


def _extract_purchase_rate(html: str) -> float | None:
    """提取最高申购费率（优先取未划线的实际费率，其次取标准费率）。"""
    # 优惠费率（划线后仍有的数字）：<span style="...line-through...">1.50%</span><em>1.00%</em>
    m = re.search(r'最高申购费率</th><td[^>]*>.*?<em>([\d.]+)%</em>', html, re.S)
    if m:
        return float(m.group(1)) / 100

    # 划线价（标准费率）
    m = re.search(r'最高申购费率</th><td[^>]*>.*?>([\d.]+)%', html, re.S)
    if m:
        return float(m.group(1)) / 100

    return None


def _save_to_fund_list(code: str, fees: dict):
    """将管理费/托管费写回 fund_list，同时更新 expense_ratio（若 DB 为 NULL）。"""
    mgmt    = fees.get("mgmt_fee")
    custody = fees.get("custody_fee")
    if mgmt is None and custody is None:
        return

    conn = get_connection()
    try:
        if mgmt is not None and custody is not None:
            conn.execute(
                """UPDATE fund_list SET mgmt_fee=?, custody_fee=?,
                   expense_ratio=COALESCE(expense_ratio, ?)
                   WHERE fund_code=?""",
                (mgmt, custody, round(mgmt + custody, 6), code),
            )
        elif mgmt is not None:
            conn.execute(
                "UPDATE fund_list SET mgmt_fee=? WHERE fund_code=?",
                (mgmt, code),
            )
        else:
            conn.execute(
                "UPDATE fund_list SET custody_fee=? WHERE fund_code=?",
                (custody, code),
            )
        conn.commit()
    finally:
        conn.close()


def _save_to_fund_fees(code: str, fees: dict):
    """将申购/赎回费写入 fund_fees 表。"""
    rows = []
    if fees.get("purchase") is not None:
        rows.append({
            "fund_code": code, "fee_type": "purchase",
            "amount_min": None, "amount_max": None,
            "rate": fees["purchase"],
            "rate_desc": f"最高申购费率 {fees['purchase']*100:.2f}%（标准/优惠，直销渠道）",
        })
    if fees.get("redeem") is not None:
        rows.append({
            "fund_code": code, "fee_type": "redemption",
            "amount_min": None, "amount_max": None,
            "rate": fees["redeem"],
            "rate_desc": f"最高赎回费率 {fees['redeem']*100:.2f}%",
        })
    if not rows:
        return

    conn = get_connection()
    try:
        conn.execute("DELETE FROM fund_fees WHERE fund_code=?", (code,))
        conn.executemany(
            """INSERT INTO fund_fees
               (fund_code, fee_type, amount_min, amount_max, rate, rate_desc, updated_at)
               VALUES (?,?,?,?,?,?,datetime('now'))""",
            [(r["fund_code"], r["fee_type"], r["amount_min"],
              r["amount_max"], r["rate"], r["rate_desc"]) for r in rows],
        )
        conn.commit()
    finally:
        conn.close()
