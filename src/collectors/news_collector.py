"""市场情绪采集：Alpha Vantage（主）+ Finnhub（辅）+ VIX/动量（fallback）

优先级：
  1. Alpha Vantage NEWS_SENTIMENT — ML 直接打分，-1~+1，25次/天
  2. Finnhub /news headlines    — 关键词多空打分，60次/分钟
  3. VIX + 标普动量推导           — 无任何新闻 key 时退回

新闻权重混合：
  AV + Finnhub 均有  → news = AV*0.65 + Finnhub*0.35
  仅 AV              → news = AV
  仅 Finnhub         → news = Finnhub
  均无               → VIX 60% + 动量 40%（旧逻辑）

最终情绪分 = VIX 40% + 动量 25% + news 35%（有新闻时）
"""
import os
import re
from datetime import datetime
import pandas as pd
from ..utils.database import read_table, upsert_dataframe
from ..utils.config import load_config


# ── 金融情绪关键词表（Finnhub 关键词打分用）────────────────────────

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
_SKIP_CATEGORIES = {"entertainment", "sports"}


# ── 共用工具 ──────────────────────────────────────────────────────

def _get_key(env_var: str, cfg_key: str) -> str:
    cfg = load_config()
    return os.environ.get(env_var) or cfg.get(cfg_key, "")


def _load_cache(today: str, source: str) -> dict | None:
    df = read_table("news_sentiment", "date = ? AND source = ?", (today, source))
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


def _save_cache(today: str, source: str, data: dict):
    df = pd.DataFrame([{
        "date":           today,
        "source":         source,
        "bullish_pct":    data["bullish_pct"],
        "bearish_pct":    data["bearish_pct"],
        "news_score":     data["news_score"],
        "buzz":           data["buzz"],
        "articles_count": data["articles"],
    }])
    upsert_dataframe(df, "news_sentiment", ["date", "source"])


# ── Alpha Vantage ─────────────────────────────────────────────────

