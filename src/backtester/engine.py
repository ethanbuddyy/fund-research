"""
走向前回测引擎 (Walk-Forward Backtest)
无前视偏差：每个调仓日仅使用截至该日的历史数据

⚠️ 幸存者偏差说明：基金池来自当前仍在运作的核心QDII列表，已清盘/合并/改名
的基金未纳入。因此回测结果对“当年可选基金”是乐观估计——真实历史中部分被
本回测选中的基金在早期可能尚未成立，未被选中的失败基金也已消失。解读时应把
策略表现视为上界而非可复现收益。结果中的 survivorship_note 字段同步披露此点。
"""
import pandas as pd
import numpy as np
from datetime import datetime
from ..utils.database import read_table
from ..utils.config import load_config


# QDII ETF 场内双边摩擦（手续费+买卖价差+汇率）：实测约 0.3–0.5%，保守取 0.5%
# 开放式 QDII（申购1%+赎回0.5%）更高；此处统一按场内 ETF 保守估算
TRANSACTION_COST_RT = 0.005  # 0.5% 双边（round-trip），按实际换手率扩展
RF_ANNUAL = 0.02             # 无风险利率假设 2%


# ─────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────

def run_backtest(
    start_date: str = None,
    end_date: str = None,
    top_n: int = 5,
    rebalance_freq: str = "MS",
    cape_overvalued: float = None,   # None = 使用 settings.yaml 值
    min_cash_pct: float = None,      # 强制最低现金下限（0~0.5），None = 不覆盖
) -> dict:
    """
    执行走向前月度回测。

    Args:
        start_date:     回测开始日 (YYYY-MM-DD)
        end_date:       回测结束日
        top_n:          每期持仓基金数量
        rebalance_freq: 调仓频率 'MS'=每月初, 'QS'=每季度初
        cape_overvalued: 覆盖 CAPE 高估阈值（默认30，可调高放宽限制）
        min_cash_pct:   强制最低现金比例（默认不限，可设0降低防守阈值）
    """
    cfg = load_config()
    # 参数覆盖
    if cape_overvalued is not None:
        cfg.setdefault("strategy_params", {}).setdefault("valuation_thresholds", {})["cape_overvalued"] = cape_overvalued

    fund_nav   = read_table("fund_nav_history")
    market_db  = read_table("market_data")
    macro_db   = read_table("macro_data")
    fund_list  = read_table("fund_list")
    cape_hist  = _load_cape_history()   # 真实 CAPE 历史（如已采集），按日期升序的 Series

    if fund_nav.empty:
        return {"error": "无基金净值数据，请先运行 python run.py"}

    fund_nav["date"]  = pd.to_datetime(fund_nav["date"])
    market_db["date"] = pd.to_datetime(market_db["date"])
    if not macro_db.empty:
        macro_db["date"] = pd.to_datetime(macro_db["date"])

    # 获取完整 SP500 历史（包含 DB 没有的更早数据）
    sp500_full = _fetch_sp500_full(market_db)

    # 确定回测区间（需要宏观+市场数据双覆盖）
    data_start = max(
        fund_nav["date"].min() + pd.DateOffset(months=6),
        macro_db["date"].min() + pd.DateOffset(months=12) if not macro_db.empty else pd.Timestamp("2022-01-01"),
        sp500_full.index.min() + pd.DateOffset(months=1),
    )
    data_end = fund_nav["date"].max()

    bt_start = pd.to_datetime(start_date) if start_date else data_start
    bt_end   = pd.to_datetime(end_date)   if end_date   else data_end

    rebalance_dates = pd.date_range(start=bt_start, end=bt_end, freq=rebalance_freq)
    if len(rebalance_dates) < 4:
        return {"error": f"回测区间过短（{len(rebalance_dates)} 个调仓日），至少需要 4 个月"}

    records = []
    # 等权基准：全仓买入所有可用基金，不择时不择基，隔离"选基"vs"择时"贡献
    ewbh_all_codes = fund_list["fund_code"].astype(str).tolist()
    prev_selected: set[str] = set()   # 上期持仓，用于计算换手率

    for i in range(len(rebalance_dates) - 1):
        t0 = rebalance_dates[i]
        t1 = rebalance_dates[i + 1]

        # 截至 t0 的数据快照（严格无前视偏差）
        nav_snap = fund_nav[fund_nav["date"] <= t0]
        mkt_snap = market_db[market_db["date"] <= t0]
        mac_snap = macro_db[macro_db["date"] <= t0] if not macro_db.empty else pd.DataFrame()
        sp500_snap = sp500_full[sp500_full.index <= t0]

        # 真实 CAPE 截至 t0 的快照（无前视）
        cape_snap = cape_hist[cape_hist.index <= t0] if cape_hist is not None and not cape_hist.empty else None

        # 生成市场信号
        signal = _compute_signal(sp500_snap, mkt_snap, mac_snap, cfg, cape_snap)

        # 评分并选基金
        scored = _score_funds(nav_snap, fund_list, signal, cfg)
        selected_codes = scored.head(top_n)["fund_code"].tolist()

        # 参数覆盖：强制最低现金下限
        if min_cash_pct is not None and signal["cash_allocation"] > min_cash_pct:
            overflow = signal["cash_allocation"] - min_cash_pct
            signal["cash_allocation"] = min_cash_pct
            signal["core_allocation"] += overflow * 0.67
            signal["satellite_allocation"] += overflow * 0.33

        # 换手率驱动的交易成本：首期全部买入（turnover=1），后续按新旧持仓差异比例
        cur_set = set(selected_codes)
        if i == 0:
            turnover = 1.0
        else:
            n_total = max(len(cur_set | prev_selected), 1)
            n_changed = len(cur_set.symmetric_difference(prev_selected))
            turnover = n_changed / n_total
        prev_selected = cur_set

        # 策略收益 = 基金组合收益 × 投资仓位 − 摩擦成本（按换手率加权）
        invested = signal["core_allocation"] + signal["satellite_allocation"]
        port_ret = _portfolio_period_return(fund_nav, selected_codes, t0, t1)
        strat_ret = port_ret * invested - TRANSACTION_COST_RT * turnover * invested

        # 基准1：标普500买入持有
        sp500_ret = _index_period_return(sp500_full, t0, t1)
        # 基准2：60/40（标普500 60% + 现金 40%）
        b6040_ret = sp500_ret * 0.6
        # 基准3：等权全仓基金买入持有（无择时无择基，隔离信号贡献）
        ewbh_ret  = _portfolio_period_return(fund_nav, ewbh_all_codes, t0, t1)

        records.append({
            "date":          t0,
            "strat_return":  strat_ret,
            "sp500_return":  sp500_ret,
            "b6040_return":  b6040_ret,
            "ewbh_return":   ewbh_ret,
            "signal":        signal["composite_signal"],
            "composite_raw": round(signal["composite_raw"], 2),
            "cape":          round(signal["cape"], 1),
            "vix":           round(signal["vix"], 1),
            "trend":         round(signal.get("trend_score", 5), 1),
            "invested":      round(invested, 2),
            "top_funds":     ", ".join(selected_codes[:3]),
            "cash":          round(signal["cash_allocation"], 2),
        })

    df = pd.DataFrame(records).set_index("date")

    # 累计净值序列（初始 = 1.0）
    df["strat_cum"]  = (1 + df["strat_return"]).cumprod()
    df["sp500_cum"]  = (1 + df["sp500_return"]).cumprod()
    df["b6040_cum"]  = (1 + df["b6040_return"]).cumprod()
    df["ewbh_cum"]   = (1 + df["ewbh_return"]).cumprod()

    # 最大回撤序列（用于画图）
    df["strat_dd"]  = _drawdown_series(df["strat_cum"])
    df["sp500_dd"]  = _drawdown_series(df["sp500_cum"])

    return {
        "df":            df,
        "strat_metrics": calc_metrics(df["strat_return"],  "本策略（动态配置）"),
        "sp500_metrics": calc_metrics(df["sp500_return"],  "标普500（买入持有）"),
        "b6040_metrics": calc_metrics(df["b6040_return"],  "60/40 组合"),
        "ewbh_metrics":  calc_metrics(df["ewbh_return"],   "等权基金买入持有"),
        "signal_stats":  _signal_accuracy(df),
        "start_date":    rebalance_dates[0].strftime("%Y-%m-%d"),
        "end_date":      rebalance_dates[-1].strftime("%Y-%m-%d"),
        "n_periods":     len(df),
        "fund_list":     fund_list,
        "data_source":   _backtest_data_source(),
        "survivorship_note": (
            f"基金池为当前在运作的 {len(fund_list)} 只核心QDII，未含已清盘/改名基金；"
            "策略收益为乐观上界，非可复现实盘收益。"
        ),
    }


