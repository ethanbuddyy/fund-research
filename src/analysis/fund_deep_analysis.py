"""单基金综合研判引擎

将「分析拆解基金方法论」和「基金量化分析评分框架」两份文档融合进系统，
形成 7 维 100 分制评分 + 一票否决条件 + 配置结论的完整研判链路。

主入口：analyze_fund(fund_code, market_signal) -> dict
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

from ..utils.database import read_table
from ..utils.config import load_config
from ..utils.fund_universe import (
    classify_asset_class,
    infer_region,
    EXPENSE_RATIO_BY_CODE,
    BENCHMARK_BY_CODE,
    REGION_BY_CODE,
)
from ..domain.scoring import consistency_score

RF_ANNUAL = 0.02          # 无风险利率假设
TRADING_DAYS = 252


# ─────────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────────

def analyze_fund(fund_code: str, market_signal: dict | None = None) -> dict:
    """7 维 100 分制单基金综合研判。

    Args:
        fund_code:     基金代码，如 "513100"
        market_signal: 市场信号 dict（可选，用于组合适配评估）

    Returns:
        结构化研判报告 dict
    """
    fund_info   = _load_fund_info(fund_code)
    perf        = _load_performance(fund_code)
    holdings    = _load_holdings(fund_code)
    nav_series, sp500_prices = _load_nav_and_market(fund_code)

    adv  = _compute_advanced(nav_series, sp500_prices, perf)
    peer = _load_peer_context(fund_info.get("asset_class", "broad_equity"))

    s_perf   = _score_performance(perf, adv, peer)
    s_risk   = _score_risk(perf, adv, peer)
    s_mgr    = _score_manager(fund_info, adv)
    s_strat  = _score_strategy(fund_info, holdings)
    s_attr   = _score_attribution(adv)
    s_struct = _score_structure(fund_info)
    s_cost   = _score_cost(fund_info, peer)

    total = sum(s["score"] for s in [s_perf, s_risk, s_mgr, s_strat, s_attr, s_struct, s_cost])

    vetoes = _check_vetoes(fund_info, perf, adv, peer)
    conclusion = _build_conclusion(total, vetoes, fund_info, adv, market_signal)

    # 地区宏观机会评估（美/日/德/法 四地区对比）
    try:
        from .region_outlook import assess_region_outlook
        region_outlook = assess_region_outlook(fund_info.get("region", ""))
    except Exception:
        region_outlook = None

    return {
        "fund_code":        fund_code,
        "fund_info":        fund_info,
        "performance":      perf,
        "holdings":         holdings,
        "advanced_metrics": adv,
        "peer_context":     peer,
        "region_outlook":   region_outlook,
        "scores": {
            "performance": s_perf,
            "risk":        s_risk,
            "manager":     s_mgr,
            "strategy":    s_strat,
            "attribution": s_attr,
            "structure":   s_struct,
            "cost":        s_cost,
            "total":       round(total, 1),
        },
        "vetoes":     vetoes,
        "conclusion": conclusion,
    }


# ─────────────────────────────────────────────────────────────
# Step 1 — 数据加载
# ─────────────────────────────────────────────────────────────

def _load_fund_info(fund_code: str) -> dict:
    fl = read_table("fund_list")
    row = fl[fl["fund_code"].astype(str) == fund_code]
    if row.empty:
        # 尝试从 universe 字典补充
        info: dict = {
            "fund_code":    fund_code,
            "fund_name":    fund_code,
            "fund_type":    "",
            "benchmark":    BENCHMARK_BY_CODE.get(fund_code, ""),
            "inception_date": None,
            "expense_ratio": None,   # 不用静态库，由 _refresh_expense_ratio 实时更新
            "total_assets": None,
            "in_db":        False,
        }
    else:
        r = row.iloc[0].to_dict()
        info = {
            "fund_code":     fund_code,
            "fund_name":     str(r.get("fund_name") or fund_code),
            "fund_type":     str(r.get("fund_type") or ""),
            "benchmark":     str(r.get("benchmark") or BENCHMARK_BY_CODE.get(fund_code, "")),
            "inception_date": r.get("inception_date"),
            "expense_ratio": _safe_float(r.get("expense_ratio")),   # 只用 DB 实时值
            "total_assets":  _safe_float(r.get("total_assets")),
            "in_db":         True,
        }

    # 衍生字段
    info["asset_class"] = classify_asset_class(
        fund_code=fund_code,
        fund_type=info["fund_type"],
        fund_name=info["fund_name"],
        benchmark=info["benchmark"],
    )
    info["region"] = REGION_BY_CODE.get(fund_code) or infer_region(info["fund_name"], info["benchmark"])
    info["tenure_years"] = _compute_tenure(info.get("inception_date"))
    return info


def _load_performance(fund_code: str) -> dict:
    df = read_table("fund_performance", "fund_code = ?", (fund_code,))
    if df.empty:
        return {}
    r = df.iloc[0].to_dict()
    return {k: _safe_float(v) for k, v in r.items() if k != "fund_code"}


def _load_holdings(fund_code: str) -> dict:
    df = read_table("fund_holdings", "fund_code = ? ORDER BY date DESC LIMIT 1", (fund_code,))
    if df.empty:
        return {}
    r = df.iloc[0].to_dict()
    return {
        "stock_ratio": _safe_float(r.get("stock_ratio")),
        "bond_ratio":  _safe_float(r.get("bond_ratio")),
        "cash_ratio":  _safe_float(r.get("cash_ratio")),
        "stock_codes": r.get("stock_codes", ""),
        "date":        r.get("date"),
    }


def _load_nav_and_market(fund_code: str) -> tuple[pd.Series, pd.Series]:
    """返回基金月度 NAV 序列和 SP500 月度价格序列（对齐）。"""
    nav_df = read_table("fund_nav_history", "fund_code = ? ORDER BY date", (fund_code,))
    sp_df  = read_table("market_data", "symbol = ? ORDER BY date", ("^GSPC",))

    empty = pd.Series(dtype=float), pd.Series(dtype=float)
    if nav_df.empty or sp_df.empty:
        return empty

    try:
        nav_df["date"] = pd.to_datetime(nav_df["date"])
        sp_df["date"]  = pd.to_datetime(sp_df["date"])
        nav_m = nav_df.set_index("date")["nav"].astype(float).resample("ME").last().dropna()
        sp_m  = sp_df.set_index("date")["close"].astype(float).resample("ME").last().dropna()
        common = nav_m.index.intersection(sp_m.index)
        if len(common) < 12:
            return empty
        return nav_m.loc[common], sp_m.loc[common]
    except Exception:
        return empty


# ─────────────────────────────────────────────────────────────
# Step 2 — 高级指标计算（纯函数）
# ─────────────────────────────────────────────────────────────

def _compute_advanced(
    nav: pd.Series,
    sp500: pd.Series,
    perf: dict,
) -> dict:
    """从月度 NAV 和 SP500 序列计算高级指标。"""
    result: dict = {
        "alpha_annual":    None,
        "beta":            None,
        "r_squared":       None,
        "information_ratio": None,
        "downside_capture":  None,
        "rolling_win_rate":  None,
        "calmar_ratio":      None,
        "data_months":       0,
    }
    if nav.empty or sp500.empty or len(nav) < 12:
        return result

    fund_ret  = nav.pct_change().dropna()
    mkt_ret   = sp500.pct_change().dropna()
    common    = fund_ret.index.intersection(mkt_ret.index)
    if len(common) < 12:
        return result

    f = fund_ret.loc[common].values
    m = mkt_ret.loc[common].values
    result["data_months"] = len(common)

    # OLS: 月度 alpha + beta
    alpha_m, beta, r2 = _ols(f, m)
    result["alpha_annual"] = round(alpha_m * 12 * 100, 2)  # 年化 %
    result["beta"]         = round(beta, 3)
    result["r_squared"]    = round(r2, 3)

    # 信息比率（月度超额收益 → 年化）
    excess = f - m
    result["information_ratio"] = round(_ir(excess), 3)

    # 下行捕获率
    result["downside_capture"] = round(_downside_capture(f, m), 3)

    # 滚动 3 年胜率（月度，36 期窗口）
    result["rolling_win_rate"] = round(_rolling_win_rate(fund_ret.loc[common], mkt_ret.loc[common]), 3)

    # 卡玛比率（从 fund_performance 取）
    ann_r  = _safe_float(perf.get("annualized_return"))
    max_dd = _safe_float(perf.get("max_drawdown"))
    if ann_r is not None and max_dd is not None and max_dd < 0:
        result["calmar_ratio"] = round(_calmar(ann_r, max_dd), 3)

    return result


def _ols(y: np.ndarray, x: np.ndarray) -> tuple[float, float, float]:
    """OLS: y = alpha + beta·x。返回 (alpha, beta, R²)。"""
    x_bar, y_bar = x.mean(), y.mean()
    denom = ((x - x_bar) ** 2).sum()
    if denom == 0:
        return 0.0, 1.0, 0.0
    beta  = ((x - x_bar) * (y - y_bar)).sum() / denom
    alpha = y_bar - beta * x_bar
    y_hat = alpha + beta * x
    ss_res = ((y - y_hat) ** 2).sum()
    ss_tot = ((y - y_bar) ** 2).sum()
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return float(alpha), float(beta), float(r2)


def _ir(excess: np.ndarray) -> float:
    """信息比率 = 年化超额均值 / 年化超额标准差。"""
    std = excess.std()
    if std == 0 or math.isnan(std):
        return 0.0
    return float(excess.mean() * 12 / (std * math.sqrt(12)))


def _downside_capture(fund_ret: np.ndarray, mkt_ret: np.ndarray) -> float:
    """下行捕获率（<1 越好）。"""
    down = mkt_ret < 0
    if down.sum() == 0:
        return 0.0
    mkt_down_avg  = mkt_ret[down].mean()
    fund_down_avg = fund_ret[down].mean()
    if abs(mkt_down_avg) < 1e-9:
        return 1.0
    return float(fund_down_avg / mkt_down_avg)


def _rolling_win_rate(fund_ret: pd.Series, mkt_ret: pd.Series, window: int = 36) -> float:
    """任意月末滚动 window 期，基金累计收益跑赢市场的比例。"""
    n = len(fund_ret)
    if n < window:
        return float("nan")
    wins = 0
    count = 0
    for i in range(n - window + 1):
        fc = (1 + fund_ret.iloc[i:i+window]).prod() - 1
        mc = (1 + mkt_ret.iloc[i:i+window]).prod() - 1
        wins  += 1 if fc > mc else 0
        count += 1
    return float(wins / count) if count > 0 else float("nan")


def _calmar(ann_return: float, max_drawdown: float) -> float:
    """卡玛比率 = 年化收益 / abs(最大回撤)。两者均为百分比形式。"""
    if max_drawdown >= 0:
        return float("nan")
    return float(ann_return / abs(max_drawdown))


# ─────────────────────────────────────────────────────────────
# Step 3 — 同类对比
# ─────────────────────────────────────────────────────────────

def _load_peer_context(asset_class: str) -> dict:
    """加载同类基金统计（中位数 + 均值 + 分位数）。"""
    perf_df = read_table("fund_performance")
    fl_df   = read_table("fund_list")
    if perf_df.empty or fl_df.empty:
        return {"asset_class": asset_class, "peer_count": 0, "stats": {}}

    merged = perf_df.merge(
        fl_df[["fund_code", "fund_type", "fund_name", "benchmark"]],
        on="fund_code", how="left",
    )
    merged["asset_class"] = merged.apply(
        lambda r: classify_asset_class(
            str(r["fund_code"]), str(r.get("fund_type", "")),
            str(r.get("fund_name", "")), str(r.get("benchmark", "")),
        ), axis=1,
    )
    peers = merged[merged["asset_class"] == asset_class]
    if len(peers) < 3:
        peers = merged

    stats: dict = {}
    for col in ["return_3y", "return_5y", "annualized_return",
                "sharpe_ratio", "max_drawdown", "volatility"]:
        vals = peers[col].dropna()
        if len(vals) >= 2:
            stats[col] = {
                "median": float(vals.median()),
                "p25":    float(vals.quantile(0.25)),
                "p75":    float(vals.quantile(0.75)),
                "mean":   float(vals.mean()),
                "count":  int(len(vals)),
            }
    expense_vals = fl_df["expense_ratio"].dropna()
    if len(expense_vals) >= 2:
        stats["expense_ratio"] = {
            "median": float(expense_vals.median()),
            "p25":    float(expense_vals.quantile(0.25)),
            "p75":    float(expense_vals.quantile(0.75)),
        }

    return {"asset_class": asset_class, "peer_count": len(peers), "stats": stats}


# ─────────────────────────────────────────────────────────────
# Step 4 — 7 维评分
# ─────────────────────────────────────────────────────────────
# 每维返回 {"score": float, "max": int, "details": {...}}

def _score_performance(perf: dict, adv: dict, peer: dict) -> dict:
    """维度一：长期业绩质量（满分 20）"""
    details: dict = {}
    score = 0.0
    ps = peer.get("stats", {})

    # ① 近3年年化收益 vs 同类中位数（4分）
    r3y = perf.get("return_3y")
    p_r3 = ps.get("return_3y", {})
    if r3y is not None and p_r3:
        med = p_r3["median"]
        ann_r3 = _annualize_pct(r3y, 3)
        ann_med = _annualize_pct(med, 3)
        diff = ann_r3 - ann_med
        s = 4 if diff >= 3 else 3 if diff >= 1 else 2 if diff >= 0 else 1 if diff >= -2 else 0
        score += s
        details["return_3y"] = {"raw": ann_r3, "peer_median": ann_med, "diff": diff, "score": s, "max": 4, "coverage": "COMPUTED"}
    else:
        score += 2  # 中性默认
        details["return_3y"] = {"score": 2, "max": 4, "coverage": "UNAVAILABLE"}

    # ② 近5年年化收益 vs 同类中位数（5分）
    r5y = perf.get("return_5y")
    p_r5 = ps.get("return_5y", {})
    if r5y is not None and p_r5:
        ann_r5 = _annualize_pct(r5y, 5)
        ann_med5 = _annualize_pct(p_r5["median"], 5)
        diff5 = ann_r5 - ann_med5
        s = 5 if diff5 >= 3 else 4 if diff5 >= 1 else 3 if diff5 >= 0 else 1 if diff5 >= -2 else 0
        score += s
        details["return_5y"] = {"raw": ann_r5, "peer_median": ann_med5, "diff": diff5, "score": s, "max": 5, "coverage": "COMPUTED"}
    else:
        score += 2.5
        details["return_5y"] = {"score": 2.5, "max": 5, "coverage": "UNAVAILABLE"}

    # ③ 相对SP500年化超额收益（5分）— alpha 代理
    alpha = adv.get("alpha_annual")
    if alpha is not None:
        s = 5 if alpha >= 5 else 4 if alpha >= 3 else 3 if alpha >= 1 else 2 if alpha >= 0 else 0
        score += s
        details["excess_return"] = {"alpha_annual_pct": alpha, "score": s, "max": 5, "coverage": "COMPUTED"}
    else:
        score += 2.5
        details["excess_return"] = {"score": 2.5, "max": 5, "coverage": "UNAVAILABLE"}

    # ④ 滚动3年胜率（4分）
    rwr = adv.get("rolling_win_rate")
    if rwr is not None and not math.isnan(rwr):
        s = 4 if rwr >= 0.70 else 3 if rwr >= 0.60 else 2 if rwr >= 0.50 else 1 if rwr >= 0.40 else 0
        score += s
        details["rolling_win_rate"] = {"value": rwr, "score": s, "max": 4, "coverage": "COMPUTED"}
    else:
        score += 2
        details["rolling_win_rate"] = {"score": 2, "max": 4, "coverage": "UNAVAILABLE"}

    # ⑤ 年度收益稳定性（2分）— 用 consistency_score 代理
    ann_r  = perf.get("annualized_return")
    r1y    = perf.get("return_1y")
    r3y_v  = perf.get("return_3y")
    if ann_r is not None:
        consist = consistency_score([
            _annualize_pct(r1y, 1) if r1y else None,
            _annualize_pct(r3y_v, 3) if r3y_v else None,
            ann_r,
        ])
        s = round(consist / 10 * 2, 1)
        score += s
        details["consistency"] = {"consistency_score": consist, "score": s, "max": 2, "coverage": "PROXY"}
    else:
        score += 1
        details["consistency"] = {"score": 1, "max": 2, "coverage": "UNAVAILABLE"}

    return {"score": round(score, 1), "max": 20, "details": details}


def _score_risk(perf: dict, adv: dict, peer: dict) -> dict:
    """维度二：风险控制能力（满分 20）"""
    details: dict = {}
    score = 0.0
    ps = peer.get("stats", {})

    # ① 最大回撤 vs 同类（6分）
    max_dd = perf.get("max_drawdown")
    p_dd = ps.get("max_drawdown", {})
    if max_dd is not None and p_dd:
        peer_avg = p_dd["mean"]  # 回撤是负数，均值通常也是负数
        # 越小（更负）越差；比例：fund_dd / peer_avg，>1 表示比同类更好（绝对值更小）
        if peer_avg < 0:
            ratio = abs(max_dd) / abs(peer_avg)
            s = 6 if ratio <= 0.7 else 5 if ratio <= 0.85 else 3 if ratio <= 1.0 else 1
        else:
            s = 3
        score += s
        details["max_drawdown"] = {"raw": max_dd, "peer_mean": peer_avg, "score": s, "max": 6, "coverage": "COMPUTED"}
    else:
        score += 3
        details["max_drawdown"] = {"score": 3, "max": 6, "coverage": "UNAVAILABLE"}

    # ② 夏普比率（4分）
    sharpe = perf.get("sharpe_ratio")
    if sharpe is not None:
        s = 4 if sharpe >= 1.5 else 3 if sharpe >= 1.0 else 2 if sharpe >= 0.5 else 1 if sharpe >= 0 else 0
        score += s
        details["sharpe"] = {"value": sharpe, "score": s, "max": 4, "coverage": "COMPUTED"}
    else:
        score += 2
        details["sharpe"] = {"score": 2, "max": 4, "coverage": "UNAVAILABLE"}

    # ③ 卡玛比率（4分）
    calmar = adv.get("calmar_ratio")
    if calmar is not None and not math.isnan(calmar):
        s = 4 if calmar >= 1.0 else 3 if calmar >= 0.7 else 2 if calmar >= 0.4 else 1 if calmar >= 0.2 else 0
        score += s
        details["calmar"] = {"value": calmar, "score": s, "max": 4, "coverage": "COMPUTED"}
    else:
        score += 2
        details["calmar"] = {"score": 2, "max": 4, "coverage": "UNAVAILABLE"}

    # ④ 下行捕获率（3分）
    dc = adv.get("downside_capture")
    if dc is not None:
        s = 3 if dc <= 0.70 else 2 if dc <= 0.80 else 1 if dc <= 1.0 else 0
        score += s
        details["downside_capture"] = {"value": dc, "score": s, "max": 3, "coverage": "COMPUTED"}
    else:
        score += 1.5
        details["downside_capture"] = {"score": 1.5, "max": 3, "coverage": "UNAVAILABLE"}

    # ⑤ 波动率 vs 同类（3分，替代回撤修复速度—数据不足）
    vol = perf.get("volatility")
    p_vol = ps.get("volatility", {})
    if vol is not None and p_vol:
        peer_vol = p_vol["mean"]
        ratio_v = vol / peer_vol if peer_vol > 0 else 1.0
        s = 3 if ratio_v <= 0.8 else 2 if ratio_v <= 1.0 else 1 if ratio_v <= 1.2 else 0
        score += s
        details["volatility"] = {"raw": vol, "peer_mean": peer_vol, "score": s, "max": 3, "coverage": "COMPUTED"}
    else:
        score += 1.5
        details["volatility"] = {"score": 1.5, "max": 3, "coverage": "UNAVAILABLE"}

    return {"score": round(score, 1), "max": 20, "details": details}


def _score_manager(fund_info: dict, adv: dict) -> dict:
    """维度三：基金经理能力（满分 15）"""
    details: dict = {}
    score = 0.0

    # ① 任职年限估算（4分）—— 以成立日期为代理下限
    tenure = fund_info.get("tenure_years")
    if tenure is not None:
        s = 4 if tenure >= 7 else 3 if tenure >= 5 else 2 if tenure >= 3 else 1 if tenure >= 1 else 0
        score += s
        details["tenure_years"] = {
            "value": round(tenure, 1), "note": "以基金成立日为代理（实际可能更短）",
            "score": s, "max": 4, "coverage": "PROXY",
        }
    else:
        score += 2
        details["tenure_years"] = {"score": 2, "max": 4, "coverage": "UNAVAILABLE"}

    # ② 代表产品历史（3分）—— 需人工核实，给中性默认
    score += 1.5
    details["track_record"] = {
        "note": "需人工核实：基金经理历史管理产品是否长期跑赢同类",
        "score": 1.5, "max": 3, "coverage": "UNAVAILABLE",
    }

    # ③ 管理规模适配度（3分）
    aum = fund_info.get("total_assets")
    asset_class = fund_info.get("asset_class", "broad_equity")
    if aum is not None:
        # 亿元为单位
        aum_bn = aum / 1e8  # DB 存的是元，转亿元
        s = _aum_score(aum_bn, asset_class)
        score += s
        details["aum_fit"] = {"aum_bn": round(aum_bn, 1), "asset_class": asset_class, "score": s, "max": 3, "coverage": "COMPUTED"}
    else:
        score += 1.5
        details["aum_fit"] = {"score": 1.5, "max": 3, "coverage": "UNAVAILABLE"}

    # ④ 风格稳定性代理（3分）—— 用下行捕获率作代理（极端市场表现）
    dc = adv.get("downside_capture")
    if dc is not None:
        s = 3 if dc <= 0.8 else 2 if dc <= 0.95 else 1
        score += s
        details["style_stability"] = {
            "note": "以下行捕获率代理极端市场表现（<0.8 较好）",
            "downside_capture": dc, "score": s, "max": 3, "coverage": "PROXY",
        }
    else:
        score += 1.5
        details["style_stability"] = {"score": 1.5, "max": 3, "coverage": "UNAVAILABLE"}

    # ⑤ 极端市场表现（2分）—— 已在风格稳定性里代理，这里给基础分
    score += 1.0
    details["extreme_market"] = {
        "note": "需对照2020/2022等市场极端时期的表现数据（建议人工核实）",
        "score": 1.0, "max": 2, "coverage": "PROXY",
    }

    return {"score": round(score, 1), "max": 15, "details": details}


def _score_strategy(fund_info: dict, holdings: dict) -> dict:
    """维度四：策略与风格稳定性（满分 15）"""
    details: dict = {}
    score = 0.0

    # ① 持仓风格与策略一致性（4分）
    asset_class = fund_info.get("asset_class", "broad_equity")
    sr = holdings.get("stock_ratio")
    br = holdings.get("bond_ratio")
    if sr is not None:
        s = _holding_consistency_score(asset_class, sr, br)
        score += s
        details["holding_consistency"] = {
            "asset_class": asset_class, "stock_ratio": sr, "bond_ratio": br,
            "score": s, "max": 4, "coverage": "COMPUTED",
        }
    else:
        score += 2
        details["holding_consistency"] = {"score": 2, "max": 4, "coverage": "UNAVAILABLE"}

    # ② 行业集中度代理（3分）—— 以 stock_ratio 代理（无细分行业数据）
    if sr is not None and asset_class in ("broad_equity", "growth_equity", "sector_equity"):
        # 权益基金：股票比例是否与声明类型吻合
        expected_sr = {"broad_equity": (70, 100), "growth_equity": (70, 100), "sector_equity": (70, 100)}.get(asset_class, (60, 100))
        in_range = expected_sr[0] <= sr <= expected_sr[1]
        s = 3 if in_range else 1
        score += s
        details["sector_concentration"] = {
            "note": "以持仓股票比例评估集中度一致性（无逐行业分布数据）",
            "stock_ratio": sr, "expected_range": expected_sr, "score": s, "max": 3, "coverage": "PROXY",
        }
    else:
        score += 1.5
        details["sector_concentration"] = {"score": 1.5, "max": 3, "coverage": "UNAVAILABLE"}

    # ③ 前十大持仓集中度（3分）—— 以持仓代码数代理
    stock_codes_str = holdings.get("stock_codes", "")
    if stock_codes_str:
        codes = [c for c in str(stock_codes_str).split(",") if c.strip()]
        n = len(codes)
        s = 3 if n >= 15 else 2 if n >= 10 else 1
        score += s
        details["top10_concentration"] = {
            "note": "以持仓股票数量代理（数量越多相对分散）",
            "stock_count": n, "score": s, "max": 3, "coverage": "PROXY",
        }
    else:
        score += 1.5
        details["top10_concentration"] = {"score": 1.5, "max": 3, "coverage": "UNAVAILABLE"}

    # ④ 换手率（3分）—— 无数据，给中性
    score += 1.5
    details["turnover"] = {
        "note": "换手率数据不在当前数据库中，建议从天天基金F10人工核实",
        "score": 1.5, "max": 3, "coverage": "UNAVAILABLE",
    }

    # ⑤ 名称与实际持仓一致性（2分）
    s = _name_reality_score(fund_info, holdings)
    score += s
    details["name_reality"] = {"score": s, "max": 2, "coverage": "COMPUTED"}

    return {"score": round(score, 1), "max": 15, "details": details}


def _score_attribution(adv: dict) -> dict:
    """维度五：持仓与收益来源（满分 10）"""
    details: dict = {}
    score = 0.0

    # ① Alpha（3分）
    alpha = adv.get("alpha_annual")
    if alpha is not None:
        s = 3 if alpha >= 5 else 2 if alpha >= 2 else 1 if alpha >= 0 else 0
        score += s
        details["alpha"] = {"value_pct": alpha, "note": "以SP500为代理基准（非实际基准）", "score": s, "max": 3, "coverage": "COMPUTED"}
    else:
        score += 1.5
        details["alpha"] = {"score": 1.5, "max": 3, "coverage": "UNAVAILABLE"}

    # ② Beta（2分）—— 是否与产品定位一致
    beta = adv.get("beta")
    asset_class = None  # 不在此函数参数里，给中性
    if beta is not None:
        s = 2 if 0.7 <= beta <= 1.3 else 1  # 与市场偏离过大则低分
        score += s
        details["beta"] = {"value": beta, "score": s, "max": 2, "coverage": "COMPUTED"}
    else:
        score += 1
        details["beta"] = {"score": 1, "max": 2, "coverage": "UNAVAILABLE"}

    # ③ 信息比率 IR（3分）
    ir = adv.get("information_ratio")
    if ir is not None:
        s = 3 if ir >= 0.8 else 2 if ir >= 0.5 else 1 if ir >= 0.3 else 0
        score += s
        details["ir"] = {"value": ir, "score": s, "max": 3, "coverage": "COMPUTED"}
    else:
        score += 1.5
        details["ir"] = {"score": 1.5, "max": 3, "coverage": "UNAVAILABLE"}

    # ④ 主动份额 Active Share（2分）—— 无数据
    score += 1.0
    details["active_share"] = {
        "note": "Active Share 需完整指数成分持仓数据，当前不可计算",
        "score": 1.0, "max": 2, "coverage": "UNAVAILABLE",
    }

    return {"score": round(score, 1), "max": 10, "details": details}


def _score_structure(fund_info: dict) -> dict:
    """维度六：规模、流动性与持有人结构（满分 10）"""
    details: dict = {}
    score = 0.0

    # ① 基金规模（3分）
    aum = fund_info.get("total_assets")
    asset_class = fund_info.get("asset_class", "broad_equity")
    if aum is not None:
        aum_bn = aum / 1e8
        s = _aum_score_structure(aum_bn, asset_class)
        score += s
        details["aum"] = {"aum_bn": round(aum_bn, 1), "score": s, "max": 3, "coverage": "COMPUTED"}
    else:
        score += 1.5
        details["aum"] = {"score": 1.5, "max": 3, "coverage": "UNAVAILABLE"}

    # ② 规模变化趋势（2分）—— 未跟踪历史
    score += 1.0
    details["aum_trend"] = {
        "note": "历史规模变化未跟踪，建议从天天基金/晨星查看近1年资金流入/流出",
        "score": 1.0, "max": 2, "coverage": "UNAVAILABLE",
    }

    # ③ 机构持有人占比（2分）—— 无数据
    score += 1.0
    details["institutional_pct"] = {
        "note": "持有人结构见半年度报告，可从天天基金F10查询机构持有比例",
        "score": 1.0, "max": 2, "coverage": "UNAVAILABLE",
    }

    # ④ 单一持有人占比（1分）—— 无数据
    score += 0.5
    details["single_holder"] = {"score": 0.5, "max": 1, "coverage": "UNAVAILABLE"}

    # ⑤ ETF/QDII 流动性（2分）
    fund_type = fund_info.get("fund_type", "")
    is_etf = "ETF" in str(fund_type).upper()
    s = 1.5 if is_etf else 2  # ETF 还需折溢价数据才能满分
    score += s
    details["liquidity"] = {
        "is_etf": is_etf,
        "note": "ETF 需折溢价数据（当前不可计算）；场外 QDII 流动性相对可接受",
        "score": s, "max": 2, "coverage": "PROXY",
    }

    return {"score": round(score, 1), "max": 10, "details": details}


def _score_cost(fund_info: dict, peer: dict) -> dict:
    """维度七：费用与交易成本（满分 10）"""
    details: dict = {}
    score = 0.0

    # ① 综合费率 vs 同类（7分）
    er = fund_info.get("expense_ratio")
    ps = peer.get("stats", {}).get("expense_ratio", {})
    if er is not None:
        if ps:
            peer_median = ps["median"]
            # 优秀（低于同类25分位），良好（低于中位数），一般（高于中位数），差（高于75分位）
            if er <= ps.get("p25", 0.006):
                s = 7
            elif er <= ps["median"]:
                s = 6
            elif er <= ps.get("p75", 0.015):
                s = 4
            else:
                s = 2
            details["expense_ratio"] = {
                "value_pct": round(er * 100, 3), "peer_median_pct": round(peer_median * 100, 3),
                "score": s, "max": 7, "coverage": "COMPUTED",
            }
        else:
            # 用绝对阈值
            s = 7 if er <= 0.005 else 6 if er <= 0.008 else 5 if er <= 0.010 else 4 if er <= 0.012 else 3 if er <= 0.015 else 1
            details["expense_ratio"] = {
                "value_pct": round(er * 100, 3), "score": s, "max": 7, "coverage": "COMPUTED",
            }
        score += s
    else:
        score += 3.5
        details["expense_ratio"] = {"score": 3.5, "max": 7, "coverage": "UNAVAILABLE"}

    # ② 申购赎回费（2分）
    fund_type = fund_info.get("fund_type", "")
    is_etf = "ETF" in str(fund_type).upper()
    s = 2 if is_etf else 1  # ETF 无申赎费；场外基金默认给1分
    score += s
    details["purchase_redeem_fee"] = {
        "is_etf": is_etf, "note": "ETF 无申购赎回费；场外申购费需查基金合同",
        "score": s, "max": 2, "coverage": "PROXY",
    }

    # ③ 换手隐性成本（1分）
    score += 0.5
    details["turnover_cost"] = {
        "note": "换手率越高隐性成本越大；当前无换手率数据",
        "score": 0.5, "max": 1, "coverage": "UNAVAILABLE",
    }

    return {"score": round(score, 1), "max": 10, "details": details}


# ─────────────────────────────────────────────────────────────
# Step 5 — 一票否决（5 条硬门槛）
# ─────────────────────────────────────────────────────────────

def _check_vetoes(
    fund_info: dict,
    perf: dict,
    adv: dict,
    peer: dict,
) -> list[dict]:
    vetoes: list[dict] = []
    ps = peer.get("stats", {})

    # 条件1：基金经理（代理）任职不足1年
    tenure = fund_info.get("tenure_years")
    if tenure is not None and tenure < 1.0:
        vetoes.append({
            "id": 1,
            "condition": "基金经理任职不足1年",
            "detail": f"成立日估算任职约 {tenure:.1f} 年，宣传以来历史业绩可能不代表当前经理能力",
            "severity": "hard",
        })

    # 条件2：长期跑不赢基准（alpha<0）且费率明显偏高
    alpha = adv.get("alpha_annual")
    er = fund_info.get("expense_ratio")
    if alpha is not None and alpha < -2 and er is not None and er > 0.012:
        vetoes.append({
            "id": 2,
            "condition": "长期跑不赢基准且费率偏高",
            "detail": f"年化 alpha ≈ {alpha:.1f}%，费率 {er*100:.2f}%，主动管理价值存疑",
            "severity": "hard",
        })

    # 条件3：最大回撤显著高于同类且无充分收益补偿
    max_dd = perf.get("max_drawdown")
    ann_r  = perf.get("annualized_return")
    p_dd   = ps.get("max_drawdown", {})
    if max_dd is not None and ann_r is not None and p_dd:
        peer_dd = p_dd.get("mean", max_dd)
        if peer_dd < 0 and abs(max_dd) > abs(peer_dd) * 1.4:
            calmar = adv.get("calmar_ratio")
            if calmar is None or calmar < 0.5:
                vetoes.append({
                    "id": 3,
                    "condition": "最大回撤显著高于同类但无充分收益补偿",
                    "detail": f"最大回撤 {max_dd:.1f}%，同类均值 {peer_dd:.1f}%，卡玛比率仅 {calmar:.2f if calmar else '不可计算'}",
                    "severity": "hard",
                })

    # 条件4：名称与实际持仓严重不符（asset_class 与 holdings 严重偏离）
    asset_class = fund_info.get("asset_class", "broad_equity")
    sr = fund_info.get("stock_ratio") or (fund_info.get("holdings", {}) or {}).get("stock_ratio")
    if sr is None:
        from ..utils.database import read_table as _rt
        hdf = _rt("fund_holdings", "fund_code = ? ORDER BY date DESC LIMIT 1", (fund_info["fund_code"],))
        if not hdf.empty:
            sr = _safe_float(hdf.iloc[0].get("stock_ratio"))

    if sr is not None:
        if asset_class == "bond" and sr > 50:
            vetoes.append({
                "id": 4,
                "condition": "名称标注为债券类但实际股票比例超 50%",
                "detail": f"asset_class=bond，但持仓 stock_ratio={sr:.1f}%",
                "severity": "warn",
            })
        elif asset_class in ("broad_equity", "growth_equity", "sector_equity") and sr < 40:
            vetoes.append({
                "id": 4,
                "condition": "名称标注为权益类但实际股票比例低于 40%",
                "detail": f"asset_class={asset_class}，但持仓 stock_ratio={sr:.1f}%",
                "severity": "warn",
            })

    # 条件5：规模过小（<2亿）
    aum = fund_info.get("total_assets")
    if aum is not None and aum > 0:
        aum_bn = aum / 1e8
        if aum_bn < 2.0:
            vetoes.append({
                "id": 5,
                "condition": "基金规模过小（<2亿），存在清盘风险",
                "detail": f"规模约 {aum_bn:.2f} 亿元",
                "severity": "hard",
            })

    return vetoes


# ─────────────────────────────────────────────────────────────
# Step 6 — 配置结论
# ─────────────────────────────────────────────────────────────

def _build_conclusion(
    total: float,
    vetoes: list[dict],
    fund_info: dict,
    adv: dict,
    market_signal: dict | None,
) -> dict:
    hard_vetoes = [v for v in vetoes if v.get("severity") == "hard"]
    if hard_vetoes:
        grade = "剔除"
    else:
        grade = _grade(total)

    asset_class = fund_info.get("asset_class", "broad_equity")
    region      = fund_info.get("region", "未知")
    benchmark   = fund_info.get("benchmark", "")
    alpha       = adv.get("alpha_annual")
    beta        = adv.get("beta")
    er          = fund_info.get("expense_ratio")

    # 赚钱逻辑
    if asset_class == "bond":
        earn_logic = "票息 + 久期（固收类）"
    elif asset_class == "commodity":
        earn_logic = "商品价格 beta"
    elif asset_class == "growth_equity":
        earn_logic = f"成长/科技 beta（{region}）"
    else:
        earn_logic = f"市场 beta（{region}，{benchmark}）"

    if alpha is not None and alpha > 2:
        earn_logic += f"，叠加年化约 {alpha:.1f}% alpha（相对SP500代理基准）"

    # 主要风险
    risks = []
    if beta is not None and beta > 1.2:
        risks.append(f"高 beta（{beta:.2f}）放大市场波动")
    if region not in ("全球", "现金") and region != "未知":
        risks.append(f"{region} 区域集中")
    risks.append("QDII 汇率风险和额度限制")

    # 适合场景
    if grade in ("优质候选", "合格候选"):
        role = "核心底仓或卫星配置" if asset_class == "broad_equity" else "卫星增强配置"
    else:
        role = "不建议常规配置"

    # 市场信号适配
    fit_signal = None
    if market_signal:
        composite = market_signal.get("composite_signal", "标配稳健")
        from ..utils.fund_universe import strategy_match_score as sms
        strat_score = sms(asset_class, composite)
        fit_signal = {
            "composite_signal": composite,
            "strategy_match_score": strat_score,
            "assessment": "高度契合" if strat_score >= 7.5 else "基本契合" if strat_score >= 6 else "一般" if strat_score >= 5 else "不契合",
        }

    summary = (
        f"本质是{asset_class}类 QDII（{region}），靠{earn_logic}赚钱；"
        f"主要风险：{'、'.join(risks)}；"
        f"{'一票否决触发' if hard_vetoes else f'综合评分 {total:.1f} 分，等级：{grade}'}；"
        f"适合作为{role}。"
    )

    return {
        "grade":       grade,
        "total_score": round(total, 1),
        "summary":     summary,
        "earn_logic":  earn_logic,
        "main_risks":  risks,
        "role":        role,
        "fit_signal":  fit_signal,
        "veto_triggered": len(hard_vetoes) > 0,
    }


# ─────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────

def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _annualize_pct(cum_return_pct: float, years: float) -> float:
    """累计收益率（%）→ 年化收益率（%）。"""
    growth = 1 + cum_return_pct / 100
    if growth <= 0:
        return -100.0
    return ((growth ** (1.0 / years)) - 1) * 100


def _compute_tenure(inception_date) -> Optional[float]:
    """从成立日估算最长可能任职年限（代理）。"""
    if not inception_date:
        return None
    try:
        d = pd.to_datetime(str(inception_date))
        delta = (pd.Timestamp.now() - d).days
        return round(delta / 365.25, 1)
    except Exception:
        return None


def _aum_score(aum_bn: float, asset_class: str) -> float:
    """规模适配分（用于经理维度）：0-3。"""
    if aum_bn < 2:
        return 0.0
    if asset_class in ("growth_equity", "sector_equity"):
        return 3 if 20 <= aum_bn <= 150 else 2 if aum_bn < 20 else 1
    else:
        return 3 if 20 <= aum_bn <= 300 else 2 if aum_bn < 20 else 2


def _aum_score_structure(aum_bn: float, asset_class: str) -> float:
    """规模评分（用于结构维度）：0-3。"""
    if aum_bn < 0.5:
        return 0.0  # 极小，几乎必清盘
    if aum_bn < 2:
        return 1.0  # 清盘风险
    if asset_class in ("growth_equity", "sector_equity"):
        return 3 if 10 <= aum_bn <= 200 else 2
    return 3 if 10 <= aum_bn <= 500 else 2


def _holding_consistency_score(asset_class: str, stock_ratio: float, bond_ratio: Optional[float]) -> float:
    """持仓比例与声明策略一致性评分：0-4。"""
    if asset_class in ("broad_equity", "growth_equity", "sector_equity"):
        if stock_ratio >= 80:
            return 4
        elif stock_ratio >= 65:
            return 3
        elif stock_ratio >= 50:
            return 2
        else:
            return 0  # 权益基金股票比例过低
    elif asset_class == "bond":
        br = bond_ratio or 0
        if br >= 80:
            return 4
        elif br >= 60:
            return 3
        else:
            return 1
    else:
        return 2  # 商品/其他，默认中性


def _name_reality_score(fund_info: dict, holdings: dict) -> float:
    """名称/声明类别与实际持仓一致性：0-2。"""
    asset_class = fund_info.get("asset_class", "broad_equity")
    sr = holdings.get("stock_ratio")
    br = holdings.get("bond_ratio")

    if sr is None:
        return 1.0  # 无数据，给中性

    if asset_class == "bond" and sr > 50:
        return 0
    if asset_class in ("broad_equity", "growth_equity", "sector_equity") and sr < 40:
        return 0
    if asset_class == "bond" and (br or 0) >= 60:
        return 2
    if asset_class in ("broad_equity", "growth_equity", "sector_equity") and sr >= 70:
        return 2
    return 1


def _grade(total: float) -> str:
    if total >= 85: return "优质候选"
    if total >= 75: return "合格候选"
    if total >= 65: return "有明显短板"
    if total >= 50: return "不建议配置"
    return "剔除"