def _fetch_alphavantage(api_key: str, today: str) -> dict | None:
    """调用 Alpha Vantage NEWS_SENTIMENT，返回归一化情绪指标。

    AV 返回每篇文章的 overall_sentiment_score（-1~+1）；
    过滤 relevance_score < 0.3 的低相关文章后加权平均，
    转换到 0-1 区间作为 bullish_pct。
    """
    cached = _load_cache(today, "alphavantage")
    if cached:
        return cached

    try:
        import requests
        resp = requests.get(
            "https://www.alphavantage.co/query",
            params={
                "function": "NEWS_SENTIMENT",
                "topics":   "financial_markets,economy_macro",
                "limit":    50,
                "apikey":   api_key,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        if "Information" in data or "Note" in data:
            msg = data.get("Information") or data.get("Note", "")
            print(f"[WARN] Alpha Vantage 限流或配额耗尽: {msg[:80]}")
            return None

        feed = data.get("feed", [])
        if not feed:
            print("[WARN] Alpha Vantage 返回空 feed")
            return None

        # relevance_score 在 ticker_sentiment 子项中，文章层面不存在；
        # 已通过 topics 参数过滤，直接使用全部文章，等权平均。
        scores = []
        bullish_n = bearish_n = neutral_n = 0
        for article in feed:
            raw = article.get("overall_sentiment_score")
            if raw is None:
                continue
            scores.append(float(raw))
            label = article.get("overall_sentiment_label", "")
            if "Bullish" in label:
                bullish_n += 1
            elif "Bearish" in label:
                bearish_n += 1
            else:
                neutral_n += 1

        if not scores:
            print("[WARN] Alpha Vantage 过滤后无有效文章")
            return None

        # 等权平均分（-1~+1）→ bullish_pct（0~1）
        avg_score = sum(scores) / len(scores)
        bullish_pct = (avg_score + 1.0) / 2.0

        # news_score：多头占比 0.7 + 有情绪文章占比 0.3
        total_scored = bullish_n + bearish_n + neutral_n or 1
        engagement   = (bullish_n + bearish_n) / total_scored
        news_score   = bullish_pct * 0.7 + engagement * 0.3

        result = {
            "bullish_pct": round(bullish_pct, 4),
            "bearish_pct": round(1 - bullish_pct, 4),
            "news_score":  round(news_score, 4),
            "buzz":        round(engagement, 4),
            "articles":    total_scored,
            "bullish_n":   bullish_n,
            "bearish_n":   bearish_n,
            "avg_score":   round(avg_score, 4),
        }
        _save_cache(today, "alphavantage", result)
        print(
            f"[OK] Alpha Vantage 情绪（{total_scored}篇有效）: "
            f"均分{avg_score:+.3f} → 多头占比{bullish_pct*100:.1f}%  "
            f"多{bullish_n}/空{bearish_n}/中{neutral_n}"
        )
        return result

    except Exception as e:
        print(f"[WARN] Alpha Vantage 情绪获取失败: {e}")
        return None


# ── Finnhub ───────────────────────────────────────────────────────

def _score_headlines(articles: list) -> dict:
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

    total  = bullish + bearish + neutral or 1
    scored = bullish + bearish or 1
    bullish_pct = bullish / scored
    engagement  = (bullish + bearish) / total
    news_score  = bullish_pct * 0.7 + engagement * 0.3
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
    cached = _load_cache(today, "finnhub")
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
        _save_cache(today, "finnhub", result)
        print(
            f"[OK] Finnhub 新闻情绪（{result['articles']}条）: "
            f"多头{result['bullish_n']}篇 / 空头{result['bearish_n']}篇 → "
            f"多头占比{result['bullish_pct']*100:.1f}%"
        )
        return result

    except Exception as e:
        print(f"[WARN] Finnhub 情绪获取失败: {e}")
        return None


# ── 主函数 ────────────────────────────────────────────────────────

def get_market_sentiment() -> dict:
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

    # ── 新闻情绪：AV 主 + Finnhub 辅 ─────────────────────────────
    av_key  = _get_key("ALPHAVANTAGE_API_KEY", "alphavantage_api_key")
    fh_key  = _get_key("FINNHUB_API_KEY",      "finnhub_api_key")

    av      = _fetch_alphavantage(av_key, today) if av_key else None
    finnhub = _fetch_finnhub(fh_key, today)      if fh_key else None

    if av and finnhub:
        # AV 质量更高，权重 0.65；Finnhub 关键词补充 0.35
        raw_news  = av["bullish_pct"] * 0.65 + finnhub["bullish_pct"] * 0.35
        news_score = raw_news * 100.0
        news_source = "alphavantage+finnhub"
    elif av:
        news_score  = av["bullish_pct"] * 100.0
        news_source = "alphavantage"
    elif finnhub:
        news_score  = finnhub["bullish_pct"] * 100.0
        news_source = "finnhub"
    else:
        news_score  = None
        news_source = "N/A"

    if news_score is not None:
        sentiment_score = int(
            vix_score      * 0.40
            + momentum_score * 0.25
            + news_score     * 0.35
        )
    else:
        sentiment_score = int(vix_score * 0.60 + momentum_score * 0.40)

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

    if av:
        result["av_avg_score"]   = av.get("avg_score")
        result["av_bullish_pct"] = av["bullish_pct"]
        result["av_articles"]    = av["articles"]
        result["av_bullish_n"]   = av.get("bullish_n")
        result["av_bearish_n"]   = av.get("bearish_n")
    if finnhub:
        result["finnhub_bullish_pct"] = finnhub["bullish_pct"]
        result["finnhub_articles"]    = finnhub["articles"]
        result["finnhub_bullish_n"]   = finnhub.get("bullish_n")
        result["finnhub_bearish_n"]   = finnhub.get("bearish_n")

    return result