def _backtest_data_source() -> str:
    try:
        from ..utils import provenance
        return provenance.overall_mode()
    except Exception:
        return "unknown"


# ─────────────────────────────────────────────
# 市场信号（无前视偏差版本）
# ─────────────────────────────────────────────

def _compute_signal(sp500_snap: pd.Series, mkt_snap: pd.DataFrame,
                    mac_snap: pd.DataFrame, cfg: dict,
                    cape_snap: pd.Series = None) -> dict:
    """
    用截止日期快照重算市场信号（与 signals.py 逻辑严格对应）。
    估值优先用真实 CAPE 历史（cape_snap，截至 t0，无前视）；缺失时回退点位近似。
    权重：宏观20% + 估值20% + 逆向情绪15% + 趋势30% + 信用15%（独立因子）
    """
    # SP500 最新价 & VIX
    sp500_price = float(sp500_snap.iloc[-1]) if not sp500_snap.empty else 5000.0
    vix_df = mkt_snap[mkt_snap["symbol"] == "^VIX"].sort_values("date")
    vix = float(vix_df.iloc[-1]["close"]) if not vix_df.empty else 18.0

    # ① 估值评分：真实 CAPE 历史分位数优先，否则点位近似 + 固定参考分布
    if cape_snap is not None and len(cape_snap) >= 1:
        cape = float(cape_snap.iloc[-1])
        if len(cape_snap) >= 60:
            P25, P50, P75, P90 = np.percentile(cape_snap.values.astype(float), [25, 50, 75, 90])
        else:
            P25, P50, P75, P90 = 21.0, 26.0, 31.0, 36.0
    else:
        cape = float(np.clip(30.0 + (sp500_price - 5000) / 1000 * 3.0, 12, 50))
        P25, P50, P75, P90 = 21.0, 26.0, 31.0, 36.0

    if   cape >= P90: val_score = 1
    elif cape >= P75: val_score = 3
    elif cape >= P50: val_score = 5
    elif cape >= P25: val_score = 7
    else:             val_score = 9

    # ② 宏观周期评分
    macro_base = _macro_score_from_snap(mac_snap)

    # ③ 美联储方向修正
    fed_dir = _fed_direction_from_snap(mac_snap)
    macro_adj = float(np.clip(macro_base + fed_dir, 1, 10))

    # ④ 逆向情绪评分
    sp500_1m = sp500_snap.tail(22)
    mom = (float(sp500_1m.iloc[-1]) / float(sp500_1m.iloc[0]) - 1) * 100 if len(sp500_1m) >= 2 else 0
    vix_s = float(np.clip(100 - (vix - 10) * 3.33, 0, 100))
    mom_s = float(np.clip(50 + mom * 5, 0, 100))
    contrarian = 10 - (vix_s * 0.6 + mom_s * 0.4) / 10

    # ⑤ 趋势滤波器：当前价格 vs 12个月均线
    trend = _trend_from_snap(sp500_snap)

    # ⑥ 信用利差（独立因子）
    credit = _credit_from_snap(mac_snap)

    # 去相关权重：宏观20% + 估值20% + 逆向情绪15% + 趋势30% + 信用15%
    raw = (macro_adj * 0.20 + val_score * 0.20
           + contrarian * 0.15 + trend * 0.30 + credit * 0.15)

    if   raw >= 7.0: sig = "重仓进取"; c, s, ca = 0.70, 0.25, 0.05
    elif raw >= 5.0: sig = "标配稳健"; c, s, ca = 0.60, 0.30, 0.10
    elif raw >= 3.0: sig = "谨慎防守"; c, s, ca = 0.50, 0.20, 0.30
    else:            sig = "减仓防守"; c, s, ca = 0.35, 0.15, 0.50

    return {
        "composite_signal": sig, "composite_raw": raw,
        "core_allocation": c, "satellite_allocation": s, "cash_allocation": ca,
        "cape": cape, "vix": vix, "trend_score": trend,
    }


