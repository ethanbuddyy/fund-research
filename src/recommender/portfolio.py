"""投资组合构建与建议"""
import json
import pandas as pd
from pathlib import Path
from ..utils.database import read_table
from ..utils.config import load_config
from ..domain.types import MarketSignal, PortfolioRecommendation

_SNAPSHOT_PATH = Path(__file__).parent.parent.parent / "data" / "portfolio_snapshot.json"


CORE_BENCHMARKS = ["标普500", "S&P", "纳斯达克100", "MSCI全球", "全球"]
SATELLITE_BENCHMARKS = ["科技", "医疗", "能源", "亚洲", "主动"]


def build_portfolio_recommendation(market_signal: MarketSignal, top_n: int = 10) -> PortfolioRecommendation:
    scores_df = read_table("fund_scores")
    funds_df = read_table("fund_list")
    cfg = load_config()

    if scores_df.empty:
        return _empty_portfolio(market_signal)

    merged = scores_df.merge(
        funds_df[["fund_code", "fund_type", "fund_name", "expense_ratio"]],
        on="fund_code", how="left", suffixes=("", "_info")
    )
    merged = merged.sort_values("total_score", ascending=False)

    core_alloc = market_signal.get("core_allocation", 0.60)
    satellite_alloc = market_signal.get("satellite_allocation", 0.30)
    cash_alloc = market_signal.get("cash_allocation", 0.10)

    score_threshold = cfg.get("rebalancing", {}).get("score_threshold", 10)
    prev_core_scores, prev_sat_scores = _load_previous_codes()

    # 核心仓位：宽基指数
    core_funds = _select_core_funds(merged, core_alloc, prev_core_scores, score_threshold)
    # 卫星仓位：行业/主动/主题
    satellite_funds = _select_satellite_funds(merged, satellite_alloc,
                                              exclude_codes={f["fund_code"] for f in core_funds},
                                              prev_scores=prev_sat_scores,
                                              score_threshold=score_threshold)

    total_invested = core_alloc + satellite_alloc
    portfolio: PortfolioRecommendation = {
        "composite_signal": market_signal.get("composite_signal"),
        "core_allocation_pct": round(core_alloc * 100, 0),
        "satellite_allocation_pct": round(satellite_alloc * 100, 0),
        "cash_allocation_pct": round(cash_alloc * 100, 0),
        "core_funds": core_funds,
        "satellite_funds": satellite_funds,
        "total_invested_pct": round(total_invested * 100, 0),
        "top_picks": merged.head(top_n).to_dict("records"),
        "investment_notes": _generate_notes(market_signal),
    }

    # ── AI 阶段二：投资决策增强（配置开关控制）──────────
    cfg_ai = cfg.get("ai_analysis", {})
    if cfg_ai.get("enabled", False) and market_signal.get("ai_analysis") is not None:
        try:
            from ..ai.phase2_portfolio_advisor import PortfolioAdvisor
            ai_decision = PortfolioAdvisor().advise(
                market_signal=market_signal,
                ai_phase1=market_signal["ai_analysis"],
                portfolio=portfolio,
            )
            if ai_decision:
                notes = ai_decision.get("position_sizing_notes")
                if notes:
                    portfolio["investment_notes"] = notes
                portfolio["ai_decision"] = ai_decision
        except Exception as e:
            print(f"[AI Phase2] 跳过: {e}")

    _save_portfolio_snapshot(core_funds, satellite_funds, merged[["fund_code", "total_score"]])
    return portfolio


def _extract_scores(bucket: dict) -> dict[str, float]:
    """从快照 bucket 中提取 {code: score}，兼容新格式 {code: dict} 和旧格式 {code: float}。"""
    result = {}
    for code, val in (bucket or {}).items():
        if isinstance(val, dict):
            result[code] = float(val.get("score", 0.0))
        else:
            try:
                result[code] = float(val)
            except (TypeError, ValueError):
                result[code] = 0.0
    return result


