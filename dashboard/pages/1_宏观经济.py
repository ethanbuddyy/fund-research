"""宏观经济分析页面"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots

st.set_page_config(page_title="宏观经济分析", layout="wide")
st.title("🌍 美国宏观经济分析")

from src.utils.database import read_table
from src.analyzers.macro_analyzer import analyze_macro_cycle


@st.cache_data(ttl=3600)
def load_macro():
    return analyze_macro_cycle()


@st.cache_data(ttl=3600)
def load_series(series_id: str, limit: int = 60):
    return read_table("macro_data", f"series_id = ? ORDER BY date DESC LIMIT {limit}", (series_id,))


macro = load_macro()

# 经济周期状态卡
st.subheader("经济周期判断")
col1, col2, col3, col4, col5 = st.columns(5)
cycle_colors = {"扩张": "🟢", "高峰": "🟡", "收缩": "🟠", "衰退": "🔴", "复苏": "🔵"}
cycle_icon = cycle_colors.get(macro.get("cycle", ""), "⚪")
col1.metric(f"{cycle_icon} 当前周期", macro.get("cycle", "—"))
col2.metric("GDP增速", f"{macro.get('gdp_growth', 0):.1f}%" if macro.get("gdp_growth") else "—")
col3.metric("CPI通胀", f"{macro.get('inflation', 0):.1f}%" if macro.get("inflation") else "—")
col4.metric("联邦利率", f"{macro.get('fed_rate', 0):.2f}%" if macro.get("fed_rate") else "—")
col5.metric("失业率", f"{macro.get('unemployment', 0):.1f}%" if macro.get("unemployment") else "—")

st.info(f"**周期分析：** {macro.get('cycle_description', '')}")

yield_curve = macro.get("yield_curve", 0)
if macro.get("yield_inverted"):
    st.warning(f"⚠️ 收益率曲线倒挂（10年-2年利差：{yield_curve:.2f}%），历史上衰退先行指标，请提高防守意识")
else:
    st.success(f"✅ 收益率曲线正常（10年-2年利差：{yield_curve:.2f}%），未见倒挂压力")

st.markdown(f"**货币政策：** {macro.get('policy_env', '')} — {macro.get('policy_note', '')}")

st.divider()

# 宏观数据图表
st.subheader("关键宏观指标走势")
tab1, tab2, tab3, tab4 = st.tabs(["利率与国债", "通胀", "就业", "货币供应"])

with tab1:
    rate_df = load_series("FEDFUNDS", 60).sort_values("date")
    t10_df = load_series("GS10", 60).sort_values("date")
    t2_df = load_series("GS2", 60).sort_values("date")

    fig = go.Figure()
    if not rate_df.empty:
        fig.add_trace(go.Scatter(x=rate_df["date"], y=rate_df["value"], name="联邦基金利率", line=dict(color="red", width=2)))
    if not t10_df.empty:
        fig.add_trace(go.Scatter(x=t10_df["date"], y=t10_df["value"], name="10年国债收益率", line=dict(color="blue", width=2)))
    if not t2_df.empty:
        fig.add_trace(go.Scatter(x=t2_df["date"], y=t2_df["value"], name="2年国债收益率", line=dict(color="orange", width=2, dash="dash")))
    fig.update_layout(title="利率走势（%）", xaxis_title="日期", yaxis_title="%", height=400)
    st.plotly_chart(fig, use_container_width=True)

with tab2:
    cpi_df = load_series("CPIAUCSL", 60).sort_values("date")
    core_cpi_df = load_series("CPILFESL", 60).sort_values("date")

    fig = go.Figure()
    if not cpi_df.empty:
        # 计算同比
        cpi_df["yoy"] = cpi_df["value"].pct_change(12) * 100
        fig.add_trace(go.Scatter(x=cpi_df["date"], y=cpi_df["yoy"], name="CPI同比", line=dict(color="purple", width=2)))
    if not core_cpi_df.empty:
        core_cpi_df["yoy"] = core_cpi_df["value"].pct_change(12) * 100
        fig.add_trace(go.Scatter(x=core_cpi_df["date"], y=core_cpi_df["yoy"], name="核心CPI同比", line=dict(color="green", width=2, dash="dash")))
    fig.add_hline(y=2.0, line_dash="dot", annotation_text="Fed目标2%", line_color="red")
    fig.update_layout(title="通胀走势（%，同比）", height=400)
    st.plotly_chart(fig, use_container_width=True)

with tab3:
    unemp_df = load_series("UNRATE", 60).sort_values("date")
    fig = go.Figure()
    if not unemp_df.empty:
        fig.add_trace(go.Scatter(x=unemp_df["date"], y=unemp_df["value"], name="失业率",
                                  fill="tozeroy", fillcolor="rgba(255,100,100,0.2)", line=dict(color="red")))
    fig.add_hline(y=4.0, line_dash="dot", annotation_text="充分就业基准4%", line_color="green")
    fig.update_layout(title="美国失业率（%）", height=400)
    st.plotly_chart(fig, use_container_width=True)

with tab4:
    m2_df = load_series("M2SL", 60).sort_values("date")
    fig = go.Figure()
    if not m2_df.empty:
        fig.add_trace(go.Bar(x=m2_df["date"], y=m2_df["value"], name="M2货币供应量（十亿美元）", marker_color="steelblue"))
    fig.update_layout(title="M2货币供应量", height=400)
    st.plotly_chart(fig, use_container_width=True)

st.divider()
st.caption("数据来源：美联储FRED数据库 | 更新频率：每日")