def _macro_score_from_snap(mac: pd.DataFrame) -> float:
    if mac.empty:
        return 5.0

    def latest(sid):
        sub = mac[mac["series_id"] == sid].sort_values("date")
        return float(sub.iloc[-1]["value"]) if not sub.empty else None

    def yoy(sid, n):
        sub = mac[mac["series_id"] == sid].sort_values("date")
        if len(sub) < n:
            return None
        return (float(sub.iloc[-1]["value"]) / float(sub.iloc[-n]["value"]) - 1) * 100

    g     = yoy("GDPC1", 5)
    inf   = yoy("PCEPILFE", 13)        # 优先核心PCE
    if inf is None:
        inf = yoy("CPIAUCSL", 12)      # 回退CPI
    rate  = latest("FEDFUNDS")
    unemp = latest("UNRATE")

    g     = g     if g     is not None else 2.5
    inf   = inf   if inf   is not None else 3.0
    rate  = rate  if rate  is not None else 5.3
    unemp = unemp if unemp is not None else 4.1

    if   g > 2.5 and inf < 3.5 and unemp < 4.5: return 8.0
    elif g > 1.5 and inf >= 3.5:                  return 5.0
    elif g < 1.5 and rate > 3.0:                  return 3.0
    elif g < 0   or  unemp > 5.5:                 return 2.0
    else:                                          return 6.0


