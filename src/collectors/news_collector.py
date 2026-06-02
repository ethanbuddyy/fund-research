"""市场情绪采集：Finnhub 新闻情绪（主路径）+ VIX/动量推导（fallback）

权重：有 Finnhub 数据时 VIX 40% + 动量 25% + 新闻 35%
      无 Finnhub 数据时 VIX 60% + 动量 40%（与旧逻辑一致）
"""
import os
from datetime import datetime
import pandas as pd
from ..utils.database import read_table, upsert_dataframe
from ..utils.config import load_config


# ── Finnhub 采集 ──────────────────────────────────────────────────

def _get_finnhub_api_key() -> str:
    cfg = load_config()
    return os.environ.get("FINNHUB_API_KEY") or cfg.get("finnhub_api_key", "")


def _load_cached_finnhub(today: str) -> dict | None:
    """读取今日已缓存的 Finnhub 情绪数据，避免重复调用 API。"""
    df = read_table(
        "news_sentiment",
        "date = ? AND source = 'finnhub'",
        (today,),
    )
    if df.empty:
        return None
    row = df.iloc[0]
    return {
        "bullish_pct": float(row["bullish_pct"]),
        "bearish_pct": float(row["bearish_pct"]),
        "news_score":  float(row["news_score"]),
        "buzz":        float(row["buzz"]),
        "articles":    int(row["articles_count"]),
    }


def _save_finnhub(today: str, data: dict):
    df = pd.DataFrame([{
        "date":          today,
        "source":        "finnhub",
        "bullish_pct":   data["bullish_pct"],
        "bearish_pct":   data["bearish_pct"],
        "news_score":    data["news_score"],
        "buzz":          data["buzz"],
        "articles_count": data["articles"],
    }])
    upsert_dataframe(df, "news_sentiment", ["date", "source"])


def _fetch_finnhub(api_key: str, today: str) -> dict | None:
    """调用 Finnhub /news-sentiment?symbol=SPY 获取市场新闻情绪。"""
    cached = _load_cached_finnhub(today)
    if cached:
        return cached

    try:
        import requests
        resp = requests.get(
            "https://finnhub.io/api/v1/news-sentiment",
            params={"symbol": "SPY", "token": api_key},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        sentiment = data.get("sentiment", {})
        buzz_block = data.get("buzz", {})
        result = {
            "bullish_pct": float(sentiment.get("bullishPercent", 0.5)),
            "bearish_pct": float(sentiment.get("bearishPercent", 0.5)),
            "news_score":  float(data.get("companyNewsScore", 0.5)),
            "buzz":        float(buzz_block.get("buzz", 0.0)),
            "articles":    int(buzz_block.get("articlesInLastWeek", 0)),
        }
        _save_finnhub(today, result)
        print(f"[OK] Finnhub 新闻情绪: 多头{result['bullish_pct']*100:.1f}% "
              f"空头{result['bearish_pct']*100:.1f}% "
              f"新闻分{result['news_score']:.3f} "
              f"（近7天{result['articles']}篇）")
        return result

    except Exception as e:
        print(f"[WARN] Finnhub 情绪获取失败: {e}")
        return None


# ── 主函数 ────────────────────────────────────────────────────────

def get_market_sentiment() -> dict:
    """返回综合市场情绪字典。

    有 Finnhub Key 时：VIX 40% + 动量 25% + 新闻 35%
    无 Finnhub Key 时：VIX 60% + 动量 40%（旧逻辑）
    """
    today = datetime.now().strftime("%Y-%m-%d")

    # ── 基础指标：VIX + 标普动量 ──────────────────────────────────
    vix_df  = read_table("market_data", "symbol = ? ORDER BY date DESC LIMIT 1", ("^VIX",))
    sp500_df = read_table("market_data", "symbol = ? ORDER BY date DESC LIMIT 30", ("^GSPC",))

    vix = 18.0
    sp500_1m_return = 0.0

    if not vix_df.empty:
        vix = float(vix_df.iloc[0]["close"])

    if len(sp500_df) >= 20:
        sp500_df = sp500_df.sort_values("date")
        sp500_1m_return = (
            float(sp500_df.iloc[-1]["close"]) / float(sp500_df.iloc[0]["close"]) - 1
        ) * 100

    # VIX 10→100分 / 40→0分
    vix_score      = max(0.0, min(100.0, 100.0 - (vix - 10.0) * 3.33))
    momentum_score = max(0.0, min(100.0, 50.0 + sp500_1m_return * 5.0))

    # ── 尝试接入 Finnhub 新闻情绪 ─────────────────────────────────
    api_key = _get_finnhub_api_key()
    finnhub = _fetch_finnhub(api_key, today) if api_key else None

    if finnhub:
        # 多头占比 → 0-100 分（0.5 = 中性50分）
        news_score = finnhub["bullish_pct"] * 100.0
        sentiment_score = int(
            vix_score      * 0.40
            + momentum_score * 0.25
            + news_score     * 0.35
        )
        news_source = "finnhub"
    else:
        news_score  = None
        sentiment_score = int(vix_score * 0.60 + momentum_score * 0.40)
        news_source = "N/A"

    # ── 情绪标签 ──────────────────────────────────────────────────
    if sentiment_score >= 75:
        label, color = "极度贪婪", "red"
    elif sentiment_score >= 55:
        label, color = "贪婪", "orange"
    elif sentiment_score >= 45:
        label, color = "中性", "gray"
    elif sentiment_score >= 25:
        label, color = "恐惧", "lightblue"
    else:
        label, color = "极度恐惧", "blue"

    result = {
        "score":            sentiment_score,
        "label":            label,
        "color":            color,
        "vix":              vix,
        "sp500_1m_return":  sp500_1m_return,
        "vix_score":        round(vix_score, 1),
        "momentum_score":   round(momentum_score, 1),
        "news_score":       round(news_score, 1) if news_score is not None else None,
        "news_source":      news_source,
    }

    if finnhub:
        result["finnhub_bullish_pct"] = finnhub["bullish_pct"]
        result["finnhub_bearish_pct"] = finnhub["bearish_pct"]
        result["finnhub_articles"]    = finnhub["articles"]

    return result
