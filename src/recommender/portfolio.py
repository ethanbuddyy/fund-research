"""投资组合构建与建议"""
from typing import Any, Optional
from collections.abc import Mapping
import pandas as pd
from ..utils.database import read_table
from ..utils.config import load_config
from ..utils.fund_universe import is_index_fund
from ..domain.types import MarketSignal, PortfolioRecommendation, PortfolioState


CORE_BENCHMARKS = ["标普500", "S&P", "纳斯达克100", "MSCI全球", "全球"]
SATELLITE_BENCHMARKS = ["科技", "医疗", "能源", "亚洲", "油气", "黄金", "日本", "欧洲"]


def build_portfolio_recommendation(
    market_signal: MarketSignal,
    top_n: int = 10,
    previous_portfolio: Optional[dict] = None,
) -> PortfolioRecommendation:
    """适配器：读库/读配置 → 调纯函数 select_portfolio → 追加 AI → 构造本期快照数据。

    `previous_portfolio` 为上期推荐组合快照原文（由编排层一次性读入并显式传入），
    用于换仓门槛与报告换仓对比；适配器**不再自行读盘**。也**不再写快照**——本期
    应提交的快照数据放进返回值 `snapshot_payload`，由编排层在所有「与上期对比」
    数据计算完成后再统一提交，从根上消除快照的时序耦合。
    """
    scores_df = read_table("fund_scores")
    funds_df = read_table("fund_list")
    cfg = load_config()

    if scores_df.empty:
        return _empty_portfolio(market_signal)

    portfolio = select_portfolio(scores_df, funds_df, market_signal,
                                 previous_portfolio, cfg, top_n)

    # ── AI 阶段二/三：投资决策增强（IO/AI，留在适配器）──────────
    _apply_ai_enhancements(portfolio, market_signal, cfg)

    # 本期应提交的快照数据（需查最新净值=IO）放进返回值，由编排层稍后统一提交。
    portfolio["snapshot_payload"] = _build_snapshot_payload(
        portfolio["core_funds"], portfolio["satellite_funds"], scores_df
    )
    return portfolio


def select_portfolio(
    scores_df: pd.DataFrame,
    funds_df: pd.DataFrame,
    market_signal: MarketSignal,
    previous_portfolio: Optional[dict],
    config: Mapping[str, Any],
    top_n: int = 10,
) -> PortfolioRecommendation:
    """纯函数：内存评分表+基金表+信号+上期快照+配置 → 组合推荐。

    **不读库、不读配置文件、不调 AI、不读写文件、不打印**——可脱离 SQLite/AI
    用内存数据直接、可重复地测试（回归 test #6）。AI 增强与快照落盘由适配器负责。
    """
    if scores_df.empty:
        return _empty_portfolio(market_signal)

    merged = scores_df.merge(
        funds_df[["fund_code", "fund_type", "fund_name", "expense_ratio"]],
        on="fund_code", how="left", suffixes=("", "_info")
    )
    merged = merged.sort_values("total_score", ascending=False)
    index_only = config.get("portfolio", {}).get("index_only", True)
    if index_only:
        index_mask = merged.apply(
            lambda row: is_index_fund(
                fund_code=row.get("fund_code", ""),
                fund_type=row.get("fund_type", ""),
                fund_name=row.get("fund_name", ""),
            ),
            axis=1,
        )
        merged = merged[index_mask].copy()

    core_alloc = market_signal.get("core_allocation", 0.60)
    satellite_alloc = market_signal.get("satellite_allocation", 0.30)
    cash_alloc = market_signal.get("cash_allocation", 0.10)

    score_threshold = config.get("rebalancing", {}).get("score_threshold", 10)
    prev_core_scores, prev_sat_scores = _extract_prev_scores(previous_portfolio)

    # 核心仓位：宽基指数
    core_funds = _select_core_funds(merged, core_alloc, prev_core_scores, score_threshold)
    # 卫星仓位：行业/主题/区域指数
    satellite_funds = _select_satellite_funds(merged, satellite_alloc,
                                              exclude_codes={f["fund_code"] for f in core_funds},
                                              prev_scores=prev_sat_scores,
                                              score_threshold=score_threshold)

    actual_core_pct = round(sum(float(f.get("weight") or 0) for f in core_funds), 1)
    actual_satellite_pct = round(sum(float(f.get("weight") or 0) for f in satellite_funds), 1)
    actual_invested_pct = round(actual_core_pct + actual_satellite_pct, 1)
    actual_cash_pct = round(max(0.0, 100.0 - actual_invested_pct), 1)
    target_invested_pct = round((core_alloc + satellite_alloc) * 100, 1)
    portfolio: PortfolioRecommendation = {
        "composite_signal": market_signal.get("composite_signal"),
        "core_allocation_pct": actual_core_pct,
        "satellite_allocation_pct": actual_satellite_pct,
        "cash_allocation_pct": actual_cash_pct,
        "core_funds": core_funds,
        "satellite_funds": satellite_funds,
        "total_invested_pct": actual_invested_pct,
        "top_picks": merged.head(top_n).to_dict("records"),
        "investment_notes": _generate_notes(market_signal),
        "score_threshold": score_threshold,  # 报告层「未入选原因」据此说明，避免猜测
        "index_only": index_only,
        "allocation_shortfall_pct": round(
            max(0.0, target_invested_pct - actual_invested_pct), 1
        ),
        # 上期快照原文：供报告层「换仓变动」对比（报告层不再读盘），首次运行为 None
        "previous_portfolio": previous_portfolio,
    }
    return portfolio