def _fed_direction_from_snap(mac: pd.DataFrame) -> float:
    """美联储6个月方向修正（与 macro_analyzer._fed_direction_score 逻辑一致）。"""
    if mac.empty:
        return 0.0
    fed = mac[mac["series_id"] == "FEDFUNDS"].sort_values("date")
    if len(fed) < 6:
        return 0.0
    delta = float(fed.iloc[-1]["value"]) - float(fed.iloc[-6]["value"])
    if   delta < -0.25: return +1.5
    elif delta >  0.25: return -1.5
    else:               return  0.0


def _credit_from_snap(mac: pd.DataFrame) -> float:
    """信用利差评分（与 signals._credit_score 同口径）。"""
    if mac.empty:
        return 5.0
    sub = mac[mac["series_id"] == "BAMLH0A0HYM2"].sort_values("date")
    if sub.empty:
        return 5.0
    spread = float(sub.iloc[-1]["value"])
    if   spread < 3.0: return 8.0
    elif spread < 4.0: return 6.5
    elif spread < 5.5: return 5.0
    elif spread < 8.0: return 3.5
    else:              return 2.0


def _trend_from_snap(sp500_snap: pd.Series) -> float:
    """SP500 vs 12个月均线趋势评分（与 signals._trend_score 逻辑一致）。"""
    if len(sp500_snap) < 60:
        return 5.0
    current  = float(sp500_snap.iloc[-1])
    ma252    = float(sp500_snap.tail(252).mean())
    dev      = (current - ma252) / ma252
    if   dev >  0.08: return 8.0
    elif dev >  0.02: return 6.5
    elif dev > -0.02: return 5.0
    elif dev > -0.08: return 3.5
    else:             return 2.0


# ─────────────────────────────────────────────
# 基金评分（无前视偏差版本）
# ─────────────────────────────────────────────

