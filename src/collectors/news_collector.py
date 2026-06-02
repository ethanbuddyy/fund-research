"""市场情绪采集：Finnhub 市场新闻标题情绪（主路径）+ VIX/动量推导（fallback）

Finnhub 免费层可用 /news?category=general（100条），对标题+摘要做
金融关键词多空打分，转换为 0-100 情绪分。

权重：有 Finnhub 数据时 VIX 40% + 动量 25% + 新闻 35%
      无 Finnhub 数据时 VIX 60% + 动量 40%（退回旧逻辑）
"""
import os
import re
from datetime import datetime
import pandas as pd
from ..utils.database import read_table, upsert_dataframe
from ..utils.config import load_config


# ── 金融情绪关键词表 ──────────────────────────────────────────────
# 多空词各 ~30 个，覆盖市场/宏观/地缘三个维度，避免通用否定词误判

_BULLISH_WORDS = re.compile(
    r"\b("
    r"surges?|rallies?|rally|gains?|higher|climbs?|jumps?|soars?|rises?|rose|"
    r"beats?|beat expectations?|record high|all[- ]time high|upgraded?|upgrades?|"
    r"strong(er)?|robust|optimistic|optimism|recovery|recovers?|rebound|"
    r"deal|agreement|ceasefire|peace|resolved?|stimulus|easing|cut rates?|"
    r"rate cut|dovish|soft landing|outperform|buyback|dividend|growth|boom|"
    r"confidence|positive|upbeat|breakthrough|expansion"
    r")\b",
    re.IGNORECASE,
)

_BEARISH_WORDS = re.compile(
    r"\b("
    r"falls?|fell|drops?|dropped|plunges?|declines?|declined|slides?|slid|sinks?|sank|"
    r"misses?|missed expectations?|downgraded?|downgrades?|warning|warns?|"
    r"weak(er)?|slowdown|slowing|recession|contraction|stagflation|"
    r"tariff|sanction|trade war|inflation surges?|hike|rate hike|hawkish|"
    r"crash|correction|sell[- ]off|selloff|fear|concern|risk|uncertainty|"
    r"layoffs?|job cuts?|bankruptcy|default|debt crisis|contagion|"
    r"tension|conflict|escalat"
    r")\b",
    re.IGNORECASE,
)

# 仅过滤明显无关类别（娱乐、体育）；business/top news/forex/crypto 全保留
_SKIP_CATEGORIES = {"entertainment", "sports"}


# ── Finnhub 采集 ──────────────────────────────────────────────────

def _get_finnhub_api_key() -> str:
    cfg = load_config()
    return os.environ.get("FINNHUB_API_KEY") or cfg.get("finnhub_api_key", "")


def _load_cached_finnhub(today: str) -> dict | None:
    """读取今日已缓存的 Finnhub 情绪数据，避免重复调用 API。"""
    df = read_table("news_sentiment", "date = ? AND source = 'finnhub'", (today,))
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
        "date":           today,
        "source":         "finnhub",
        "bullish_pct":    data["bullish_pct"],
        "bearish_pct":    data["bearish_pct"],
        "news_score":     data["news_score"],
        "buzz":           data["buzz"],
        "articles_count": data["articles"],
    }])
    upsert_dataframe(df, "news_sentiment", ["date", "source"])


def _score_headlines(articles: list) -> dict:
    """对标题 + 摘要做关键词多空计数，返回情绪指标。"""
    bullish = bearish = neutral = 0
    for item in articles:
        if item.get("category", "").lower() in _SKIP_CATEGORIES:
            continue
        text = f"{item.get('headline', '')} {item.get('summary', '')}"
        b_hits = len(_BULLISH_WORDS.findall(text))
        s_hits = len(_BEARISH_WORDS.findall(text))
        if b_hits > s_hits:
            bullish += 1
        elif s_hits > b_hits:
            bearish += 1
        else:
            neutral += 1

    total = bullish + bearish + neutral or 1
    scored = bullish + bearish or 1
    bullish_pct = bullish / scored          # 有明确倾向的文章中多头占比
    # news_score：结合多头占比与参与度（有明确情绪的文章占比）
    engagement = (bullish + bearish) / total
    news_score = bullish_pct * 0.7 + engagement * 0.3

    return {
        "bullish_pct": round(bullish_pct, 4),
        "bearish_pct": round(1 - bullish_pct, 4),
        "news_score":  round(news_score, 4),
        "buzz":        round(engagement, 4),
        "articles":    total,
        "bullish_n":   bullish,
        "bearish_n":   bearish,
    }


def _fetch_finnhub(api_key: str, today: str) -> dict | None:
    """调用 Finnhub /news?category=general，返回关键词情绪指标。"""
    cached = _load_cached_finnhub(today)
    if cached:
        return cached

    try:
        import requests
        resp = requests.get(
            "https://finnhub.io/api/v1/news",
            params={"category": "general", "token": api_key},
            timeout=10,
        )
        resp.raise_for_status()
        articles = resp.json()
        if not articles:
            return None

        result = _score_headlines(articles)
        _save_finnhub(today, result)
        print(
            f"[OK] Finnhub 新闻情绪（{result['articles']}条）: "
            f"多头{result['bullish_n']}篇 / 空头{result['bearish_n']}篇 → "
            f"多头占比{result['bullish_pct']*100:.1f}%  新闻分{result['news_score']:.3f}"
        )
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
    vix_df   = read_table("market_data", "symbol = ? ORDER BY date DESC LIMIT 1", ("^VIX",))
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

    vix_score      = max(0.0, min(100.0, 100.0 - (vix - 10.0) * 3.33))
    momentum_score = max(0.0, min(100.0, 50.0 + sp500_1m_return * 5.0))

    # ── 尝试接入 Finnhub 新闻情绪 ─────────────────────────────────
    api_key = _get_finnhub_api_key()
    finnhub = _fetch_finnhub(api_key, today) if api_key else None

    if finnhub:
        news_score = finnhub["bullish_pct"] * 100.0
        sentiment_score = int(
            vix_score      * 0.40
            + momentum_score * 0.25
            + news_score     * 0.35
        )
        news_source = "finnhub"
    else:
        news_score      = None
        sentiment_score = int(vix_score * 0.60 + momentum_score * 0.40)
        news_source     = "N/A"

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
        "score":           sentiment_score,
        "label":           label,
        "color":           color,
        "vix":             vix,
        "sp500_1m_return": sp500_1m_return,
        "vix_score":       round(vix_score, 1),
        "momentum_score":  round(momentum_score, 1),
        "news_score":      round(news_score, 1) if news_score is not None else None,
        "news_source":     news_source,
    }

    if finnhub:
        result["finnhub_bullish_pct"] = finnhub["bullish_pct"]
        result["finnhub_bearish_pct"] = finnhub["bearish_pct"]
        result["finnhub_articles"]    = finnhub["articles"]
        result["finnhub_bullish_n"]   = finnhub.get("bullish_n")
        result["finnhub_bearish_n"]   = finnhub.get("bearish_n")

    return result
