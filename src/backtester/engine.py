"""
走向前回测引擎 (Walk-Forward Backtest)
无前视偏差：每个调仓日仅使用截至该日的历史数据

⚠️ 幸存者偏差说明：基金池来自当前仍在运作的核心QDII列表，已清盘/合并/改名
的基金未纳入。因此回测结果对“当年可选基金”是乐观估计——真实历史中部分被
本回测选中的基金在早期可能尚未成立，未被选中的失败基金也已消失。解读时应把
策略表现视为上界而非可复现收益。结果中的 survivorship_note 字段同步披露此点。
"""
from typing import Optional
import pandas as pd
import numpy as np
from datetime import datetime
from ..utils.database import read_table
from ..utils.config import load_config
from ..domain.scoring import (
    category_percentile,
    consistency_score,
    cost_score,
    classify_signal,
    credit_score_from_spread,
    trend_score_from_deviation,
)
from ..domain.factor_config import FACTOR_WEIGHTS as _FACTOR_WEIGHTS, REGION_WEIGHTS_QDII as _REGION_WEIGHTS_BACKTEST


# QDII ETF 场内双边摩擦（手续费+买卖价差+汇率）：实测约 0.3–0.5%，保守取 0.5%
# 开放式 QDII（申购1%+赎回0.5%）更高；此处统一按场内 ETF 保守估算
TRANSACTION_COST_RT = 0.005  # 0.5% 双边（round-trip），按实际换手率扩展
RF_ANNUAL = 0.02             # 无风险利率假设 2%



# ─────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────