def _load_previous_codes() -> tuple[dict[str, float], dict[str, float]]:
    """读取上次推荐组合，返回 (core_scores, satellite_scores) 两个 {code: score} 字典。
    首次运行或文件缺失/格式旧版时返回两个空字典（不触发门槛约束）。
    """
    if not _SNAPSHOT_PATH.exists():
        return {}, {}  # 首次运行属正常，不告警
    try:
        raw = json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and "core" in raw and "satellite" in raw:
            return _extract_scores(raw["core"]), _extract_scores(raw["satellite"])
        return {}, {}
    except Exception as e:
        # 文件存在却读不出 = 损坏，换仓门槛会失效（本期所有持仓都按新基计算），必须可见。
        print(f"[WARN] 组合快照损坏，换仓门槛本期不生效: {e}")
        return {}, {}


def _get_latest_navs(fund_codes: list) -> dict:
    """从 fund_nav_history 查各基金最新净值，用于快照记录（止损追踪基准）。"""
    try:
        from ..utils.database import get_connection
        conn = get_connection()
        nav_map = {}
        for code in fund_codes:
            row = conn.execute(
                "SELECT nav FROM fund_nav_history WHERE fund_code=? ORDER BY date DESC LIMIT 1",
                (code,),
            ).fetchone()
            if row and row[0] is not None:
                nav_map[code] = float(row[0])
        conn.close()
        return nav_map
    except Exception:
        return {}


def _save_portfolio_snapshot(core_funds: list, satellite_funds: list, scores_df: pd.DataFrame):
    """将本次推荐的基金代码+评分+权重+净值写入快照（止损追踪与换仓门槛共用）。"""
    try:
        from datetime import datetime as _dt
        score_map = dict(zip(scores_df["fund_code"].astype(str), scores_df["total_score"].astype(float)))
        all_codes = [f["fund_code"] for f in core_funds + satellite_funds]
        nav_map = _get_latest_navs(all_codes)

        def _info(f):
            return {
                "score": score_map.get(f["fund_code"], 0.0),
                "weight_pct": f.get("weight", 0.0),   # 已是 pct（如 20.0 表示 20%）
                "nav": nav_map.get(f["fund_code"]),    # 快照时点净值，止损追踪基准
            }

        snapshot = {
            "date": _dt.now().strftime("%Y-%m-%d"),
            "core": {f["fund_code"]: _info(f) for f in core_funds},
            "satellite": {f["fund_code"]: _info(f) for f in satellite_funds},
        }
        _SNAPSHOT_PATH.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        # 快照保存是不可降级的写操作：失败会让下次「换仓门槛」与「止损追踪」静默失效，
        # 必须让用户可见，而非静默吞掉。
        print(f"[WARN] 组合快照保存失败（将影响下次换仓门槛/止损追踪）: {e}")


def _select_core_funds(df: pd.DataFrame, alloc: float,
                       prev_scores: dict, score_threshold: float) -> list:
    """选取核心仓位（宽基指数，最多3只）。"""
    pool = df[df["fund_name"].str.contains("|".join(CORE_BENCHMARKS), na=False)]
    if pool.empty:
        pool = df[df["fund_type"].str.contains("ETF|指数|被动", na=False)]
    return _select_funds(pool, alloc, max_n=3, prev_scores=prev_scores,
                         score_threshold=score_threshold, role="核心")


def _select_satellite_funds(df: pd.DataFrame, alloc: float, exclude_codes: set,
                             prev_scores: dict, score_threshold: float) -> list:
    """选取卫星仓位（行业/主动/主题，最多2只）。"""
    sat = df[~df["fund_code"].isin(exclude_codes)]
    pool = sat[sat["fund_type"].str.contains("主动|LOF|行业|主题", na=False)]
    if pool.empty:
        pool = sat
    return _select_funds(pool, alloc, max_n=2, prev_scores=prev_scores,
                         score_threshold=score_threshold, role="卫星")


