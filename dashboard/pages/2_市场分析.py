"""市场行情分析页面"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px

st.set_page_config(page_title="市场行情分析", layout="wide")
st.title("📊 美股及全球市场分析")

from src.utils.database import read_table
from src.analyzers.valuation import calculate_valuation_metrics
from src.collectors.news_collector import get_market_sentiment
from src.utils.config import load_config


@st.cache_data(ttl=1800)
def load_valuation():
    return calculate_valuation_metrics()


@st.cache_data(ttl=1800)
def load_sentiment():
    return get_market_sentiment()


@st.cache_data(ttl=1800)
def load_market(symbol: str, limit: int = 252):
    df = read_table("market_data", f"symbol = ? ORDER BY date DESC LIMIT {limit}", (symbol,))
    return df.sort_values("date") if not df.empty else df


cfg = load_config()
val = load_valuation()
sent = load_sentiment()

# 估值水位 + 情绪
st.subheader("市场估值与情绪")
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Shiller CAPE", f"{val.get('cape', 0):.1f}", help=">30高估，<15低估，历史均值约17")
c2.metric("标普500 P/E", f"{val.get('sp500_pe', 0):.1f}")
c3.metric("巴菲特指标", f"{val.get('buffett_indicator', 0):.2f}", help="总市值/GDP，>1.2高估")
c4.metric("VIX 恐慌指数", f"{sent.get('vix', 0):.1f}")
c5.metric(f"情绪 {sent.get('icon', '')}", sent.get("label", "—"), f"得分 {sent.get('score', 0)}")

st.metric("股权风险溢价（ERP）", f"{val.get('equity_risk_premium', 0):.2f}%",
          help="股票预期收益率 - 10年国债收益率，>3%有吸引力")

level = val.get("valuation_level", "合理")
color_map = {"偏低": "success", "合理": "info", "偏高": "warning", "高估": "error"}
msg_fn = getattr(st, color_map.get(level, "info"))
msg_fn(f"**市场估值水位：{level}** | 10年国债收益率 {val.get('treasury_10y', 0):.2f}%")

st.divider()

# 主要指数走势
st.subheader("主要指数走势")
tab1, tab2, tab3 = st.tabs(["美股三大指数", "全球市场", "行业轮动"])

with tab1:
    major = [("^GSPC", "标普500", "blue"), ("^IXIC", "纳斯达克", "green"), ("^DJI", "道琼斯", "red")]
    fig = go.Figure()
    for symbol, name, color in major:
        df = load_market(symbol, 252)
        if not df.empty:
            # 归一化为100
            base = float(df.iloc[0]["close"])
            df["norm"] = df["close"] / base * 100
            fig.add_trace(go.Scatter(x=df["date"], y=df["norm"], name=name,
                                      line=dict(color=color, width=2)))
    fig.update_layout(title="美股三大指数（归一化至100）", height=450,
                       xaxis_title="日期", yaxis_title="指数（归一化）")
    st.plotly_chart(fig, use_container_width=True)

with tab2:
    global_idx = [
        ("^GSPC", "标普500(美)"), ("^N225", "日经225"), ("^FTSE", "富时100"),
        ("^GDAXI", "DAX(德)"), ("000300.SS", "沪深300"),
    ]
    fig = go.Figure()
    for symbol, name in global_idx:
        df = load_market(symbol, 252)
        if not df.empty:
            base = float(df.iloc[0]["close"])
            df["norm"] = df["close"] / base * 100
            fig.add_trace(go.Scatter(x=df["date"], y=df["norm"], name=name, line=dict(width=2)))
    fig.update_layout(title="全球主要指数（归一化至100）", height=450)
    st.plotly_chart(fig, use_container_width=True)

with tab3:
    sector_etfs = cfg.get("sector_etfs", [])
    sector_returns = []
    for etf in sector_etfs:
        df = load_market(etf["symbol"], 21)
        if len(df) >= 5:
            ret = (float(df.iloc[-1]["close"]) / float(df.iloc[0]["close"]) - 1) * 100
            sector_returns.append({"板块": etf["name"], "月度涨跌幅(%)": round(ret, 2)})

    if sector_returns:
        sr_df = pd.DataFrame(sector_returns).sort_values("月度涨跌幅(%)", ascending=True)
        colors = ["#d62728" if v < 0 else "#2ca02c" for v in sr_df["月度涨跌幅(%)"]]
        fig = go.Figure(go.Bar(
            x=sr_df["月度涨跌幅(%)"], y=sr_df["板块"],
            orientation="h", marker_color=colors,
            text=sr_df["月度涨跌幅(%)"].apply(lambda v: f"{v:+.2f}%"),
            textposition="outside",
        ))
        fig.update_layout(title="美国11大行业ETF月度表现（彼得林奇行业轮动）", height=450)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("行业数据加载中，请先更新市场数据")

st.divider()

# VIX + 商品
col1, col2 = st.columns(2)
with col1:
    st.subheader("VIX 恐慌指数")
    vix_df = load_market("^VIX", 252)
    if not vix_df.empty:
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=vix_df["date"], y=vix_df["close"], fill="tozeroy",
                                  fillcolor="rgba(255,100,100,0.15)", line=dict(color="red")))
        fig.add_hline(y=30, line_dash="dash", annotation_text="恐惧区(30)", line_color="red")
        fig.add_hline(y=15, line_dash="dash", annotation_text="贪婪区(15)", line_color="green")
        fig.update_layout(height=300, title="VIX（>30=极恐，<15=贪婪）")
        st.plotly_chart(fig, use_container_width=True)

with col2:
    st.subheader("黄金与原油")
    gold_df = load_market("GC=F", 252)
    oil_df = load_market("CL=F", 252)
    if not gold_df.empty or not oil_df.empty:
        fig = go.Figure()
        if not gold_df.empty:
            fig.add_trace(go.Scatter(x=gold_df["date"], y=gold_df["close"], name="黄金(美元/盎司)", line=dict(color="gold")))
        if not oil_df.empty:
            fig.add_trace(go.Scatter(x=oil_df["date"], y=oil_df["close"], name="原油(美元/桶)",
                                      line=dict(color="brown"), yaxis="y2"))
        fig.update_layout(
            height=300, title="黄金与原油",
            yaxis2=dict(overlaying="y", side="right"),
        )
        st.plotly_chart(fig, use_container_width=True)

st.caption("数据来源：Yahoo Finance | 更新频率：每日")