def _apply_ai_enhancements(
    portfolio: PortfolioRecommendation,
    market_signal: MarketSignal,
    cfg: Mapping[str, Any],
) -> None:
    """AI 阶段二/三增强（IO/AI，就地改写 portfolio）。配置关闭或无 phase1 则直接返回。"""
    cfg_ai = cfg.get("ai_analysis", {})
    ai_phase1 = market_signal.get("ai_analysis")
    if not (cfg_ai.get("enabled", False) and ai_phase1 is not None):
        return
    try:
        from ..ai.phase2_portfolio_advisor import PortfolioAdvisor
        ai_decision = PortfolioAdvisor().advise(
            market_signal=market_signal,
            ai_phase1=ai_phase1,
            portfolio=portfolio,
        )
        if ai_decision:
            notes = ai_decision.get("position_sizing_notes")
            if notes:
                portfolio["investment_notes"] = notes
            portfolio["ai_decision"] = ai_decision

            # ── AI 阶段三：对抗式审查（默认关闭，需显式开启）──────
            # 由"只负责挑错"的子智能体复核 Phase2 决策，防止看似合理实则
            # 与数据矛盾的结论被静默采用。额外消耗 token/延迟，故按需启用。
            try:
                from ..ai.phase3_adversarial_reviewer import AdversarialReviewer, is_enabled
                if is_enabled():
                    review = AdversarialReviewer().review(
                        market_signal=market_signal,
                        portfolio=portfolio,
                        ai_decision=ai_decision,
                    )
                    if review:
                        portfolio["adversarial_review"] = review
                        print(f"[AI Phase3] 对抗审查：{review.get('overall_verdict')}"
                              f"（{len(review.get('findings', []))} 项问题）")
            except Exception as e:
                print(f"[AI Phase3] 对抗审查跳过（不影响主流程）: {e}")
    except Exception as e:
        print(f"[AI Phase2] 跳过: {e}")


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


def _extract_prev_scores(previous_portfolio: Optional[dict]) -> tuple[dict[str, float], dict[str, float]]:
    """从上期快照原文解析出 (core_scores, satellite_scores) 两个 {code: score} 字典。

    快照由编排层经 portfolio_state_store 读入并显式传入（本模块不再读盘）。
    首次运行（None）或旧格式（无 core/satellite 键）返回两个空字典，不触发门槛约束。
    """
    raw = previous_portfolio
    if isinstance(raw, dict) and "core" in raw and "satellite" in raw:
        return _extract_scores(raw["core"]), _extract_scores(raw["satellite"])
    return {}, {}


def _get_latest_navs(fund_codes: list) -> dict:
    """最新净值经 FundRepository 读取（适配器不再直接查库）。

    保留此薄包装作为测试接缝（既有用例 monkeypatch 本函数注入内存净值）。
    """
    from ..utils.fund_repository import get_latest_navs
    return get_latest_navs(fund_codes)


def _build_snapshot_payload(core_funds: list, satellite_funds: list,
                            scores_df: pd.DataFrame) -> PortfolioState:
    """构造本期推荐快照数据（代码+评分+权重+净值），**不写盘**。

    返回的 dict 由编排层在所有「与上期对比」展示数据计算完成后，经
    portfolio_state_store.commit_runtime_state 统一提交（止损追踪与换仓门槛共用）。
    """
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

    return {
        "date": _dt.now().strftime("%Y-%m-%d"),
        "core": {f["fund_code"]: _info(f) for f in core_funds},
        "satellite": {f["fund_code"]: _info(f) for f in satellite_funds},
    }


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
    """选取卫星仓位（行业/主题/区域指数，最多2只）。"""
    sat = df[~df["fund_code"].isin(exclude_codes)]
    satellite_pattern = "|".join(SATELLITE_BENCHMARKS)
    pool = sat[
        sat["fund_name"].str.contains(satellite_pattern, na=False)
        | sat["fund_type"].str.contains("ETF|指数|被动|增强", na=False)
    ]
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

    selected: list[str]
    if not prev_scores:
        selected = candidate_codes[:max_n]
    else:
        selected = [c for c in prev_scores if c in candidate_codes][:max_n]
        selected_scores = {c: prev_scores.get(c, 0.0) for c in selected}
        for code in candidate_codes:
            if code in selected:
                continue
            score_series = pool.loc[pool["fund_code"].astype(str) == code, "total_score"]
            if score_series.empty:
                continue
            score = float(score_series.iloc[0])
            if len(selected) < max_n:
                selected.append(code)
                selected_scores[code] = score
            else:
                min_code = min(selected, key=lambda c: selected_scores.get(c, 0.0))
                if score >= selected_scores.get(min_code, 0.0) + score_threshold:
                    selected.remove(min_code)
                    selected_scores.pop(min_code, None)
                    selected.append(code)
                    selected_scores[code] = score
                else:
                    # 候选按总分降序；当前候选失败后，后续候选也无法跨过门槛。
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


def _generate_notes(market_signal: Mapping[str, Any]) -> list[str]:
    signal = market_signal.get("composite_signal", "标配稳健")
    macro = market_signal.get("macro", {})
    notes = []

    if signal == "重仓进取":
        notes.append("市场信号积极，可适当提高股票仓位，加配成长型QDII")
        notes.append("博格策略：维持定投，市场上涨中持续积累份额")
    elif signal == "标配稳健":
        notes.append("市场处于合理区间，维持标配，组合仅使用指数基金以降低风格漂移")
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
        "core_allocation_pct": 0,
        "satellite_allocation_pct": 0,
        "cash_allocation_pct": 100,
        "core_funds": [],
        "satellite_funds": [],
        "total_invested_pct": 0,
        "top_picks": [],
        "investment_notes": ["无合格基金可供配置，未分配仓位全部保留为现金"],
        "allocation_shortfall_pct": round(
            (
                market_signal.get("core_allocation", 0.60)
                + market_signal.get("satellite_allocation", 0.30)
            ) * 100,
            1,
        ),
    }
