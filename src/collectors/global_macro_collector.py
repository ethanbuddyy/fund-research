"""采集全球宏观数据（World Bank + OECD，均免费、无需 API Key）。

补足「全球QDII却只有美国宏观」的缺口：覆盖日/德/欧/英/中等基金所在区域的
GDP增长、通胀、失业（World Bank，年度，稳定可靠），以及 OECD 综合领先指标
CLI（先行指标，尽力而为；其 SDMX 端点格式偶有变动，失败则安全跳过）。

遵循全项目一致的「失败即降级 + provenance 标记」策略。
"""
import pandas as pd
from datetime import datetime
from ..utils.config import load_config
from ..utils.database import get_connection
from ..utils import provenance

_WB_BASE = "https://api.worldbank.org/v2/country/{wb}/indicator/{ind}"
_HEADERS = {"User-Agent": "fund-research/1.0"}


def collect_global_macro() -> dict:
    """抓取各区域 World Bank 指标（主力）+ OECD CLI（尽力而为），存入 global_macro。"""
    cfg = load_config()
    gm_cfg = cfg.get("global_macro", {})
    regions = gm_cfg.get("regions", [])
    wb_inds = gm_cfg.get("worldbank_indicators", {})

    if not regions or not wb_inds:
        provenance.record("global_macro", provenance.MOCK, 0, "缺少 global_macro 配置")
        return {}

    try:
        import requests
    except ImportError:
        rows = _mock_global(regions, wb_inds)
        _save(rows)
        provenance.record("global_macro", provenance.MOCK, len(rows), "requests 未安装")
        return {"rows": len(rows)}

    rows = []
    start, end = datetime.now().year - 6, datetime.now().year

    # ① World Bank（主力）
    wb_ok = 0
    for region in regions:
        wb_code = region.get("wb")
        if not wb_code:
            continue
        for ind_key, ind_id in wb_inds.items():
            series = _fetch_worldbank(requests, wb_code, ind_id, start, end)
            for date, val in series:
                rows.append((region["name"], ind_key, date, val, "worldbank"))
            if series:
                wb_ok += 1

    # ② OECD CLI（先行指标，尽力而为）
    oecd_ok = 0
    for region in regions:
        oecd_code = region.get("oecd")
        if not oecd_code:
            continue
        series = _fetch_oecd_cli(requests, oecd_code)
        for date, val in series:
            rows.append((region["name"], "cli", date, val, "oecd"))
        if series:
            oecd_ok += 1

    if rows:
        _save(rows)
        mode = provenance.REAL if wb_ok else provenance.PARTIAL
        detail = f"WorldBank {wb_ok} 序列" + (f" + OECD CLI {oecd_ok} 区域" if oecd_ok else "（OECD CLI 不可用）")
        provenance.record("global_macro", mode, len(rows), detail)
        print(f"[OK] 全球宏观: {len(rows)} 条（{detail}）")
        return {"rows": len(rows), "wb_series": wb_ok, "oecd_regions": oecd_ok}

    # 全失败 → 模拟降级
    rows = _mock_global(regions, wb_inds)
    _save(rows)
    provenance.record("global_macro", provenance.MOCK, len(rows), "World Bank/OECD 均不可达")
    print("[WARN] 全球宏观数据源不可达，使用模拟数据")
    return {"rows": len(rows)}


def _fetch_worldbank(requests, wb_code: str, indicator: str, start: int, end: int) -> list:
    """返回 [(date 'YYYY', value), ...]（按年份升序，跳过空值）。"""
    url = _WB_BASE.format(wb=wb_code, ind=indicator)
    params = {"format": "json", "date": f"{start}:{end}", "per_page": 100}
    try:
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[WARN] WorldBank {wb_code}/{indicator} 失败: {e}")
        return []
    # World Bank 返回 [metadata, [observations]]
    if not (isinstance(data, list) and len(data) >= 2 and data[1]):
        return []
    out = []
    for obs in data[1]:
        v = obs.get("value")
        d = obs.get("date")
        if v is not None and d is not None:
            try:
                out.append((str(d), float(v)))
            except (TypeError, ValueError):
                continue
    return sorted(out, key=lambda x: x[0])


def _fetch_oecd_cli(requests, oecd_code: str) -> list:
    """OECD 综合领先指标 CLI（尽力而为）。

    OECD 新版 SDMX-JSON 端点格式偶有调整；任何异常都安全返回空，由 World Bank 兜底。
    返回 [(date 'YYYY-MM', value), ...]。
    """
    # OECD SDD STES 数据流的 CLI（幅度调整）。端点若变动，此处失败不影响主流程。
    url = (
        "https://sdmx.oecd.org/public/rest/data/"
        f"OECD.SDD.STES,DSD_STES@DF_CLI,/{oecd_code}.M.LI...AA...H"
    )
    params = {"startPeriod": f"{datetime.now().year - 2}-01",
              "dimensionAtObservation": "AllDimensions", "format": "jsondata"}
    try:
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=15)
        if resp.status_code != 200:
            return []
        data = resp.json()
        obs = data["data"]["dataSets"][0]["observations"]
        dims = data["data"]["structure"]["dimensions"]["observation"]
        time_dim = next(d for d in dims if d["id"] in ("TIME_PERIOD", "TIME"))
        periods = [v["id"] for v in time_dim["values"]]
        out = []
        for key, val in obs.items():
            idx = int(key.split(":")[-1])
            if 0 <= idx < len(periods) and val and val[0] is not None:
                out.append((periods[idx], float(val[0])))
        return sorted(out, key=lambda x: x[0])
    except Exception:
        return []  # OECD 端点不可用时静默跳过，World Bank 已兜底


def _mock_global(regions: list, wb_inds: dict) -> list:
    """离线/失败时的模拟值（明确标记 mock，不可用于决策）。"""
    import numpy as np
    np.random.seed(int(datetime.now().strftime("%Y%m%d")))
    year = str(datetime.now().year - 1)
    base = {"gdp_growth": 2.0, "inflation": 2.5, "unemployment": 4.0}
    rows = []
    for region in regions:
        for ind_key in wb_inds:
            v = base.get(ind_key, 2.0) + float(np.random.randn() * 0.5)
            rows.append((region["name"], ind_key, year, round(v, 2), "mock"))
    return rows


def _save(rows: list):
    if not rows:
        return
    conn = get_connection()
    try:
        conn.executemany(
            """INSERT INTO global_macro (region, indicator, date, value, source, updated_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(region, indicator, date) DO UPDATE SET
                 value=excluded.value, source=excluded.source, updated_at=excluded.updated_at""",
            rows,
        )
        conn.commit()
    finally:
        conn.close()
