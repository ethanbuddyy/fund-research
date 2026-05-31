"""综合投资建议页面"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

st.set_page_config(page_title="投资建议", layout="wide")
st.title("💡 综合投资建议")
st.caption("融合五大投资大师方法论 — 目标年化收益率20%")

from src.recommender.signals import generate_market_signal
from src.recommender.portfolio import build_portfolio_recommendation
from src.recommender.scorer import score_all_funds
from src.utils.database import read_table

# 侧边栏：数据更新
with st.sidebar:
    st.header("系统控制")
    if st.button("🔄 更新全部数据", type="primary", use_container_width=True):
        with st.spinner("更新宏观数据..."):
            from src.collectors.macro_collector import collect_macro_data
            collect_macro_data()
        with st.spinner("更新市场数据..."):
            from src.collectors.market_collector import collect_market_data
            collect_market_data()
        with st.spinner("更新QDII基金数据..."):
            from src.collectors.fund_collector import collect_fund_data
            collect_fund_data()
        with st.spinner("计算基金绩效..."):
            from src.analyzers.fund_analyzer import analyze_all_funds
            analyze_all_funds()
        with st.spinner("生成投资信号..."):
            signal = generate_market_signal()
            score_all_funds(signal)
        st.success("数据更新完成！")
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.markdown("**数据更新时间**")
    signals_db = read_table("market_signals", "1=1 ORDER BY date DESC LIMIT 1")
    if not signals_db.empty:
        st.info(f"最后更新: {signals_db.iloc[0].get('date', '—')}")

# 生成信号（展示时不写库；写库由上方"更新全部数据"按钮负责）
try:
    with st.spinner("生成综合投资信号..."):
        signal = generate_market_signal(save=False)
    portfolio = build_portfolio_recommendation(signal)
except Exception as e:
    st.error(f"信号生成失败: {e}\n\n请先点击侧边栏「更新全部数据」")
    st.stop()

# 综合信号Banner
composite = signal.get("composite_signal", "标配稳健")
signal_colors = {"重仓进取": "🟢", "标配稳健": "🔵", "谨慎防守": "🟠", "减仓防守": "🔴"}
icon = signal_colors.get(composite, "⚪")

st.markdown(f"""
<div style="background-color: #f0f8ff; padding: 20px; border-radius: 10px; border-left: 5px solid steelblue;">
<h2>{icon} 当前综合信号：<strong>{composite}</strong></h2>
</div>
""", unsafe_allow_html=True)

st.divider()

# 核心指标概览
st.subheader("市场关键指标")
m = signal
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("经济周期", m.get("macro_cycle", "—"))
c2.metric("估值水位", m.get("valuation_level", "—"))
c3.metric("市场情绪", m.get("sentiment_label", "—"))
c4.metric("CAPE", f"{m.get('cape', 0):.1f}" if m.get("cape") else "—")
c5.metric("巴菲特指标", f"{m.get('buffett_indicator', 0):.2f}" if m.get("buffett_indicator") else "—")
c6.metric("股权溢价ERP", f"{m.get('equity_risk_premium', 0):.2f}%" if m.get("equity_risk_premium") else "—")

st.divider()

# 仓位建议
st.subheader("推荐仓位配置")
col1, col2 = st.columns([1, 2])

with col1:
    core_pct = portfolio.get("core_allocation_pct", 60)
    sat_pct = portfolio.get("satellite_allocation_pct", 30)
    cash_pct = portfolio.get("cash_allocation_pct", 10)

    st.metric("核心仓位 (宽基指数ETF)", f"{core_pct:.0f}%")
    st.metric("卫星仓位 (行业/主动QDII)", f"{sat_pct:.0f}%")
    st.metric("现金/防守", f"{cash_pct:.0f}%")

with col2:
    fig = go.Figure(go.Pie(
        labels=["核心仓位\n(宽基指数)", "卫星仓位\n(行业/主题)", "现金防守"],
        values=[core_pct, sat_pct, cash_pct],
        hole=0.4,
        marker_colors=["#2196F3", "#4CAF50", "#FF9800"],
        textinfo="label+percent",
    ))
    fig.update_layout(title=f"推荐仓位分配 — {composite}", height=300, margin=dict(t=40, b=0))
    st.plotly_chart(fig, use_container_width=True)

st.divider()

# 推荐基金
st.subheader("推荐配置基金")
core_funds = portfolio.get("core_funds", [])
sat_funds = portfolio.get("satellite_funds", [])

if core_funds or sat_funds:
    all_rec = core_funds + sat_funds
    rec_data = []
    for f in all_rec:
        rec_data.append({
            "角色": f.get("role", ""),
            "基金名称": f.get("fund_name", f.get("fund_code")),
            "代码": f.get("fund_code"),
            "建议仓位": f"{f.get('weight', 0):.1f}%",
            "评分": f"{f.get('score', 0):.1f}",
            "信号": f.get("signal", "持有"),
        })
    st.table(pd.DataFrame(rec_data))
else:
    st.info("暂无推荐基金数据，请先更新数据")

# 投资建议要点
st.divider()
st.subheader("投资决策要点")
notes = portfolio.get("investment_notes", [])
for note in notes:
    st.info(f"📌 {note}")

# 大师共识
st.divider()
st.subheader("五大投资大师共识")
masters = signal.get("masters", {})
avg_score = masters.get("avg_score", 5)

st.metric("大师综合评分", f"{avg_score:.1f}/10",
          delta="看多" if avg_score >= 6 else "中性" if avg_score >= 4 else "看空")

master_data = [
    {"大师": "格雷厄姆（安全边际）", "评分": masters.get("graham", {}).get("score", 5),
     "行动建议": masters.get("graham", {}).get("action", "")},
    {"大师": "巴菲特（品质+逆向）", "评分": masters.get("buffett", {}).get("score", 5),
     "行动建议": masters.get("buffett", {}).get("action", "")},
    {"大师": "博格（指数+低成本）", "评分": masters.get("bogle", {}).get("score", 5),
     "行动建议": masters.get("bogle", {}).get("action", "")},
    {"大师": "彼得林奇（GARP成长）", "评分": masters.get("lynch", {}).get("score", 5),
     "行动建议": masters.get("lynch", {}).get("action", "")},
    {"大师": "西格尔（长期权益）", "评分": masters.get("siegel", {}).get("score", 5),
     "行动建议": masters.get("siegel", {}).get("action", "")},
]

master_df = pd.DataFrame(master_data)
fig = go.Figure(go.Bar(
    x=[d["大师"].split("（")[0] for d in master_data],
    y=[d["评分"] for d in master_data],
    marker_color=["#2ca02c" if d["评分"] >= 7 else "#ff7f0e" if d["评分"] >= 5 else "#d62728" for d in master_data],
    text=[f"{d['评分']}/10" for d in master_data],
    textposition="outside",
))
fig.add_hline(y=5, line_dash="dot", annotation_text="中性线(5分)", line_color="gray")
fig.update_layout(title="五大投资大师当前市场观点评分", height=350, yaxis=dict(range=[0, 12]))
st.plotly_chart(fig, use_container_width=True)

st.table(master_df.rename(columns={"大师": "投资大师", "评分": "得分(10分制)", "行动建议": "建议行动"}))

st.divider()

# 定投计划
st.subheader("定投方案建议（博格+西格尔长期策略）")
invest_amount = st.number_input("每月定投金额（元）", min_value=100, max_value=1000000,
                                  value=5000, step=500)
invest_years = st.slider("计划定投年限", 1, 30, 10)

# 简单复利计算
annual_rate = 0.12  # 假设12%基础年化（保守于目标20%）
monthly_rate = annual_rate / 12
months = invest_years * 12
fv = invest_amount * ((1 + monthly_rate) ** months - 1) / monthly_rate * (1 + monthly_rate)
total_invested = invest_amount * months
profit = fv - total_invested

c1, c2, c3 = st.columns(3)
c1.metric("预计总投入", f"¥{total_invested:,.0f}")
c2.metric("预计终值（年化12%）", f"¥{fv:,.0f}")
c3.metric("预计收益", f"¥{profit:,.0f}", f"+{profit/total_invested*100:.0f}%")
st.caption("⚠️ 投资有风险，以上为历史数据模拟，实际收益可能有差异。本系统仅供参考，不构成投资建议。")
