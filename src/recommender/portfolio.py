"""投资组合构建与建议"""
import pandas as pd
from ..utils.database import read_table


CORE_BENCHMARKS = ["标普500", "S&P", "纳斯达克100", "MSCI全球", "全球"]
SATELLITE_BENCHMARKS = ["科技", "医疗", "能源", "亚洲", "主动"]


def build_portfolio_recommendation(market_signal: dict, top_n: int = 10) -> dict:
    scores_df = read_table("fund_scores")
    funds_df = read_table("fund_list")

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

    # 核心仓位：宽基指数
    core_funds = _select_core_funds(merged, core_alloc)
    # 卫星仓位：行业/主动/主题
    satellite_funds = _select_satellite_funds(merged, satellite_alloc, exclude_codes={f["fund_code"] for f in core_funds})

    total_invested = core_alloc + satellite_alloc
    portfolio = {
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
    return portfolio


def _select_core_funds(df: pd.DataFrame, alloc: float) -> list:
    core = df[df["fund_name"].str.contains("|".join(CORE_BENCHMARKS), na=False)]
    if core.empty:
        core = df[df["fund_type"].str.contains("ETF|指数|被动", na=False)]

    picks = core.head(3)
    n = len(picks)
    if n == 0:
        return []
    weight = alloc / n  # 在选中的基金间等权分配，确保权重之和 = alloc
    selected = []
    for _, row in picks.iterrows():
        selected.append({
            "fund_code": row["fund_code"],
            "fund_name": row.get("fund_name", row["fund_code"]),
            "signal": row.get("signal", "持有"),
            "score": row.get("total_score", 0),
            "weight": round(weight * 100, 1),
            "role": "核心",
        })
    return selected


def _select_satellite_funds(df: pd.DataFrame, alloc: float, exclude_codes: set) -> list:
    sat = df[~df["fund_code"].isin(exclude_codes)]
    # 优先主动QDII和行业主题
    active = sat[sat["fund_type"].str.contains("主动|LOF|行业|主题", na=False)]
    if active.empty:
        active = sat

    candidates = active.head(2)
    n = max(1, len(candidates))
    selected = []
    for _, row in candidates.iterrows():
        weight = alloc / n
        selected.append({
            "fund_code": row["fund_code"],
            "fund_name": row.get("fund_name", row["fund_code"]),
            "signal": row.get("signal", "持有"),
            "score": row.get("total_score", 0),
            "weight": round(weight * 100, 1),
            "role": "卫星",
        })
    return selected


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


def _empty_portfolio(market_signal: dict) -> dict:
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
