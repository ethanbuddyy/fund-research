"""巴菲特策略：品质护城河 + 恐惧贪婪 + 长期持有"""


def analyze(valuation: dict, sentiment: dict, cfg: dict) -> dict:
    params = cfg.get("strategy_params", {}).get("buffett", {})
    vix = sentiment.get("vix", 18)
    buffett_indicator = valuation.get("buffett_indicator", 1.85)
    erp = valuation.get("equity_risk_premium", 1.5)

    bi_high = params.get("buffett_indicator_high", 1.2)
    bi_low = params.get("buffett_indicator_low", 0.8)
    vix_fear = params.get("vix_fear", 30)
    vix_greed = params.get("vix_greed", 15)

    # 巴菲特指标（总市值/GDP）
    if buffett_indicator > bi_high * 1.3:
        bi_score = 2
        bi_label = "严重高估"
    elif buffett_indicator > bi_high:
        bi_score = 4
        bi_label = "偏高"
    elif buffett_indicator > bi_low:
        bi_score = 7
        bi_label = "合理"
    else:
        bi_score = 9
        bi_label = "低估"

    # 恐惧贪婪（逆向）：别人恐惧时贪婪
    if vix > vix_fear:
        fear_bonus = 2  # 恐惧时加分
        fear_note = f"VIX {vix:.1f} > {vix_fear}，市场恐惧，巴菲特式买入机会"
    elif vix < vix_greed:
        fear_bonus = -1  # 贪婪时减分
        fear_note = f"VIX {vix:.1f} < {vix_greed}，市场贪婪，需保持谨慎"
    else:
        fear_bonus = 0
        fear_note = f"VIX {vix:.1f}，市场情绪中性"

    score = min(10, max(1, bi_score + fear_bonus))

    if score >= 7:
        action = "长期持有/逢低增持"
    elif score >= 5:
        action = "持有，不追涨"
    else:
        action = "谨慎，等待回调"

    insights = [
        f"巴菲特指标（总市值/GDP）: {buffett_indicator:.2f}（{bi_label}）",
        fear_note,
        f"股权风险溢价 ERP: {erp:.2f}%（{'吸引力不足' if erp < 2 else '有配置价值'}）",
        "巴菲特核心：买入并长期持有优质宽基指数，在恐慌时加码",
    ]

    return {
        "master": "巴菲特",
        "score": score,
        "label": bi_label,
        "action": action,
        "insights": insights,
        "prefer_quality": True,
        "long_term_bias": True,
    }
