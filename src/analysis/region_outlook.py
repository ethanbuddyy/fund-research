"""地区宏观机会判断系统

对美国、日本、德国、法国四个市场从三个维度打分：
  1. 宏观景气（0–4）：GDP 增速 + 趋势 + 通胀压力
  2. 市场动量（0–3）：指数近1年涨跌历史分位 + 均线位置
  3. 相对机会（0–3）：vs 美国 SP500 的超额收益（均值回归视角）

主入口：assess_region_outlook(fund_region) -> dict
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd

from ..utils.database import read_table

# ── 地区配置 ──────────────────────────────────────────────────
_REGION_CONFIG = {
    "美国": {"macro_region": "美国",  "symbol": "^GSPC",  "label_cn": "美国(SP500)"},
    "日本": {"macro_region": "日本",  "symbol": "^N225",  "label_cn": "日本(日经225)"},
    "德国": {"macro_region": "德国",  "symbol": "^GDAXI", "label_cn": "德国(DAX)"},
    "法国": {"macro_region": "法国",  "symbol": "^FCHI",  "label_cn": "法国(CAC40)"},
}

# 基金地区 → 分析地区映射（基金 region 字段可能与配置 key 不完全一致）
_FUND_REGION_MAP = {
    "美国": "美国",
    "日本": "日本",
    "德国": "德国",
    "法国": "法国",
    "欧洲": "德国",      # 泛欧洲用德国代理
    "欧元区": "德国",
    "亚洲": "日本",      # 泛亚洲用日本代理
    "全球": None,        # 全球 = 所有区域综合
}

_US_SYMBOL = "^GSPC"


def assess_region_outlook(fund_region: str) -> dict:
    """评估基金所在地区及4个对比地区的宏观机会。

    Args:
        fund_region: 基金地区，如 "欧洲"、"日本"、"德国" 等

    Returns:
        结构化区域机会报告 dict
    """
    gm_df    = read_table("global_macro")
    price_df = read_table("market_data")
    notes: list[str] = []

    # 计算所有4个地区的得分
    covered: dict[str, Optional[dict]] = {}
    for region_key, cfg in _REGION_CONFIG.items():
        result = _score_region(region_key, cfg, gm_df, price_df, notes)
        covered[region_key] = result

    # 排名（有数据的地区，按 total 降序）
    ranked = sorted(
        [(k, v["total"]) for k, v in covered.items() if v and v.get("total") is not None],
        key=lambda x: -x[1],
    )
    ranking = [k for k, _ in ranked]

    # 确定焦点地区
    focus_key = _FUND_REGION_MAP.get(fund_region)
    focus_data = covered.get(focus_key) if focus_key else None
    if focus_key and focus_data:
        focus = {
            "name": f"{fund_region}（{_REGION_CONFIG[focus_key]['label_cn']}代理）"
                    if fund_region != focus_key else _REGION_CONFIG[focus_key]["label_cn"],
            "score": focus_data["total"],
            "label": focus_data["label"],
            "summary": _build_summary(focus_key, focus_data, covered),
        }
    elif fund_region == "全球" or focus_key is None:
        # 全球基金：用4地区加权平均
        valid = [v for v in covered.values() if v]
        avg_score = round(sum(v["total"] for v in valid) / len(valid), 1) if valid else 5.0
        focus = {
            "name": "全球（4地区均值）",
            "score": avg_score,
            "label": _label(avg_score),
            "summary": f"4地区平均机会得分 {avg_score}/10，最强：{ranking[0] if ranking else '—'}，最弱：{ranking[-1] if ranking else '—'}。",
        }
    else:
        focus = {"name": fund_region, "score": None, "label": "数据不足", "summary": "该地区暂无宏观或价格数据。"}

    return {
        "fund_region":     fund_region,
        "covered_regions": covered,
        "ranking":         ranking,
        "focus_region":    focus,
        "data_notes":      notes,
    }


# ── 单地区评分 ────────────────────────────────────────────────

def _score_region(
    region_key: str,
    cfg: dict,
    gm_df: pd.DataFrame,
    price_df: pd.DataFrame,
    notes: list[str],
) -> Optional[dict]:
    macro_region = cfg["macro_region"]
    symbol       = cfg["symbol"]

    # ① 宏观景气
    macro_result = _macro_score(macro_region, gm_df)
    macro_s   = macro_result["score"]
    gdp_val   = macro_result.get("gdp_growth")
    infl_val  = macro_result.get("inflation")
    if macro_result.get("fallback"):
        notes.append(f"{region_key} 宏观数据不足，降级处理（{macro_result.get('fallback')}）")

    # ② 市场动量
    price_series = _load_price(symbol, price_df)
    if price_series is None or len(price_series) < 60:
        notes.append(f"{region_key} ({symbol}) 价格数据不足，动量维度设为中性")
        mom_s     = 1.5
        ret_1y    = None
        ret_3y    = None
        vs_200dma = None
    else:
        mom_result = _momentum_score(price_series)
        mom_s      = mom_result["score"]
        ret_1y     = mom_result.get("return_1y")
        ret_3y     = mom_result.get("return_3y")
        vs_200dma  = mom_result.get("vs_200dma")

    # ③ 相对机会 vs 美国
    if region_key == "美国":
        rel_s    = 1.5   # 美国是基准，给中性分
        vs_us_3y = 0.0
    else:
        us_series = _load_price(_US_SYMBOL, price_df)
        if price_series is None or us_series is None or len(price_series) < 60:
            rel_s    = 1.5
            vs_us_3y = None
        else:
            rel_result = _relative_score(price_series, us_series)
            rel_s    = rel_result["score"]
            vs_us_3y = rel_result.get("vs_us_3y")

    total = round(macro_s + mom_s + rel_s, 1)

    return {
        "macro_score":    round(macro_s, 2),
        "momentum_score": round(mom_s, 2),
        "relative_score": round(rel_s, 2),
        "total":          total,
        "label":          _label(total),
        "gdp_growth":     gdp_val,
        "inflation":      infl_val,
        "return_1y":      ret_1y,
        "return_3y":      ret_3y,
        "vs_200dma":      vs_200dma,
        "vs_us_3y":       vs_us_3y,
        "symbol":         symbol,
    }


# ── 维度一：宏观景气 ──────────────────────────────────────────

def _macro_score(region: str, gm_df: pd.DataFrame) -> dict:
    if gm_df.empty:
        return {"score": 2.0, "fallback": "无 global_macro 数据"}

    rdf = gm_df[gm_df["region"] == region]

    # 如果是法国且无数据，用欧元区代替
    fallback = None
    if rdf.empty and region == "法国":
        rdf = gm_df[gm_df["region"] == "欧元区"]
        fallback = "法国无数据，以欧元区代替"
    if rdf.empty:
        return {"score": 2.0, "fallback": f"{region} 无宏观数据"}

    def _latest(indicator: str):
        s = rdf[rdf["indicator"] == indicator].sort_values("date")
        if s.empty:
            return None, None
        latest = float(s.iloc[-1]["value"]) if pd.notna(s.iloc[-1]["value"]) else None
        prev   = float(s.iloc[-2]["value"]) if len(s) >= 2 and pd.notna(s.iloc[-2]["value"]) else None
        return latest, prev

    gdp, gdp_prev = _latest("gdp_growth")
    infl, _       = _latest("inflation")

    # GDP 增速得分（0–2）
    if gdp is None:
        gdp_s = 1.0
    elif gdp > 3.0:
        gdp_s = 2.0
    elif gdp > 1.0:
        gdp_s = 1.5
    elif gdp > 0.0:
        gdp_s = 1.0
    else:
        gdp_s = 0.0

    # 增速趋势（0–1）
    trend_s = 1.0 if (gdp is not None and gdp_prev is not None and gdp > gdp_prev) else 0.0

    # 通胀压力（0–1）
    if infl is None:
        infl_s = 0.5
    elif infl < 3.0:
        infl_s = 1.0
    elif infl < 5.0:
        infl_s = 0.5
    else:
        infl_s = 0.0

    return {
        "score":      gdp_s + trend_s + infl_s,
        "gdp_growth": gdp,
        "inflation":  infl,
        "fallback":   fallback,
    }


# ── 维度二：市场动量 ──────────────────────────────────────────

def _load_price(symbol: str, df: pd.DataFrame) -> Optional[pd.Series]:
    sub = df[df["symbol"] == symbol].copy()
    if sub.empty:
        return None
    sub["date"] = pd.to_datetime(sub["date"])
    return sub.sort_values("date").set_index("date")["close"].astype(float)


def _momentum_score(prices: pd.Series) -> dict:
    today_price = float(prices.iloc[-1])

    # 近1年、近3年收益
    def _ret(days: int) -> Optional[float]:
        cutoff = prices.index[-1] - pd.Timedelta(days=days)
        sub = prices[prices.index >= cutoff]
        if len(sub) < 5:
            return None
        return (today_price / float(sub.iloc[0]) - 1) * 100

    ret_1y = _ret(365)
    ret_3y = _ret(365 * 3)

    # 近1年涨幅在历史所有滚动1年窗口中的分位
    rets_1y_all = []
    for i in range(len(prices) - 252):
        r = (float(prices.iloc[i + 252]) / float(prices.iloc[i]) - 1) * 100
        rets_1y_all.append(r)

    if ret_1y is not None and rets_1y_all:
        pct = sum(1 for r in rets_1y_all if r <= ret_1y) / len(rets_1y_all)
        # 低分位（涨幅小）→ 高机会分；高分位（涨幅大）→ 低机会分
        if pct <= 0.25:
            mom_s = 2.0
        elif pct <= 0.50:
            mom_s = 1.5
        elif pct <= 0.75:
            mom_s = 0.75
        else:
            mom_s = 0.0
    else:
        mom_s = 1.0
        pct   = None

    # 价格 vs 200日均线
    ma200 = float(prices.tail(200).mean())
    vs_200dma = (today_price / ma200 - 1) * 100
    ma200_s = 1.0 if today_price < ma200 else 0.0

    return {
        "score":      mom_s + ma200_s,
        "return_1y":  round(ret_1y, 2) if ret_1y is not None else None,
        "return_3y":  round(ret_3y, 2) if ret_3y is not None else None,
        "vs_200dma":  round(vs_200dma, 2),
        "percentile": round(pct * 100, 1) if pct is not None else None,
    }


# ── 维度三：相对机会 vs 美国 ──────────────────────────────────

def _relative_score(region_prices: pd.Series, us_prices: pd.Series) -> dict:
    # 对齐日期
    common = region_prices.index.intersection(us_prices.index)
    if len(common) < 60:
        return {"score": 1.5, "vs_us_3y": None}

    rp = region_prices.loc[common]
    up = us_prices.loc[common]

    # 近1年、近3年各自收益
    def _period_ret(series: pd.Series, days: int) -> Optional[float]:
        cutoff = series.index[-1] - pd.Timedelta(days=days)
        sub = series[series.index >= cutoff]
        if len(sub) < 5:
            return None
        return (float(sub.iloc[-1]) / float(sub.iloc[0]) - 1) * 100

    reg_1y = _period_ret(rp, 365)
    us_1y  = _period_ret(up, 365)
    reg_3y = _period_ret(rp, 365 * 3)
    us_3y  = _period_ret(up, 365 * 3)

    vs_us_3y = (reg_3y - us_3y) if reg_3y is not None and us_3y is not None else None
    vs_us_1y = (reg_1y - us_1y) if reg_1y is not None and us_1y is not None else None

    # 历史所有滚动3年窗口的 vs_us 超额收益分布（用于 Z-score）
    excess_3y_all = []
    step = 21  # 月度步进（提高效率）
    window = min(252 * 3, len(rp) - 1)
    for i in range(0, len(rp) - window, step):
        r_ret = (float(rp.iloc[i + window]) / float(rp.iloc[i]) - 1) * 100
        u_ret = (float(up.iloc[i + window]) / float(up.iloc[i]) - 1) * 100
        excess_3y_all.append(r_ret - u_ret)

    # 相对机会分：3年超额收益越低于历史均值，均值回归潜力越大
    if vs_us_3y is not None and len(excess_3y_all) >= 5:
        mean_ex = float(np.mean(excess_3y_all))
        std_ex  = float(np.std(excess_3y_all)) or 1.0
        z_score = (vs_us_3y - mean_ex) / std_ex
        # z < -1.0（当前表现低于历史均值1个标准差）→ 均值回归机会大
        if z_score < -1.5:
            rel_s_3y = 2.0
        elif z_score < -0.5:
            rel_s_3y = 1.5
        elif z_score < 0.5:
            rel_s_3y = 1.0
        else:
            rel_s_3y = 0.0
    else:
        rel_s_3y = 1.0
        z_score  = None

    # 近1年相对美国
    rel_s_1y = 1.0 if (vs_us_1y is not None and vs_us_1y < 0) else 0.0

    return {
        "score":    rel_s_3y + rel_s_1y,
        "vs_us_3y": round(vs_us_3y, 1) if vs_us_3y is not None else None,
        "vs_us_1y": round(vs_us_1y, 1) if vs_us_1y is not None else None,
        "z_score":  round(z_score, 2) if z_score is not None else None,
    }


# ── 汇总摘要 ──────────────────────────────────────────────────

def _build_summary(focus_key: str, focus: dict, all_regions: dict) -> str:
    parts: list[str] = []

    gdp = focus.get("gdp_growth")
    infl = focus.get("inflation")
    ret1 = focus.get("return_1y")
    vs3  = focus.get("vs_us_3y")
    vs200 = focus.get("vs_200dma")
    label = focus.get("label", "—")
    total = focus.get("total", 0)

    # 宏观句
    if gdp is not None:
        infl_str = f"，通胀 {infl:.1f}%" if infl is not None else ""
        parts.append(f"{focus_key} 最新 GDP 增速 {gdp:.1f}%{infl_str}")

    # 动量句
    if ret1 is not None:
        us_data = all_regions.get("美国")
        us_ret1 = us_data.get("return_1y") if us_data else None
        if us_ret1 is not None and focus_key != "美国":
            diff = ret1 - us_ret1
            parts.append(f"近1年指数 {ret1:+.1f}%，较美国 {diff:+.1f}%")
        else:
            parts.append(f"近1年指数 {ret1:+.1f}%")

    # 相对机会句
    if vs3 is not None and focus_key != "美国":
        z = focus.get("z_score")
        z_str = f"（历史分位 Z={z:.1f}）" if z is not None else ""
        parts.append(f"3年相对美国超额 {vs3:+.1f}%{z_str}，均值回归{'潜力明显' if vs3 < -20 else '一般'}")

    if vs200 is not None:
        ma_str = "低于200日均线" if vs200 < 0 else f"高于200日均线 {vs200:.1f}%"
        parts.append(ma_str)

    summary = "；".join(parts) + f"。综合机会评分 {total}/10（{label}）。"
    return summary


def _label(score: float) -> str:
    if score >= 8.0: return "强势"
    if score >= 6.0: return "偏强"
    if score >= 4.0: return "中性"
    if score >= 2.0: return "偏弱"
    return "弱势"