def _score_funds(nav_snap: pd.DataFrame, fund_list: pd.DataFrame,
                 signal: dict, cfg: dict) -> pd.DataFrame:
    """类别相对化评分，与 scorer.py 保持同口径（去掉循环的 timing 因子）。"""
    from ..utils.fund_universe import classify_asset_class, strategy_match_score

    weights   = cfg.get("scoring_weights", {})
    w_perf    = weights.get("performance",    0.30)
    w_risk    = weights.get("risk_adjusted",  0.25)
    w_strat   = weights.get("strategy_match", 0.20)
    w_cost    = weights.get("cost_efficiency",0.15)
    w_consist = weights.get("consistency",    0.10)

    # Pass 1: 所有基金的原始指标
    raw_rows = []
    for _, fund in fund_list.iterrows():
        code = str(fund["fund_code"])
        nav  = nav_snap[nav_snap["fund_code"] == code].sort_values("date")
        if len(nav) < 20:
            continue
        nav_s = nav.set_index("date")["nav"].astype(float)
        perf_raw, ann_returns = _perf_raw(nav_s)
        sharpe, mdd, vol      = _risk_raw(nav_s)
        asset_class = classify_asset_class(
            fund_code=code,
            fund_type=str(fund.get("fund_type", "")),
            fund_name=str(fund.get("fund_name", "")),
            benchmark=str(fund.get("benchmark", "")),
        )
        raw_rows.append({
            "fund_code":     code,
            "fund_name":     str(fund.get("fund_name", code)),
            "asset_class":   asset_class,
            "perf_raw":      perf_raw,
            "sharpe_raw":    sharpe,
            "max_dd_raw":    mdd,
            "vol_raw":       vol,
            "expense_ratio": float(fund.get("expense_ratio") or 0.012),
            "ann_returns":   ann_returns,
        })

    if not raw_rows:
        return pd.DataFrame(columns=["fund_code", "fund_name", "total_score"])

    df_raw = pd.DataFrame(raw_rows)

    # Pass 2: 类别内百分位（0–10）
    df_raw["perf_pct"]   = _category_pct(df_raw, "perf_raw",  "asset_class", low_is_good=False)
    df_raw["sharpe_pct"] = _category_pct(df_raw, "sharpe_raw","asset_class", low_is_good=False)
    df_raw["dd_pct"]     = _category_pct(df_raw, "max_dd_raw","asset_class", low_is_good=False)
    df_raw["vol_pct"]    = _category_pct(df_raw, "vol_raw",   "asset_class", low_is_good=True)

    # Pass 3: 加权合并
    composite = signal["composite_signal"]
    results = []
    for _, row in df_raw.iterrows():
        perf_score    = row["perf_pct"]
        risk_score    = row["sharpe_pct"] * 0.4 + row["dd_pct"] * 0.35 + row["vol_pct"] * 0.25
        strat_score   = strategy_match_score(row["asset_class"], composite)
        cost_score    = _cost_score(row["expense_ratio"], cfg)
        consist_score = _consist_score(row["ann_returns"])

        total = (perf_score  * w_perf + risk_score * w_risk + strat_score * w_strat
                 + cost_score * w_cost + consist_score * w_consist) * 10
        results.append({"fund_code": row["fund_code"], "fund_name": row["fund_name"],
                        "total_score": round(total, 1)})

    return pd.DataFrame(results).sort_values("total_score", ascending=False).reset_index(drop=True)


def _perf_raw(nav_s: pd.Series) -> tuple[float, list]:
    """返回 (加权年化收益, 各期年化收益列表)，不做绝对分对比。"""
    today = nav_s.index[-1]
    periods = [(365, 1.0, 0.4), (365 * 3, 3.0, 0.35), (180, 0.5, 0.25)]
    avail = []
    for days, years, w in periods:
        cut = today - pd.Timedelta(days=days)
        sub = nav_s[nav_s.index >= cut]
        if len(sub) >= 2:
            growth = float(sub.iloc[-1]) / float(sub.iloc[0])
            ann = (growth ** (1.0 / years) - 1) * 100 if growth > 0 else -100.0
            avail.append((ann, w))
    if not avail:
        return 0.0, []
    total_w = sum(w for _, w in avail)
    perf_raw = sum(v * w for v, w in avail) / total_w
    ann_list = [v for v, _ in avail]
    return perf_raw, ann_list


