"""彼得林奇策略：GARP（成长+合理估值）+ 行业趋势"""
import pandas as pd
from ...utils.database import read_table


def analyze(cfg: dict) -> dict:
    sector_etfs = cfg.get("sector_etfs", [])
    sector_performance = _get_sector_performance(sector_etfs)

    # 识别强势/弱势板块
    strong_sectors = []
    weak_sectors = []
    for s in sector_performance:
        if s["return_1m"] > 2:
            strong_sectors.append(s)
        elif s["return_1m"] < -2:
            weak_sectors.append(s)

    strong_sectors.sort(key=lambda x: x["return_1m"], reverse=True)
    weak_sectors.sort(key=lambda x: x["return_1m"])

    # 林奇评分：成长机会评估
    tech_sector = next((s for s in sector_performance if "XLK" in s["symbol"]), None)
    health_sector = next((s for s in sector_performance if "XLV" in s["symbol"]), None)

    score = 6  # 基础分
    if tech_sector and tech_sector["return_1m"] > 3:
        score = min(10, score + 2)  # 科技强势加分
    if tech_sector and tech_sector["return_1m"] < -3:
        score = max(1, score - 1)

    top_sectors = [s["name"] for s in strong_sectors[:3]]
    action = f"关注{'、'.join(top_sectors[:2])}板块机会" if top_sectors else "均衡配置各板块"

    strong_str = ", ".join([s["name"] + f"({s['return_1m']:+.1f}%)" for s in strong_sectors[:3]]) or "暂无明显强势"
    weak_str = ", ".join([s["name"] + f"({s['return_1m']:+.1f}%)" for s in weak_sectors[:3]]) or "暂无明显弱势"
    insights = [
        "林奇核心：投资你了解的行业，买成长股但不为成长支付过高价格（GARP）",
        f"当月强势板块：{strong_str}",
        f"当月弱势板块：{weak_str}",
        "纳斯达克100ETF是林奇成长风格最直接的QDII载体",
    ]

    return {
        "master": "彼得林奇",
        "score": score,
        "label": f"成长机会{'较多' if score >= 7 else '一般' if score >= 5 else '有限'}",
        "action": action,
        "insights": insights,
        "strong_sectors": strong_sectors[:3],
        "weak_sectors": weak_sectors[:3],
        "prefer_growth": True,
    }


def _get_sector_performance(sector_etfs: list) -> list:
    results = []
    for etf in sector_etfs:
        symbol = etf["symbol"]
        df = read_table("market_data", "symbol = ? ORDER BY date DESC LIMIT 21", (symbol,))
        if len(df) < 5:
            results.append({"symbol": symbol, "name": etf["name"], "return_1m": 0.0, "return_3m": 0.0})
            continue
        df = df.sort_values("date")
        ret_1m = (float(df.iloc[-1]["close"]) / float(df.iloc[0]["close"]) - 1) * 100
        results.append({"symbol": symbol, "name": etf["name"], "return_1m": round(ret_1m, 2), "return_3m": 0.0})
    return results