def _select_funds(pool: pd.DataFrame, alloc: float, max_n: int,
                  prev_scores: dict, score_threshold: float, role: str) -> list:
    """
    换仓门槛选基：首次运行直接取前 max_n；后续运行保留旧持仓，
    只有新基金比最低分旧持仓高出 score_threshold 分才替换。
    """
    candidate_codes = pool["fund_code"].astype(str).tolist()

    if not prev_scores:
        selected = candidate_codes[:max_n]
    else:
        selected: list[str] = [c for c in prev_scores if c in candidate_codes][:max_n]
        for code in candidate_codes:
            if code in selected:
                continue
            score_series = pool.loc[pool["fund_code"].astype(str) == code, "total_score"]
            if score_series.empty:
                continue
            score = float(score_series.iloc[0])
            if len(selected) < max_n:
                selected.append(code)
            else:
                min_code = min(selected, key=lambda c: prev_scores.get(c, 0.0))
                if score >= prev_scores.get(min_code, 0.0) + score_threshold:
                    selected.remove(min_code)
                    selected.append(code)
            if len(selected) >= max_n:
                break

    picks = pool[pool["fund_code"].astype(str).isin(selected)].head(max_n)
    n = len(picks)
    if n == 0:
        return []
    weight = round(alloc / n * 100, 1)
    return [
        {
            "fund_code":          str(row["fund_code"]),
            "fund_name":          row.get("fund_name", row["fund_code"]),
            "fund_type":          row.get("fund_type", ""),
            "signal":             row.get("signal", "持有"),
            "score":              row.get("total_score", 0),
            "performance_score":  row.get("performance_score"),
            "risk_score":         row.get("risk_score"),
            "strategy_score":     row.get("strategy_score"),
            "consistency_score":  row.get("consistency_score"),
            "cost_score":         row.get("cost_score"),
            "expense_ratio":      row.get("expense_ratio"),
            "weight":             weight,
            "role":               role,
        }
        for _, row in picks.iterrows()
    ]


def _generate_notes(market_signal: dict) -> list[str]:
    signal = market_signal.get("composite_signal", "标配稳健")
    macro = market_signal.get("macro", {})
    notes = []

    if signal == "重仓进取":
        notes.append("市场信号积极，可适当提高股票仓位，加配成长型QDII")
        notes.append("博格策略：维持定投，市场上涨中持续积累份额")
    elif signal == "标配稳健":
        notes.append("市场处于合理区间，维持标配，核心指数基金为主")
        notes.append("格雷厄姆提示：当前估值中性，逢回调加仓更佳")
    elif signal == "谨慎防守":
        notes.append("市场存在隐忧，降低卫星仓比例，提高现金储备")
        notes.append("巴菲特提醒：保持耐心，等待更高安全边际的入场机会")
    else:
        notes.append("市场风险偏高，大幅提高现金比例，保守防守")
        notes.append("危机即机遇：分批建仓计划，为未来上涨做好准备")

    if macro.get("yield_inverted"):
        notes.append("警示：收益率曲线倒挂，历史上衰退先行指标，需提高防守意识")

    # 区域宏观背景（World Bank/OECD）：提示强弱区域，辅助多区域QDII取舍
    gm = market_signal.get("global_macro", {})
    if gm.get("available") and gm.get("regions"):
        strongest, weakest = gm.get("strongest"), gm.get("weakest")
        regions = gm["regions"]
        if strongest and weakest and strongest != weakest:
            s_lab = regions[strongest]["label"]
            w_lab = regions[weakest]["label"]
            notes.append(f"区域宏观：{strongest}最强（{s_lab}），{weakest}最弱（{w_lab}），"
                         f"同等条件下优先配置宏观更强区域的QDII")
        contracting = [r for r, info in regions.items() if info.get("label") == "收缩"]
        if contracting:
            notes.append(f"区域警示：{('、'.join(contracting))} 处于收缩区间，相关QDII需谨慎")

    return notes


def _empty_portfolio(market_signal: MarketSignal) -> PortfolioRecommendation:
    return {
        "composite_signal": market_signal.get("composite_signal", "标配稳健"),
        "core_allocation_pct": 60,
        "satellite_allocation_pct": 30,
        "cash_allocation_pct": 10,
        "core_funds": [],
        "satellite_funds": [],
        "total_invested_pct": 90,
        "top_picks": [],
        "investment_notes": ["数据采集中，请先运行数据更新"],
    }