def _risk_raw(nav_s: pd.Series) -> tuple[float, float, float]:
    """返回 (sharpe, max_drawdown%, volatility%)，原始值，不做归一化。"""
    daily = nav_s.pct_change().dropna()
    if len(daily) < 20:
        return 0.0, -20.0, 20.0
    rf     = RF_ANNUAL / 252
    excess = daily - rf
    sharpe = float(excess.mean() / excess.std() * np.sqrt(252)) if excess.std() > 0 else 0.0
    mdd    = float(((nav_s - nav_s.cummax()) / nav_s.cummax()).min()) * 100
    vol    = float(daily.std() * np.sqrt(252)) * 100
    return sharpe, mdd, vol


def _consist_score(ann_returns: list) -> float:
    """跨期收益稳定性（0–10），与 scorer._calc_consistency_score 同口径。"""
    if len(ann_returns) < 2:
        return 5.0
    pos_ratio = sum(1 for v in ann_returns if v > 0) / len(ann_returns)
    std = float(np.std(ann_returns))
    return float(np.clip(pos_ratio * 7.0 + max(0.0, 1.0 - std / 30.0) * 3.0, 0.0, 10.0))


def _category_pct(df: pd.DataFrame, col: str, group_col: str,
                  low_is_good: bool = False, min_group: int = 3) -> pd.Series:
    """类别内百分位排名（0–10）；类别过小时退回全局排名。"""
    asc    = not low_is_good
    result = pd.Series(0.0, index=df.index)
    global_ranks = df[col].rank(pct=True, ascending=asc)
    for _, idx in df.groupby(group_col).groups.items():
        if len(idx) >= min_group:
            ranks = df.loc[idx, col].rank(pct=True, ascending=asc)
        else:
            ranks = global_ranks.loc[idx]
        result.loc[idx] = ranks * 10
    return result.clip(0, 10)


def _strategy_score(fund_row, signal: dict) -> float:
    from ..utils.fund_universe import classify_asset_class, strategy_match_score
    asset_class = classify_asset_class(
        fund_code=str(fund_row.get("fund_code", "")),
        fund_type=str(fund_row.get("fund_type", "")),
        fund_name=str(fund_row.get("fund_name", "")),
        benchmark=str(fund_row.get("benchmark", "")),
    )
    return strategy_match_score(asset_class, signal["composite_signal"])


def _cost_score(er: float, cfg: dict) -> float:
    bp = cfg.get("strategy_params", {}).get("cost_filter", {})
    pref = bp.get("preferred_expense_ratio", 0.005)
    mx   = bp.get("max_expense_ratio", 0.015)
    if er <= pref:  return 10.0
    if er <= mx:    return 10 - (er - pref) / (mx - pref) * 5
    return max(0, 5 - (er - mx) * 100)


# ─────────────────────────────────────────────
# 收益计算
# ─────────────────────────────────────────────

def _portfolio_period_return(nav_df: pd.DataFrame, fund_codes: list,
                              t0: pd.Timestamp, t1: pd.Timestamp) -> float:
    """等权基金组合在 [t0, t1] 的区间收益。"""
    returns = []
    for code in fund_codes:
        fn = nav_df[nav_df["fund_code"] == code].sort_values("date")
        v0 = fn[fn["date"] <= t0]
        v1 = fn[fn["date"] <= t1]
        if v0.empty or v1.empty:
            continue
        v0v = float(v0.iloc[-1]["nav"])
        v1v = float(v1.iloc[-1]["nav"])
        if v0v > 0:
            returns.append(v1v / v0v - 1)
    return float(np.mean(returns)) if returns else 0.0


def _index_period_return(price_series: pd.Series, t0: pd.Timestamp,
                          t1: pd.Timestamp) -> float:
    p0 = price_series[price_series.index <= t0]
    p1 = price_series[price_series.index <= t1]
    if p0.empty or p1.empty:
        return 0.0
    return float(p1.iloc[-1] / p0.iloc[-1] - 1)


