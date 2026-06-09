"""基金综合评分引擎"""
from typing import Any
from collections.abc import Mapping
import pandas as pd
import numpy as np
from ..utils.database import read_table, upsert_dataframe
from ..utils.config import load_config
from ..utils.fund_universe import (
    classify_asset_class, strategy_match_score, holdings_adjusted_strategy_score,
)
from ..domain.scoring import category_percentile, consistency_score, cost_score


def score_all_funds(market_signal: Mapping[str, Any]) -> pd.DataFrame:
    cfg = load_config()
    weights = cfg.get("scoring_weights", {})
    w_perf    = weights.get("performance",    0.30)
    w_risk    = weights.get("risk_adjusted",  0.25)
    w_strategy = weights.get("strategy_match", 0.20)
    w_cost    = weights.get("cost_efficiency", 0.15)
    w_consist = weights.get("consistency",    0.10)

    funds   = read_table("fund_list")
    perf_df = read_table("fund_performance")
    if funds.empty:
        return pd.DataFrame()

    merged = (funds.merge(perf_df, on="fund_code", how="left")
              if not perf_df.empty else funds.copy())

    # 加载持仓数据（best-effort）：fund_holdings 按基金取最新一条
    holdings_map: dict[str, dict] = {}
    try:
        holdings_df = read_table("fund_holdings")
        if not holdings_df.empty:
            for _, row in (
                holdings_df.sort_values("date")
                .groupby("fund_code")
                .last()
                .reset_index()
                .iterrows()
            ):
                code = str(row["fund_code"])
                holdings_map[code] = {
                    "stock_ratio": row.get("stock_ratio"),
                    "bond_ratio":  row.get("bond_ratio"),
                    "cash_ratio":  row.get("cash_ratio"),
                }
    except Exception as e:
        # 持仓数据加载失败会让所有基金的 strategy_match 退化为无持仓口径，
        # 评分系统性偏移，必须可见（仍以空 map 继续，不阻断主流程）。
        print(f"[WARN] 持仓数据加载失败，strategy_match 将以无持仓口径评分: {e}")

    # ── Pass 1: 计算所有基金的原始指标 ────────────────────────────
    raw_rows = []
    for _, row in merged.iterrows():
        code = str(row["fund_code"])
        asset_class = classify_asset_class(
            fund_code=code,
            fund_type=str(row.get("fund_type", "")),
            fund_name=str(row.get("fund_name", "")),
            benchmark=str(row.get("benchmark", "")),
        )
        r1y = row.get("return_1y")
        r3y = row.get("return_3y")
        r6m = row.get("return_6m")

        ann_1y = _annualize(float(r1y), 1.0) if pd.notna(r1y) else None
        ann_3y = _annualize(float(r3y), 3.0) if pd.notna(r3y) else None
        ann_6m = _annualize(float(r6m), 0.5) if pd.notna(r6m) else None

        avail = [(v, w) for v, w in [(ann_1y, 0.4), (ann_3y, 0.35), (ann_6m, 0.25)] if v is not None]
        total_w = sum(w for _, w in avail)
        perf_raw = sum(v * w for v, w in avail) / total_w if avail else 0.0

        # 用 NaN 标记真正缺失的指标，让类别内百分位排名自动排除它们；
        # 有值（包括合法的 0 / 负数）才转为 float。
        def _to_float_or_nan(v):
            return float(v) if pd.notna(v) and v is not None else float("nan")

        raw_rows.append({
            "fund_code":     code,
            "fund_name":     str(row.get("fund_name", code)),
            "asset_class":   asset_class,
            "perf_raw":      perf_raw if avail else float("nan"),
            "sharpe_raw":    _to_float_or_nan(row.get("sharpe_ratio")),
            "max_dd_raw":    _to_float_or_nan(row.get("max_drawdown")),
            "vol_raw":       _to_float_or_nan(row.get("volatility")),
            "expense_ratio": float(row.get("expense_ratio") or 0.012),
            "ann_1y": ann_1y,
            "ann_3y": ann_3y,
            "ann_6m": ann_6m,
        })

    if not raw_rows:
        return pd.DataFrame()

    df_raw = pd.DataFrame(raw_rows)

    # ── Pass 2: 类别内相对百分位（0–10）──────────────────────────
    # 绩效/夏普：越高越好；回撤（负数，越接近0越好）：越高越好；波动率：越低越好
    df_raw["perf_pct"]   = category_percentile(df_raw, "perf_raw",  "asset_class", low_is_good=False)
    df_raw["sharpe_pct"] = category_percentile(df_raw, "sharpe_raw","asset_class", low_is_good=False)
    df_raw["dd_pct"]     = category_percentile(df_raw, "max_dd_raw","asset_class", low_is_good=False)
    df_raw["vol_pct"]    = category_percentile(df_raw, "vol_raw",   "asset_class", low_is_good=True)

    # ── Pass 3: 加权合并 ───────────────────────────────────────
    composite = market_signal.get("composite_signal", "标配稳健")
    results = []
    for _, row in df_raw.iterrows():
        perf_score     = row["perf_pct"]
        risk_score     = row["sharpe_pct"] * 0.4 + row["dd_pct"] * 0.35 + row["vol_pct"] * 0.25
        # 策略匹配：有持仓数据时用真实持仓精修（70% 资产类别 + 30% 持仓适配）
        h = holdings_map.get(row["fund_code"], {})
        strategy_score = holdings_adjusted_strategy_score(
            row["asset_class"], composite,
            h.get("stock_ratio"), h.get("bond_ratio"), h.get("cash_ratio"),
        )
        cost_score_val = cost_score(row["expense_ratio"], cfg)
        consist_score  = consistency_score([row["ann_1y"], row["ann_3y"], row["ann_6m"]])

        total = (
            perf_score       * w_perf
            + risk_score     * w_risk
            + strategy_score * w_strategy
            + cost_score_val * w_cost
            + consist_score  * w_consist
        ) * 10

        signal, recommendation = _generate_signal(total, market_signal)
        results.append({
            "fund_code":         row["fund_code"],
            "fund_name":         row["fund_name"],
            "total_score":       round(total, 1),
            "performance_score": round(perf_score * 10, 1),
            "risk_score":        round(risk_score * 10, 1),
            "strategy_score":    round(strategy_score * 10, 1),
            "consistency_score": round(consist_score * 10, 1),
            "cost_score":        round(cost_score_val * 10, 1),
            "signal":            signal,
            "recommendation":    recommendation,
        })

    df_out = pd.DataFrame(results).sort_values("total_score", ascending=False)
    upsert_dataframe(df_out, "fund_scores", ["fund_code"])
    return df_out


# ── 工具函数 ──────────────────────────────────────────────────────


def _annualize(cum_return_pct: float, years: float) -> float:
    growth = 1 + cum_return_pct / 100.0
    if growth <= 0:
        return -100.0
    return (growth ** (1.0 / years) - 1) * 100.0


def _generate_signal(score: float, market_signal: Mapping[str, Any]) -> tuple[str, str]:
    composite = market_signal.get("composite_signal", "标配稳健")

    if score >= 75:
        if composite == "减仓防守":
            return "持有", "高质量基金，当前市场建议标配持有"
        return "买入", f"综合评分优秀（{score:.0f}分），建议积极配置"
    elif score >= 60:
        if composite == "重仓进取":
            return "增持", f"评分良好（{score:.0f}分），进取市场环境下适当加仓"
        return "持有", f"评分良好（{score:.0f}分），维持标配仓位"
    elif score >= 45:
        return "观望", f"评分一般（{score:.0f}分），等待更好时机"
    else:
        return "回避", f"评分偏低（{score:.0f}分），建议暂不配置"
