"""基金综合评分引擎"""
import pandas as pd
import numpy as np
from ..utils.database import read_table, upsert_dataframe
from ..utils.config import load_config
from ..utils.fund_universe import classify_asset_class, strategy_match_score


def score_all_funds(market_signal: dict) -> pd.DataFrame:
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

        raw_rows.append({
            "fund_code":     code,
            "fund_name":     str(row.get("fund_name", code)),
            "asset_class":   asset_class,
            "perf_raw":      perf_raw,
            "sharpe_raw":    float(row.get("sharpe_ratio") or 0),
            "max_dd_raw":    float(row.get("max_drawdown") or -20),
            "vol_raw":       float(row.get("volatility") or 20),
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
    df_raw["perf_pct"]   = _category_pct(df_raw, "perf_raw",  "asset_class", low_is_good=False)
    df_raw["sharpe_pct"] = _category_pct(df_raw, "sharpe_raw","asset_class", low_is_good=False)
    df_raw["dd_pct"]     = _category_pct(df_raw, "max_dd_raw","asset_class", low_is_good=False)
    df_raw["vol_pct"]    = _category_pct(df_raw, "vol_raw",   "asset_class", low_is_good=True)

    # ── Pass 3: 加权合并 ───────────────────────────────────────
    composite = market_signal.get("composite_signal", "标配稳健")
    results = []
    for _, row in df_raw.iterrows():
        perf_score     = row["perf_pct"]
        risk_score     = row["sharpe_pct"] * 0.4 + row["dd_pct"] * 0.35 + row["vol_pct"] * 0.25
        strategy_score = strategy_match_score(row["asset_class"], composite)
        cost_score     = _calc_cost_score(row["expense_ratio"], cfg)
        consist_score  = _calc_consistency_score(row["ann_1y"], row["ann_3y"], row["ann_6m"])

        total = (
            perf_score     * w_perf
            + risk_score   * w_risk
            + strategy_score * w_strategy
            + cost_score   * w_cost
            + consist_score * w_consist
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
            "cost_score":        round(cost_score * 10, 1),
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


def _category_pct(df: pd.DataFrame, col: str, group_col: str,
                  low_is_good: bool = False, min_group: int = 3) -> pd.Series:
    """类别内百分位排名，映射到 0–10。
    low_is_good=False → 越大越好（收益/夏普/回撤数值）。
    low_is_good=True  → 越小越好（波动率/费率）。
    类别内基金数 < min_group 时退回全局排名，避免单基金假满分。
    """
    # ascending=True: 最小值排名1, 最大值排名n, pct: 最大→1.0
    # ascending=False: 最大值排名1, 最小值排名n, pct: 最小→1.0
    # low_is_good=False → 高分有利 → ascending=True → 高值→高pct ✓
    # low_is_good=True  → 低值有利 → ascending=False → 低值→高pct ✓
    asc = not low_is_good
    result = pd.Series(0.0, index=df.index)
    global_ranks = df[col].rank(pct=True, ascending=asc)

    for _, idx in df.groupby(group_col).groups.items():
        if len(idx) >= min_group:
            ranks = df.loc[idx, col].rank(pct=True, ascending=asc)
        else:
            ranks = global_ranks.loc[idx]
        result.loc[idx] = ranks * 10

    return result.clip(0, 10)


def _calc_consistency_score(ann_1y, ann_3y, ann_6m) -> float:
    """跨期收益稳定性评分（0–10）。
    基于已有期间数据，衡量正收益占比和期间离散度。
    """
    avail = [v for v in [ann_1y, ann_3y, ann_6m] if v is not None]
    if len(avail) < 2:
        return 5.0  # 数据不足 → 中性

    pos_ratio = sum(1 for v in avail if v > 0) / len(avail)
    std = float(np.std(avail))

    # 正收益占比贡献 7 分，低离散度贡献 3 分
    score = pos_ratio * 7.0 + max(0.0, 1.0 - std / 30.0) * 3.0
    return float(np.clip(score, 0.0, 10.0))


def _calc_cost_score(er: float, cfg: dict) -> float:
    params = cfg.get("strategy_params", {}).get("cost_filter", {})
    pref   = params.get("preferred_expense_ratio", 0.005)
    max_er = params.get("max_expense_ratio", 0.015)

    if er <= pref:
        return 10.0
    elif er <= max_er:
        return 10.0 - (er - pref) / (max_er - pref) * 5.0
    else:
        return max(0.0, 5.0 - (er - max_er) * 100.0)


def _generate_signal(score: float, market_signal: dict) -> tuple[str, str]:
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