def run_backtest(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    top_n: int = 5,
    rebalance_freq: str = "MS",
    cape_overvalued: Optional[float] = None,      # None = 使用 settings.yaml 值
    min_cash_pct: Optional[float] = None,         # 强制最低现金下限（0~0.5），None = 不覆盖
    correct_survivorship: bool = True,  # 是否同步运行成立日期过滤的对照组
    factor_weights: Optional[dict] = None,        # None = 使用 _FACTOR_WEIGHTS 默认权重
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
    fw  = factor_weights or _FACTOR_WEIGHTS
    # 参数覆盖
    if cape_overvalued is not None:
        cfg.setdefault("strategy_params", {}).setdefault("valuation_thresholds", {})["cape_overvalued"] = cape_overvalued

    fund_nav       = read_table("fund_nav_history")
    market_db      = read_table("market_data")
    macro_db       = read_table("macro_data")
    fund_list      = read_table("fund_list")
    global_macro_db = read_table("global_macro")   # 全球宏观（World Bank + OECD CLI）
    cape_hist      = _load_cape_history()   # 真实 CAPE 历史

    # 成立日期映射（用于幸存者偏差修正，缺失时跳过修正）
    inception_map: dict[str, str] = {}
    if "inception_date" in fund_list.columns:
        for _, row in fund_list.iterrows():
            d = row.get("inception_date")
            if d and pd.notna(d):
                inception_map[str(row["fund_code"])] = str(d)[:10]

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

    # 净值表按基金预切片一次，供下方各调仓期的区间收益查找复用（避免整表反复过滤）
    nav_by_code = _index_nav_by_code(fund_nav)

    records = []
    # 等权基准：全仓买入所有可用基金，不择时不择基，隔离"选基"vs"择时"贡献
    ewbh_all_codes = fund_list["fund_code"].astype(str).tolist()
    prev_selected: set[str] = set()    # 上期持仓，用于计算换手率
    prev_selected_corr: set[str] = set()  # 幸存者修正组的上期持仓
    n_premature_total = 0              # 累计"尚未成立"基金数

    for i in range(len(rebalance_dates) - 1):
        t0 = rebalance_dates[i]
        t1 = rebalance_dates[i + 1]
        t0_str = t0.strftime("%Y-%m-%d")

        # 截至 t0 的数据快照（严格无前视偏差）
        nav_snap   = fund_nav[fund_nav["date"] <= t0]
        mkt_snap   = market_db[market_db["date"] <= t0]
        mac_snap   = macro_db[macro_db["date"] <= t0] if not macro_db.empty else pd.DataFrame()
        sp500_snap = sp500_full[sp500_full.index <= t0]

        # 真实 CAPE 截至 t0 的快照（无前视）
        cape_snap = cape_hist[cape_hist.index <= t0] if cape_hist is not None and not cape_hist.empty else None

        # 全球宏观截至 t0 的快照（年份字符串比较）
        global_mac_snap = (
            global_macro_db[global_macro_db["date"].astype(str).str[:4] <= t0_str[:4]]
            if not global_macro_db.empty else pd.DataFrame()
        )

        # 生成市场信号（6因子，含全球宏观）
        signal = _compute_signal(sp500_snap, mkt_snap, mac_snap, cfg, cape_snap,
                                  global_mac_snap=global_mac_snap, factor_weights=fw)

        # 评分并选基金（全量基金池）
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
        port_ret = _portfolio_period_return(nav_by_code, selected_codes, t0, t1)
        strat_ret = port_ret * invested - TRANSACTION_COST_RT * turnover * invested

        # 基准1：标普500买入持有
        sp500_ret = _index_period_return(sp500_full, t0, t1)
        # 基准2：60/40（标普500 60% + 无风险现金 40%）
        b6040_ret = sp500_ret * 0.6 + (RF_ANNUAL / 12) * 0.4
        # 基准3：等权全仓基金买入持有（无择时无择基，隔离信号贡献）
        ewbh_ret  = _portfolio_period_return(nav_by_code, ewbh_all_codes, t0, t1)

        rec = {
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
        }

        # ── 幸存者偏差修正对照组（仅使用成立日期 <= t0 的基金）──────
        if correct_survivorship and inception_map:
            available = [c for c in ewbh_all_codes
                         if inception_map.get(c, "2000-01-01") <= t0_str]
            n_premature = len(ewbh_all_codes) - len(available)
            n_premature_total += n_premature

            if available:
                fl_available = fund_list[fund_list["fund_code"].astype(str).isin(available)]
                scored_corr = _score_funds(nav_snap, fl_available, signal, cfg)
                sel_corr = scored_corr.head(top_n)["fund_code"].tolist()

                cur_set_corr = set(sel_corr)
                if i == 0:
                    turnover_corr = 1.0
                else:
                    n_t = max(len(cur_set_corr | prev_selected_corr), 1)
                    turnover_corr = len(cur_set_corr.symmetric_difference(prev_selected_corr)) / n_t
                prev_selected_corr = cur_set_corr

                port_ret_corr = _portfolio_period_return(nav_by_code, sel_corr, t0, t1)
                rec["corrected_return"] = (
                    port_ret_corr * invested - TRANSACTION_COST_RT * turnover_corr * invested
                )
                rec["available_funds"] = len(available)
                rec["premature_funds"] = n_premature

        records.append(rec)

    df = pd.DataFrame(records).set_index("date")

    # 累计净值序列（初始 = 1.0）
    df["strat_cum"]  = (1 + df["strat_return"]).cumprod()
    df["sp500_cum"]  = (1 + df["sp500_return"]).cumprod()
    df["b6040_cum"]  = (1 + df["b6040_return"]).cumprod()
    df["ewbh_cum"]   = (1 + df["ewbh_return"]).cumprod()

    # 最大回撤序列（用于画图）
    df["strat_dd"]  = _drawdown_series(df["strat_cum"])
    df["sp500_dd"]  = _drawdown_series(df["sp500_cum"])

    # 幸存者偏差修正指标（当 corrected_return 列存在时）
    corrected_metrics = None
    surv_stats: dict = {}
    if "corrected_return" in df.columns:
        corrected_metrics = calc_metrics(df["corrected_return"].dropna(), "幸存者修正策略")
        if "premature_funds" in df.columns:
            surv_stats = {
                "periods_with_premature": int((df["premature_funds"] > 0).sum()),
                "avg_premature_per_period": round(df["premature_funds"].mean(), 1),
                "total_premature_instances": int(n_premature_total),
            }

    n_funds_with_inception = len(inception_map)
    surv_note = (
        f"基金池为当前在运作的 {len(fund_list)} 只核心QDII（其中 {n_funds_with_inception} 只有成立日期）"
        f"，未含已清盘/改名基金；策略收益为乐观上界，非可复现实盘收益。"
        + (f" 幸存者修正对照组基于成立日期过滤，"
           f"平均每期剔除 {surv_stats.get('avg_premature_per_period', 0):.1f} 只未成立基金。"
           if surv_stats else "")
    )

    return {
        "df":                    df,
        "strat_metrics":         calc_metrics(df["strat_return"],  "本策略（动态配置）"),
        "sp500_metrics":         calc_metrics(df["sp500_return"],  "标普500（买入持有）"),
        "b6040_metrics":         calc_metrics(df["b6040_return"],  "60/40 组合"),
        "ewbh_metrics":          calc_metrics(df["ewbh_return"],   "等权基金买入持有"),
        "corrected_strat_metrics": corrected_metrics,          # 幸存者偏差修正对照组
        "survivorship_stats":    surv_stats,
        "signal_stats":          _signal_accuracy(df),
        "start_date":            rebalance_dates[0].strftime("%Y-%m-%d"),
        "end_date":              rebalance_dates[-1].strftime("%Y-%m-%d"),
        "n_periods":             len(df),
        "fund_list":             fund_list,
        "data_source":           _backtest_data_source(),
        "factor_weights":        fw,
        "survivorship_note":     surv_note,
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
                    cape_snap: Optional[pd.Series] = None,
                    global_mac_snap: Optional[pd.DataFrame] = None,
                    factor_weights: Optional[dict] = None) -> dict:
    """
    用截止日期快照重算市场信号（与 signals.py 逻辑严格对应，6因子版本）。
    估值优先用真实 CAPE 历史（cape_snap，截至 t0，无前视）；缺失时回退点位近似。
    factor_weights 允许逐因子归因实验（屏蔽某因子时传入调整后权重）。
    """
    fw = factor_weights or _FACTOR_WEIGHTS

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

    # ⑦ 全球宏观综合评分（新第6因子）
    global_macro = _global_macro_score_from_snap(global_mac_snap)

    # 6因子加权综合评分
    raw = (
        macro_adj   * fw.get("macro",        0.18)
        + val_score * fw.get("valuation",    0.18)
        + contrarian * fw.get("sentiment",   0.135)
        + trend      * fw.get("trend",       0.27)
        + credit     * fw.get("credit",      0.135)
        + global_macro * fw.get("global_macro", 0.10)
    )

    sig, c, s, ca = classify_signal(raw)

    return {
        "composite_signal": sig, "composite_raw": raw,
        "core_allocation": c, "satellite_allocation": s, "cash_allocation": ca,
        "cape": cape, "vix": vix, "trend_score": trend,
        "global_macro_score": global_macro,
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
    if mac.empty:
        return 5.0
    sub = mac[mac["series_id"] == "BAMLH0A0HYM2"].sort_values("date")
    if sub.empty:
        return 5.0
    return credit_score_from_spread(float(sub.iloc[-1]["value"]))


def _global_macro_score_from_snap(snap: pd.DataFrame) -> float:
    """从 global_macro 快照计算加权全球宏观评分（0-10）。与 production 端对称。"""
    if snap is None or snap.empty:
        return 5.0

    weighted_sum = 0.0
    total_weight = 0.0

    for region, g in snap.groupby("region"):
        def _latest_val(ind):
            sub = g[g["indicator"] == ind].sort_values("date")
            return float(sub.iloc[-1]["value"]) if not sub.empty and pd.notna(sub.iloc[-1]["value"]) else None

        gdp   = _latest_val("gdp_growth")
        inf   = _latest_val("inflation")
        unemp = _latest_val("unemployment")
        cli   = _latest_val("cli")

        if all(x is None for x in (gdp, inf, unemp, cli)):
            continue

        score = _region_score_engine(gdp, inf, unemp, cli)
        w = _REGION_WEIGHTS_BACKTEST.get(region, 0.05)
        weighted_sum += w * score
        total_weight += w

    return round(weighted_sum / total_weight, 2) if total_weight > 0 else 5.0


def _region_score_engine(gdp, inf, unemp, cli) -> float:
    """与 global_macro_analyzer._region_score 逻辑一致，内联避免跨模块依赖。"""
    score = 5.0
    if gdp is not None:
        if gdp >= 3.0:   score += 2.0
        elif gdp >= 1.5: score += 1.0
        elif gdp < 0:    score -= 2.0
    if inf is not None:
        if 1.0 <= inf <= 3.0:  score += 1.0
        elif inf > 5.0:        score -= 1.5
        elif inf > 3.0:        score -= 0.5
        elif inf < 0:          score -= 1.0
    if unemp is not None:
        if unemp <= 4.0:  score += 1.0
        elif unemp > 6.0: score -= 1.0
    if cli is not None:
        if cli >= 100.5:  score += 1.0
        elif cli < 99.5:  score -= 1.0
    return max(0.0, min(10.0, score))


def _trend_from_snap(sp500_snap: pd.Series) -> float:
    if len(sp500_snap) < 60:
        return 5.0
    current = float(sp500_snap.iloc[-1])
    ma252 = float(sp500_snap.tail(252).mean())
    return trend_score_from_deviation((current - ma252) / ma252)


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
    df_raw["perf_pct"]   = category_percentile(df_raw, "perf_raw",  "asset_class", low_is_good=False)
    df_raw["sharpe_pct"] = category_percentile(df_raw, "sharpe_raw","asset_class", low_is_good=False)
    df_raw["dd_pct"]     = category_percentile(df_raw, "max_dd_raw","asset_class", low_is_good=False)
    df_raw["vol_pct"]    = category_percentile(df_raw, "vol_raw",   "asset_class", low_is_good=True)

    # Pass 3: 加权合并
    composite = signal["composite_signal"]
    results = []
    for _, row in df_raw.iterrows():
        perf_score    = row["perf_pct"]
        risk_score    = row["sharpe_pct"] * 0.4 + row["dd_pct"] * 0.35 + row["vol_pct"] * 0.25
        strat_score   = strategy_match_score(row["asset_class"], composite)
        cost_score_val = cost_score(row["expense_ratio"], cfg)
        consist_score = consistency_score(row["ann_returns"])

        total = (perf_score    * w_perf + risk_score   * w_risk + strat_score * w_strat
                 + cost_score_val * w_cost + consist_score * w_consist) * 10
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


def _strategy_score(fund_row, signal: dict) -> float:
    from ..utils.fund_universe import classify_asset_class, strategy_match_score
    asset_class = classify_asset_class(
        fund_code=str(fund_row.get("fund_code", "")),
        fund_type=str(fund_row.get("fund_type", "")),
        fund_name=str(fund_row.get("fund_name", "")),
        benchmark=str(fund_row.get("benchmark", "")),
    )
    return strategy_match_score(asset_class, signal["composite_signal"])


# ─────────────────────────────────────────────
# 收益计算
# ─────────────────────────────────────────────

def _index_nav_by_code(nav_df: pd.DataFrame) -> dict:
    """把整张净值表按 fund_code 预切片为 {code: 以 date 升序为索引的 nav Series}。

    回测主循环对每个调仓期、每只基金各取一次区间端点净值；若每次都对整表做
    `nav_df[nav_df["fund_code"]==code]` 布尔过滤，复杂度为 O(periods×funds×rows)。
    这里一次性 groupby 预切片，循环内改用 Series.asof() 做 O(log n) 端点查找。
    """
    out = {}
    for code, g in nav_df.groupby("fund_code"):
        s = g.sort_values("date").set_index("date")["nav"].astype(float)
        out[str(code)] = s
    return out


def _portfolio_period_return(nav_by_code: dict, fund_codes: list,
                              t0: pd.Timestamp, t1: pd.Timestamp) -> float:
    """等权基金组合在 [t0, t1] 的区间收益。

    nav_by_code 为 `_index_nav_by_code` 的预切片结果。asof(t) 返回索引 <= t 的
    最后一个净值（即「t 当日或之前的最新净值」），与旧版 `[date<=t].iloc[-1]` 等价；
    端点早于该基金首条净值时 asof 返回 NaN，与旧版的 empty 跳过等价。
    """
    returns = []
    for code in fund_codes:
        s = nav_by_code.get(str(code))
        if s is None or s.empty:
            continue
        v0v = s.asof(t0)
        v1v = s.asof(t1)
        if pd.isna(v0v) or pd.isna(v1v):
            continue
        v0v = float(v0v)
        if v0v > 0:
            returns.append(float(v1v) / v0v - 1)
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
        # 注意：show_errors 参数在 yfinance>=0.2.x 已移除，传入会抛 TypeError，故不再传递。
        raw = yf.download("^GSPC", start="2019-01-01", auto_adjust=True,
                          progress=False)
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
    except Exception as e:
        # 不再静默吞掉：补全失败会缩短回测历史区间，必须让用户可见
        print(f"[WARN] yfinance 补全 SP500 历史失败，仅使用数据库内 SP500（回测区间可能偏短）: {e}")

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

def _ablate_weights(base: dict, factor: str) -> dict:
    """将 factor 权重置 0，其余等比放大使总和仍为 1.0。"""
    ablated = {k: (0.0 if k == factor else v) for k, v in base.items()}
    total = sum(ablated.values())
    return {k: v / total for k, v in ablated.items()} if total > 0 else base


def run_factor_attribution(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    top_n: int = 5,
    rebalance_freq: str = "MS",
) -> dict:
    """逐因子屏蔽回测，量化各因子对策略年化收益的边际贡献。

    方法：
      1. 以默认 6 因子权重（_FACTOR_WEIGHTS）作为基准回测；
      2. 对每个因子依次将其权重置 0，剩余因子按比例放大；
      3. 对比基准 vs 屏蔽后的年化收益，差值即该因子的贡献（正 = 有益）。

    Returns:
        {
          "base_metrics":      calc_metrics of baseline,
          "base_annual_return": float,
          "factors": {
            factor_name: {
              "base_weight":       float,
              "ablated_metrics":   calc_metrics dict,
              "ablated_annual":    float,
              "contribution_pct":  float,   # 基准 - 屏蔽后（年化%）
              "contribution_label": str,
            }, ...
          }
        }
    """
    # 加载一次数据，7次共用
    cfg        = load_config()
    fund_nav   = read_table("fund_nav_history")
    market_db  = read_table("market_data")
    macro_db   = read_table("macro_data")
    fund_list  = read_table("fund_list")
    global_macro_db = read_table("global_macro")
    cape_hist  = _load_cape_history()
    sp500_full = _fetch_sp500_full(market_db)

    if fund_nav.empty:
        return {"error": "无基金净值数据"}

    fund_nav["date"]  = pd.to_datetime(fund_nav["date"])
    market_db["date"] = pd.to_datetime(market_db["date"])
    if not macro_db.empty:
        macro_db["date"] = pd.to_datetime(macro_db["date"])

    data_start = max(
        fund_nav["date"].min() + pd.DateOffset(months=6),
        macro_db["date"].min() + pd.DateOffset(months=12) if not macro_db.empty else pd.Timestamp("2022-01-01"),
        sp500_full.index.min() + pd.DateOffset(months=1),
    )
    data_end = fund_nav["date"].max()

    bt_start = pd.to_datetime(start_date) if start_date else data_start
    bt_end   = pd.to_datetime(end_date)   if end_date   else data_end
    dates    = pd.date_range(start=bt_start, end=bt_end, freq=rebalance_freq)

    if len(dates) < 4:
        return {"error": "回测区间过短"}

    ewbh_codes = fund_list["fund_code"].astype(str).tolist()
    # 净值表预切片一次，7 次权重回测、每次所有调仓期共用（避免整表反复过滤）
    nav_by_code_fa = _index_nav_by_code(fund_nav)

    def _run_with_weights(fw: dict) -> pd.Series:
        """内部快速回测循环，返回月度收益序列。"""
        prev: set[str] = set()
        rets = []
        for i in range(len(dates) - 1):
            t0, t1 = dates[i], dates[i + 1]
            t0_str = t0.strftime("%Y-%m-%d")

            nav_snap    = fund_nav[fund_nav["date"] <= t0]
            mkt_snap    = market_db[market_db["date"] <= t0]
            mac_snap    = macro_db[macro_db["date"] <= t0] if not macro_db.empty else pd.DataFrame()
            sp500_snap  = sp500_full[sp500_full.index <= t0]
            cape_snap   = cape_hist[cape_hist.index <= t0] if cape_hist is not None and not cape_hist.empty else None
            gm_snap     = (global_macro_db[global_macro_db["date"].astype(str).str[:4] <= t0_str[:4]]
                           if not global_macro_db.empty else pd.DataFrame())

            sig = _compute_signal(sp500_snap, mkt_snap, mac_snap, cfg, cape_snap,
                                  global_mac_snap=gm_snap, factor_weights=fw)

            scored = _score_funds(nav_snap, fund_list, sig, cfg)
            sel = scored.head(top_n)["fund_code"].tolist()

            cur = set(sel)
            turnover = 1.0 if i == 0 else len(cur.symmetric_difference(prev)) / max(len(cur | prev), 1)
            prev = cur

            invested = sig["core_allocation"] + sig["satellite_allocation"]
            port_ret = _portfolio_period_return(nav_by_code_fa, sel, t0, t1)
            rets.append(port_ret * invested - TRANSACTION_COST_RT * turnover * invested)

        return pd.Series(rets)

    print("[因子归因] 基准回测...")
    base_rets    = _run_with_weights(_FACTOR_WEIGHTS)
    base_metrics = calc_metrics(base_rets, "基准（6因子）")
    base_ann     = base_metrics["annualized_return"]

    factors: dict = {}
    factor_labels = {
        "trend":        "价格趋势",
        "macro":        "宏观周期",
        "valuation":    "市场估值",
        "sentiment":    "逆向情绪",
        "credit":       "信用利差",
        "global_macro": "全球宏观",
    }

    for fname in _FACTOR_WEIGHTS:
        label = factor_labels.get(fname, fname)
        print(f"[因子归因] 屏蔽 {label}...")
        ablated_fw   = _ablate_weights(_FACTOR_WEIGHTS, fname)
        ablated_rets = _run_with_weights(ablated_fw)
        ablated_met  = calc_metrics(ablated_rets, f"屏蔽{label}")
        ablated_ann  = ablated_met["annualized_return"]
        contribution = base_ann - ablated_ann

        if contribution > 1.5:
            c_label = "★★ 强正贡献"
        elif contribution > 0.3:
            c_label = "★ 正贡献"
        elif contribution > -0.3:
            c_label = "◇ 中性"
        elif contribution > -1.5:
            c_label = "▽ 负贡献"
        else:
            c_label = "▼▼ 强负贡献"

        factors[fname] = {
            "label":              label,
            "base_weight":        _FACTOR_WEIGHTS[fname],
            "ablated_weights":    ablated_fw,
            "ablated_metrics":    ablated_met,
            "ablated_annual":     ablated_ann,
            "contribution_pct":   round(contribution, 2),
            "contribution_label": c_label,
        }

    return {
        "base_metrics":      base_metrics,
        "base_annual_return": base_ann,
        "factors":           factors,
        "start_date":        dates[0].strftime("%Y-%m-%d"),
        "end_date":          dates[-1].strftime("%Y-%m-%d"),
        "n_periods":         len(dates) - 1,
    }


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
