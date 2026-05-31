"""博格策略：低成本指数化 + 长期持有 + 定投"""


def analyze(fund_list: list[dict], cfg: dict) -> dict:
    params = cfg.get("strategy_params", {}).get("bogle", {})
    max_er = params.get("max_expense_ratio", 0.015)
    pref_er = params.get("preferred_expense_ratio", 0.005)

    # 筛选低费率指数基金
    index_funds = [
        f for f in fund_list
        if any(kw in str(f.get("fund_type", "")) for kw in ["ETF", "指数", "被动", "LOF"])
        or any(kw in str(f.get("fund_name", "")) for kw in ["指数", "ETF", "500", "100"])
    ]

    cheap_funds = [f for f in index_funds if (f.get("expense_ratio") or 0.012) <= pref_er]
    acceptable_funds = [f for f in index_funds if (f.get("expense_ratio") or 0.012) <= max_er]

    # 博格评分：指数基金可用性
    if len(cheap_funds) >= 3:
        score = 9
        label = "优质低费率指数基金充裕"
    elif len(acceptable_funds) >= 3:
        score = 7
        label = "指数基金可用，注意费率"
    elif len(index_funds) >= 2:
        score = 5
        label = "指数基金有限"
    else:
        score = 3
        label = "主动基金偏多，博格策略受限"

    # 推荐的核心指数基金
    core_recommendations = []
    priority_keywords = ["标普500", "纳斯达克100", "MSCI全球", "S&P"]
    for f in index_funds:
        for kw in priority_keywords:
            if kw in str(f.get("fund_name", "")) or kw in str(f.get("benchmark", "")):
                core_recommendations.append(f.get("fund_name", f.get("fund_code")))
                break

    insights = [
        "博格核心：买入并持有低成本宽基指数基金，时间是最好的复利机器",
        f"当前可选指数基金 {len(index_funds)} 只，费率达标 {len(acceptable_funds)} 只",
        "定投策略：每月固定金额买入，平摊成本，避免择时错误",
        f"费率警示：优先选择年费率 < 0.5% 的ETF（当前市场有多只QDII ETF费率在1%左右）",
    ]
    if core_recommendations:
        insights.append(f"博格首选标的：{', '.join(core_recommendations[:3])}")

    return {
        "master": "博格",
        "score": score,
        "label": label,
        "action": "定投核心指数ETF",
        "insights": insights,
        "prefer_low_cost": True,
        "prefer_index": True,
        "core_funds": core_recommendations[:5],
    }
