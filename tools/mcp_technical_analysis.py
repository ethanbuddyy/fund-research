"""技术分析 MCP 服务器 — 为 Claude Code 提供 RSI / MACD / 均线 / 布林带等指标。

依赖：mcp[cli]  yfinance  pandas  numpy（项目已有）
启动：python3 tools/mcp_technical_analysis.py
"""
import sys
import json
import asyncio
import pandas as pd
import numpy as np

try:
    import yfinance as yf
except ImportError:
    yf = None

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

app = Server("technical-analysis")


# ── 指标计算 ──────────────────────────────────────────────────────────────────

def _rsi(close: pd.Series, period: int = 14) -> float:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    return round(float(rsi.dropna().iloc[-1]), 2)


def _macd(close: pd.Series):
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    histogram = macd_line - signal_line
    return (round(float(macd_line.iloc[-1]), 4),
            round(float(signal_line.iloc[-1]), 4),
            round(float(histogram.iloc[-1]), 4))


def _bollinger(close: pd.Series, period: int = 20):
    ma = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = ma + 2 * std
    lower = ma - 2 * std
    last = close.iloc[-1]
    bw = round(float((upper.iloc[-1] - lower.iloc[-1]) / ma.iloc[-1] * 100), 2)
    pct_b = round(float((last - lower.iloc[-1]) / (upper.iloc[-1] - lower.iloc[-1])), 3)
    return (round(float(upper.iloc[-1]), 2),
            round(float(ma.iloc[-1]), 2),
            round(float(lower.iloc[-1]), 2),
            bw, pct_b)


def _moving_averages(close: pd.Series):
    result = {}
    for period in [5, 10, 20, 50, 200]:
        if len(close) >= period:
            result[f"ma{period}"] = round(float(close.rolling(period).mean().iloc[-1]), 4)
    return result


def _fetch_history(symbol: str, period: str = "6mo") -> pd.DataFrame:
    if yf is None:
        raise RuntimeError("yfinance 未安装")
    ticker = yf.Ticker(symbol)
    hist = ticker.history(period=period)
    if hist.empty:
        raise ValueError(f"无法获取 {symbol} 数据")
    return hist


# ── MCP 工具注册 ───────────────────────────────────────────────────────────────

@app.list_tools()
async def list_tools():
    return [
        types.Tool(
            name="get_technical_indicators",
            description="获取标的的 RSI、MACD、布林带、均线等技术指标",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Yahoo Finance 代码，如 ^GSPC、AAPL、518880.SS"},
                    "period": {"type": "string", "description": "历史区间，如 3mo / 6mo / 1y，默认 6mo", "default": "6mo"},
                },
                "required": ["symbol"],
            },
        ),
        types.Tool(
            name="get_trend_signal",
            description="基于技术指标给出趋势判断（看多/中性/看空）及简要理由",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Yahoo Finance 代码"},
                },
                "required": ["symbol"],
            },
        ),
        types.Tool(
            name="compare_fund_technicals",
            description="对比多只基金/ETF 的技术指标，辅助筛选",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbols": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Yahoo Finance 代码列表，最多 8 个",
                    },
                },
                "required": ["symbols"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict):
    try:
        if name == "get_technical_indicators":
            symbol = arguments["symbol"]
            period = arguments.get("period", "6mo")
            hist = _fetch_history(symbol, period)
            close = hist["Close"]
            rsi = _rsi(close)
            macd, signal, hist_val = _macd(close)
            bb_upper, bb_mid, bb_lower, bw, pct_b = _bollinger(close)
            mas = _moving_averages(close)
            last_price = round(float(close.iloc[-1]), 4)
            result = {
                "symbol": symbol,
                "last_price": last_price,
                "rsi_14": rsi,
                "macd": {"macd": macd, "signal": signal, "histogram": hist_val},
                "bollinger_bands": {
                    "upper": bb_upper, "mid": bb_mid, "lower": bb_lower,
                    "bandwidth_pct": bw, "pct_b": pct_b,
                },
                "moving_averages": mas,
            }
            return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

        elif name == "get_trend_signal":
            symbol = arguments["symbol"]
            hist = _fetch_history(symbol, "6mo")
            close = hist["Close"]
            rsi = _rsi(close)
            macd, signal, histogram = _macd(close)
            mas = _moving_averages(close)
            last = float(close.iloc[-1])

            # 简单多空判断
            bullish = 0
            bearish = 0
            reasons = []

            if rsi < 30:
                bullish += 2; reasons.append(f"RSI={rsi} 超卖（买入信号）")
            elif rsi > 70:
                bearish += 2; reasons.append(f"RSI={rsi} 超买（卖出信号）")
            else:
                reasons.append(f"RSI={rsi} 中性区间")

            if histogram > 0 and macd > signal:
                bullish += 1; reasons.append("MACD 金叉（柱状图为正）")
            elif histogram < 0 and macd < signal:
                bearish += 1; reasons.append("MACD 死叉（柱状图为负）")

            if "ma20" in mas and "ma50" in mas:
                if mas["ma20"] > mas["ma50"]:
                    bullish += 1; reasons.append(f"MA20({mas['ma20']:.2f}) > MA50({mas['ma50']:.2f}) 短期强势")
                else:
                    bearish += 1; reasons.append(f"MA20({mas['ma20']:.2f}) < MA50({mas['ma50']:.2f}) 短期弱势")

            if bullish > bearish:
                trend = "看多"
            elif bearish > bullish:
                trend = "看空"
            else:
                trend = "中性"

            result = {
                "symbol": symbol,
                "last_price": round(last, 4),
                "trend": trend,
                "bullish_signals": bullish,
                "bearish_signals": bearish,
                "reasons": reasons,
            }
            return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

        elif name == "compare_fund_technicals":
            symbols = arguments["symbols"][:8]
            rows = []
            for sym in symbols:
                try:
                    hist = _fetch_history(sym, "6mo")
                    close = hist["Close"]
                    rsi = _rsi(close)
                    macd, sig, hval = _macd(close)
                    _, bb_mid, _, bw, pct_b = _bollinger(close)
                    mas = _moving_averages(close)
                    trend = ("看多" if (rsi < 50 and hval > 0) else
                             "看空" if (rsi > 60 and hval < 0) else "中性")
                    rows.append({
                        "symbol": sym,
                        "last": round(float(close.iloc[-1]), 4),
                        "rsi": rsi,
                        "macd_hist": hval,
                        "bb_pct_b": pct_b,
                        "ma20_vs_ma50": round(mas.get("ma20", 0) / mas.get("ma50", 1) - 1, 4),
                        "trend": trend,
                    })
                except Exception as e:
                    rows.append({"symbol": sym, "error": str(e)})
            return [types.TextContent(type="text", text=json.dumps(rows, ensure_ascii=False, indent=2))]

        else:
            return [types.TextContent(type="text", text=f"未知工具: {name}")]

    except Exception as e:
        return [types.TextContent(type="text", text=json.dumps({"error": str(e)}, ensure_ascii=False))]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
