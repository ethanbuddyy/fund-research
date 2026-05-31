"""基金投资私人幕僚系统 — 主入口"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

st.set_page_config(
    page_title="基金投资私人幕僚",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("📈 基金投资私人幕僚系统")
st.markdown("**融合巴菲特·格雷厄姆·博格·西格尔·彼得林奇 — 目标年化收益20%**")

st.markdown("---")

col1, col2, col3, col4 = st.columns(4)

with col1:
    st.page_link("pages/1_宏观经济.py", label="🌍 宏观经济分析", icon="🌍")
    st.caption("GDP、CPI、利率、经济周期")

with col2:
    st.page_link("pages/2_市场分析.py", label="📊 市场行情分析", icon="📊")
    st.caption("美股指数、行业轮动、估值水位")

with col3:
    st.page_link("pages/3_基金筛选.py", label="🔍 QDII基金筛选", icon="🔍")
    st.caption("基金评分、绩效对比、大师策略")

with col4:
    st.page_link("pages/4_投资建议.py", label="💡 投资建议", icon="💡")
    st.caption("综合信号、组合方案、仓位建议")

st.markdown("---")

# 快速状态总览（从数据库读取最新信号）
try:
    from src.utils.database import read_table

    signals = read_table("market_signals", "1=1 ORDER BY date DESC LIMIT 1")
    if not signals.empty:
        s = signals.iloc[0]
        st.subheader("当前市场综合信号")
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("综合信号", s.get("composite_signal", "—"))
        c2.metric("经济周期", s.get("macro_cycle", "—"))
        c3.metric("估值水位", s.get("valuation_level", "—"))
        c4.metric("CAPE", f"{s.get('cape', 0):.1f}" if s.get('cape') else "—")
        c5.metric("VIX", f"{s.get('vix', 0):.1f}" if s.get('vix') else "—")

        alloc_cols = st.columns(3)
        alloc_cols[0].metric("核心仓位", f"{s.get('core_allocation', 0)*100:.0f}%")
        alloc_cols[1].metric("卫星仓位", f"{s.get('satellite_allocation', 0)*100:.0f}%")
        alloc_cols[2].metric("现金", f"{s.get('cash_allocation', 0)*100:.0f}%")
    else:
        st.info("暂无信号数据，请先在侧边栏点击「更新数据」")
except Exception as e:
    st.info("请先运行数据更新以加载信号")

st.markdown("---")
st.caption("数据来源：FRED API（美国宏观）| Yahoo Finance（市场行情）| akshare（QDII基金）| 仅供参考，不构成投资建议")
