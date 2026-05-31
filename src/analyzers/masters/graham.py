"""格雷厄姆策略：安全边际、内在价值、防御性投资"""


def analyze(valuation: dict, cfg: dict) -> dict:
    params = cfg.get("strategy_params", {}).get("graham", {})
    cape = valuation.get("cape", 28)
    pe = valuation.get("sp500_pe", 22)
    cape_overvalued = params.get("cape_overvalued", 30)
    cape_undervalued = params.get("cape_undervalued", 15)

    # 安全边际评分
    if cape < cape_undervalued:
        margin_score = 9
        margin_label = "充足安全边际"
        action = "积极买入"
    elif cape < (cape_overvalued + cape_undervalued) / 2:
        margin_score = 6
        margin_label = "安全边际合理"
        action = "逢低买入"
    elif cape < cape_overvalued:
        margin_score = 4
        margin_label = "安全边际不足"
        action = "谨慎持有"
    else:
        margin_score = 2
        margin_label = "无安全边际"
        action = "减仓观望"

    # 防御性检查
    pe_check = pe < params.get("pe_overvalued", 25)
    valuation_reasonable = cape < cape_overvalued

    insights = [
        f"Shiller CAPE {cape:.1f}（历史均值约17，当前{'高于' if cape > 17 else '低于'}均值）",
        f"市场 P/E 约 {pe:.1f}，安全边际{'不足' if pe > 25 else '合理'}",
        f"格雷厄姆建议：{'市场整体高估，优先持有宽基指数等待机会' if not valuation_reasonable else '存在合理投资价值'}",
    ]

    return {
        "master": "格雷厄姆",
        "score": margin_score,
        "label": margin_label,
        "action": action,
        "insights": insights,
        "prefer_index": True,  # 格雷厄姆也支持指数化投资
        "risk_level": "低风险优先",
    }
