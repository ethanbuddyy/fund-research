"""西格尔策略：长期权益溢价 + 分红再投资 + 全球分散"""


def analyze(valuation: dict, cfg: dict) -> dict:
    erp = valuation.get("equity_risk_premium", 1.5)
    treasury_10y = valuation.get("treasury_10y", 4.5)
    cape = valuation.get("cape", 28)

    params = cfg.get("strategy_params", {}).get("siegel", {})
    # config 存储百分比值（如 3.0 代表 3%），erp 也是百分比值，直接对比
    erp_threshold = params.get("equity_risk_premium_threshold", 3.0)

    # 西格尔核心：股权风险溢价是否值得承担风险
    if erp > erp_threshold:
        erp_verdict = "股票相对债券有超额收益，长期持股合理"
        score = 8
    elif erp > 1.5:
        erp_verdict = "股票风险溢价偏低，但长期看仍优于债券"
        score = 6
    else:
        erp_verdict = "股票风险溢价极低，债券性价比上升"
        score = 4

    # 长期股票回报预测（西格尔公式：ERP + 无风险利率）
    expected_equity_return = erp + treasury_10y
    expected_real_return = expected_equity_return - 2.5  # 假设2.5%通胀

    # 全球分散建议
    global_diversification = cape > 28  # 美股高估时加大全球分散

    insights = [
        f"西格尔核心：长期而言股票是最佳资产类别，{200}年数据验证",
        f"当前股权风险溢价（ERP）: {erp:.2f}%，{erp_verdict}",
        f"预期股票名义回报：约 {expected_equity_return:.1f}%，实际回报约 {expected_real_return:.1f}%",
        f"{'建议加配MSCI全球指数，分散美股集中风险' if global_diversification else '美股估值合理，标普500可作为核心配置'}",
        "分红再投资：优先选择分红率较高的价值型QDII，发挥复利效应",
    ]

    return {
        "master": "西格尔",
        "score": score,
        "label": f"长期权益回报{'吸引力强' if score >= 7 else '一般' if score >= 5 else '偏弱'}",
        "action": "长期持有权益资产，全球分散配置",
        "insights": insights,
        "erp": erp,
        "expected_return": round(expected_equity_return, 2),
        "global_diversification": global_diversification,
        "prefer_dividend": True,
    }
