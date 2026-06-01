"""市场叙事生成：基于量化数据生成可读性文字观察，不参与评分决策。"""
import pandas as pd
from ..utils.database import read_table


def generate_narrative(valuation: dict, sentiment: dict, fund_list: list[dict], cfg: dict) -> dict:
    """返回各维度的文字洞察列表，供展示层使用。"""
    insights = []
    insights.extend(_valuation_insights(valuation, cfg))
    insights.extend(_sentiment_insights(sentiment, cfg))
    insights.extend(_cost_insights(fund_list, cfg))
    insights.extend(_sector_insights(cfg))

    return {"insights": insights}


def _valuation_insights(valuation: dict, cfg: dict) -> list[str]:
    vp = cfg.get("strategy_params", {}).get("valuation_thresholds", {})
    cape = valuation.get("cape", 28)
    pe = valuation.get("sp500_pe", 22)
    erp = valuation.get("equity_risk_premium", 1.5)
    bi = valuation.get("buffett_indicator", 1.85)
    treasury = valuation.get("treasury_10y", 4.5)
    cape_high = vp.get("cape_overvalued", 30.0)
    cape_low = vp.get("cape_undervalued", 15.0)
    erp_threshold = vp.get("equity_risk_premium_threshold", 3.0)

    lines = []

    # CAPE 观察
    if cape >= cape_high:
        lines.append(f"Shiller CAPE {cape:.1f}，高于历史高估线（{cape_high}），当前市场整体处于高估区间，安全边际不足。")
    elif cape <= cape_low:
        lines.append(f"Shiller CAPE {cape:.1f}，低于历史低估线（{cape_low}），市场具备较充足的安全边际。")
    else:
        lines.append(f"Shiller CAPE {cape:.1f}，处于合理区间（{cape_low}–{cape_high}），估值中性偏{'高' if cape > (cape_high + cape_low) / 2 else '低'}。")

    # P/E 补充
    pe_line = f"当前市场 P/E 约 {pe:.1f}"
    if pe > vp.get("pe_overvalued", 25):
        pe_line += "，高于历史均值，追涨风险需关注。"
    else:
        pe_line += "，估值尚在可接受范围。"
    lines.append(pe_line)

    # 巴菲特指标（总市值/GDP）
    bi_high = cfg.get("strategy_params", {}).get("sentiment_thresholds", {}).get("buffett_indicator_high", 1.2)
    if bi > bi_high * 1.3:
        lines.append(f"总市值/GDP 比率 {bi:.2f}，显著高于基准（{bi_high}），市场整体估值偏贵。")
    elif bi > bi_high:
        lines.append(f"总市值/GDP 比率 {bi:.2f}，略高于基准（{bi_high}），整体偏高但未达极端。")
    else:
        lines.append(f"总市值/GDP 比率 {bi:.2f}，处于合理区间。")

    # 股权风险溢价
    expected_return = erp + treasury
    if erp > erp_threshold:
        lines.append(f"股权风险溢价（ERP）{erp:.2f}%，高于阈值（{erp_threshold}%），权益资产相对债券仍有吸引力，预期名义回报约 {expected_return:.1f}%。")
    elif erp > 1.5:
        lines.append(f"股权风险溢价（ERP）{erp:.2f}%，偏低但尚为正值，长期持有权益资产仍优于纯债。")
    else:
        lines.append(f"股权风险溢价（ERP）仅 {erp:.2f}%，债券性价比上升，权益仓位需谨慎。")

    return lines


def _sentiment_insights(sentiment: dict, cfg: dict) -> list[str]:
    sp = cfg.get("strategy_params", {}).get("sentiment_thresholds", {})
    vix = sentiment.get("vix", 18)
    vix_fear = sp.get("vix_fear", 30)
    vix_greed = sp.get("vix_greed", 15)

    if vix > vix_fear:
        return [f"VIX 恐慌指数 {vix:.1f}，超过恐惧阈值（{vix_fear}），市场情绪极度悲观，历史上往往是中长期布局机会。"]
    elif vix < vix_greed:
        return [f"VIX 恐慌指数 {vix:.1f}，低于贪婪阈值（{vix_greed}），市场情绪过于乐观，需警惕短期回调风险。"]
    else:
        return [f"VIX 恐慌指数 {vix:.1f}，市场情绪中性，无明显极端信号。"]


def _cost_insights(fund_list: list[dict], cfg: dict) -> list[str]:
    cp = cfg.get("strategy_params", {}).get("cost_filter", {})
    max_er = cp.get("max_expense_ratio", 0.015)
    pref_er = cp.get("preferred_expense_ratio", 0.005)

    index_funds = [
        f for f in fund_list
        if any(kw in str(f.get("fund_type", "")) for kw in ["ETF", "指数", "被动", "LOF"])
        or any(kw in str(f.get("fund_name", "")) for kw in ["指数", "ETF", "500", "100"])
    ]
    cheap = [f for f in index_funds if (f.get("expense_ratio") or 0.012) <= pref_er]
    acceptable = [f for f in index_funds if (f.get("expense_ratio") or 0.012) <= max_er]

    lines = [f"当前基金池中共 {len(index_funds)} 只指数/ETF 基金，其中 {len(acceptable)} 只费率达标（<{max_er*100:.1f}%），{len(cheap)} 只低于优选阈值（<{pref_er*100:.1f}%）。"]
    if len(cheap) < 3:
        lines.append("可选低费率指数基金数量有限，配置时注意控制持有成本，避免费率侵蚀长期收益。")
    return lines


def _sector_insights(cfg: dict) -> list[str]:
    sector_etfs = cfg.get("sector_etfs", [])
    if not sector_etfs:
        return []

    results = []
    for etf in sector_etfs:
        symbol = etf["symbol"]
        df = read_table("market_data", "symbol = ? ORDER BY date DESC LIMIT 21", (symbol,))
        if len(df) < 5:
            results.append({"name": etf["name"], "return_1m": 0.0})
            continue
        df = df.sort_values("date")
        ret = (float(df.iloc[-1]["close"]) / float(df.iloc[0]["close"]) - 1) * 100
        results.append({"name": etf["name"], "return_1m": round(ret, 2)})

    strong = sorted([r for r in results if r["return_1m"] > 2], key=lambda x: -x["return_1m"])
    weak = sorted([r for r in results if r["return_1m"] < -2], key=lambda x: x["return_1m"])

    lines = []
    if strong:
        strong_str = "、".join([f"{s['name']}（{s['return_1m']:+.1f}%）" for s in strong[:3]])
        lines.append(f"近一月强势板块：{strong_str}。")
    if weak:
        weak_str = "、".join([f"{w['name']}（{w['return_1m']:+.1f}%）" for w in weak[:3]])
        lines.append(f"近一月弱势板块：{weak_str}，相关基金短期承压。")
    if not strong and not weak:
        lines.append("近一月各板块涨跌幅均在 ±2% 以内，市场整体震荡分化不明显。")

    return lines
