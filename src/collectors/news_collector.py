"""市场情绪与新闻摘要（基于VIX和市场指标推导）"""
import pandas as pd
from ..utils.database import read_table


def get_market_sentiment() -> dict:
    """基于VIX和市场数据计算市场情绪"""
    vix_df = read_table("market_data", "symbol = ? ORDER BY date DESC LIMIT 1", ("^VIX",))
    sp500_df = read_table("market_data", "symbol = ? ORDER BY date DESC LIMIT 30", ("^GSPC",))

    vix = 18.0
    sp500_1m_return = 0.0
    sentiment_score = 50

    if not vix_df.empty:
        vix = float(vix_df.iloc[0]["close"])

    if len(sp500_df) >= 20:
        sp500_df = sp500_df.sort_values("date")
        sp500_1m_return = (sp500_df.iloc[-1]["close"] / sp500_df.iloc[0]["close"] - 1) * 100

    # 恐惧贪婪指数（简化版：VIX + 市场动量）
    vix_score = max(0, min(100, 100 - (vix - 10) * 3.33))  # VIX 10→100分, 40→0分
    momentum_score = max(0, min(100, 50 + sp500_1m_return * 5))
    sentiment_score = int(vix_score * 0.6 + momentum_score * 0.4)

    if sentiment_score >= 75:
        label = "极度贪婪"
        color = "red"
        icon = "🔴"
    elif sentiment_score >= 55:
        label = "贪婪"
        color = "orange"
        icon = "🟠"
    elif sentiment_score >= 45:
        label = "中性"
        color = "gray"
        icon = "⚪"
    elif sentiment_score >= 25:
        label = "恐惧"
        color = "lightblue"
        icon = "🔵"
    else:
        label = "极度恐惧"
        color = "blue"
        icon = "💙"

    return {
        "score": sentiment_score,
        "label": label,
        "color": color,
        "icon": icon,
        "vix": vix,
        "sp500_1m_return": sp500_1m_return,
    }