def _load_cape_history() -> pd.Series:
    """真实 CAPE 历史序列（来自 valuation_data，按日期升序）。无则返回空 Series。"""
    try:
        df = read_table("valuation_data", "metric = ? ORDER BY date", ("cape",))
    except Exception:
        return pd.Series(dtype=float)
    if df is None or df.empty:
        return pd.Series(dtype=float)
    s = df.copy()
    s["date"] = pd.to_datetime(s["date"])
    return s.set_index("date")["value"].astype(float).sort_index()


def _fetch_sp500_full(market_db: pd.DataFrame) -> pd.Series:
    """
    返回尽可能长的 SP500 历史序列（DB + yfinance 补全）。
    """
    db_sp500 = (market_db[market_db["symbol"] == "^GSPC"]
                .set_index("date")["close"]
                .sort_index())

    try:
        import yfinance as yf
        # 用 yf.download 比 Ticker.history 更稳定
        raw = yf.download("^GSPC", start="2019-01-01", auto_adjust=True,
                          progress=False, show_errors=False)
        if raw is not None and not raw.empty:
            # yfinance 返回 MultiIndex 列时取 Close
            if isinstance(raw.columns, pd.MultiIndex):
                close = raw["Close"]["^GSPC"] if "^GSPC" in raw["Close"].columns else raw["Close"].iloc[:, 0]
            else:
                close = raw["Close"]
            close.index = pd.to_datetime(close.index).tz_localize(None)
            yf_series = close.dropna().rename("close")
            combined = yf_series.combine_first(db_sp500)
            return combined.sort_index()
    except Exception:
        pass

    return db_sp500


# ─────────────────────────────────────────────
# 绩效指标
# ─────────────────────────────────────────────

def calc_metrics(returns: pd.Series, label: str = "") -> dict:
    """计算核心绩效指标（月度收益序列输入）。"""
    if returns.empty or len(returns) < 3:
        return {"label": label, "total_return": 0, "annualized_return": 0,
                "sharpe_ratio": 0, "max_drawdown": 0, "volatility": 0,
                "win_rate": 0, "n_months": 0}

    n_months = len(returns)
    n_years  = n_months / 12
    total    = float((1 + returns).prod() - 1)
    ann      = float((1 + total) ** (1 / n_years) - 1) if n_years > 0 else 0

    rf_m  = RF_ANNUAL / 12
    exc   = returns - rf_m
    sharpe = float(exc.mean() / exc.std() * np.sqrt(12)) if exc.std() > 0 else 0

    cum   = (1 + returns).cumprod()
    mdd   = float(((cum - cum.cummax()) / cum.cummax()).min())
    vol   = float(returns.std() * np.sqrt(12))
    wr    = float((returns > 0).mean())

    return {
        "label":             label,
        "total_return":      round(total * 100, 2),
        "annualized_return": round(ann   * 100, 2),
        "sharpe_ratio":      round(sharpe, 3),
        "max_drawdown":      round(mdd * 100, 2),
        "volatility":        round(vol  * 100, 2),
        "win_rate":          round(wr   * 100, 1),
        "n_months":          n_months,
    }


def _drawdown_series(cum: pd.Series) -> pd.Series:
    rolling_max = cum.cummax()
    return ((cum - rolling_max) / rolling_max * 100).round(2)


# ─────────────────────────────────────────────
# 信号有效性分析
# ─────────────────────────────────────────────

def _signal_accuracy(df: pd.DataFrame) -> pd.DataFrame:
    """
    验证各类信号对次月市场方向的预测能力。
    有效信号：重仓进取 → SP500应上涨；减仓防守 → SP500应下跌/跑输。
    """
    order = ["重仓进取", "标配稳健", "谨慎防守", "减仓防守"]
    rows = []
    for sig in order:
        sub = df[df["signal"] == sig]
        if sub.empty:
            continue
        rows.append({
            "信号":          sig,
            "出现次数":      len(sub),
            "SP500次月均收益%": round(sub["sp500_return"].mean() * 100, 2),
            "策略次月均收益%":  round(sub["strat_return"].mean()  * 100, 2),
            "SP500上涨概率%":  round((sub["sp500_return"] > 0).mean() * 100, 1),
            "平均投资比例%":   round(sub["invested"].mean() * 100, 1),
        })
    return pd.DataFrame(rows)
