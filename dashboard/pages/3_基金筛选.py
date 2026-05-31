"""QDII基金筛选与评分页面"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px

st.set_page_config(page_title="QDII基金筛选", layout="wide")
st.title("🔍 QDII基金筛选与评分")

from src.utils.database import read_table
from src.analyzers.masters import graham, buffett, bogle, lynch, siegel
from src.utils.config import load_config


@st.cache_data(ttl=3600)
def load_scores():
    scores = read_table("fund_scores")
    funds = read_table("fund_list")
    if scores.empty or funds.empty:
        return pd.DataFrame()
    merged = scores.merge(
        funds[["fund_code", "fund_type", "expense_ratio", "manager", "company"]],
        on="fund_code", how="left"
    )
    return merged.sort_values("total_score", ascending=False)


@st.cache_data(ttl=3600)
def load_perf():
    return read_table("fund_performance")


cfg = load_config()
scores_df = load_scores()
perf_df = load_perf()

if scores_df.empty:
    st.info("暂无基金评分数据，请先在侧边栏更新数据")
    st.stop()

# 筛选控件
st.subheader("基金筛选条件")
col1, col2, col3 = st.columns(3)
with col1:
    fund_types = ["全部"] + sorted(scores_df["fund_type"].dropna().unique().tolist())
    selected_type = st.selectbox("基金类型", fund_types)
with col2:
    min_score = st.slider("最低综合评分", 0, 100, 50)
with col3:
    signals_filter = st.multiselect("信号筛选", ["买入", "增持", "持有", "观望", "回避"], default=["买入", "增持", "持有"])

# 应用筛选
filtered = scores_df.copy()
if selected_type != "全部":
    filtered = filtered[filtered["fund_type"] == selected_type]
filtered = filtered[filtered["total_score"] >= min_score]
if signals_filter:
    filtered = filtered[filtered["signal"].isin(signals_filter)]

st.markdown(f"**筛选结果：{len(filtered)} 只基金**")

# 评分排行榜
st.subheader("综合评分排行")

display_cols = ["fund_name", "fund_code", "fund_type", "total_score",
                "performance_score", "risk_score", "strategy_score", "timing_score", "cost_score", "signal"]
available_cols = [c for c in display_cols if c in filtered.columns]

def highlight_signal(val):
    color_map = {"买入": "background-color: #90EE90", "增持": "background-color: #98FB98",
                  "持有": "background-color: #F0F8FF", "观望": "background-color: #FFFACD",
                  "回避": "background-color: #FFB6C1"}
    return color_map.get(val, "")

styled = filtered[available_cols].rename(columns={
    "fund_name": "基金名称", "fund_code": "代码", "fund_type": "类型",
    "total_score": "综合得分", "performance_score": "绩效", "risk_score": "风险",
    "strategy_score": "策略匹配", "timing_score": "时机", "cost_score": "成本", "signal": "信号",
}).head(20)

st.dataframe(
    styled.style.applymap(highlight_signal, subset=["信号"]),
    use_container_width=True, height=400
)

st.divider()

# 基金详情对比
st.subheader("绩效对比（Top 10）")
if not perf_df.empty and not filtered.empty:
    top10 = filtered.head(10)["fund_code"].tolist()
    perf_top = perf_df[perf_df["fund_code"].isin(top10)].merge(
        filtered[["fund_code", "fund_name"]].drop_duplicates(), on="fund_code", how="left"
    )

    if not perf_top.empty:
        col1, col2 = st.columns(2)
        with col1:
            returns_data = perf_top[["fund_name", "return_1y", "return_3y"]].dropna(subset=["return_1y"])
            fig = go.Figure()
            fig.add_trace(go.Bar(name="1年收益%", x=returns_data["fund_name"], y=returns_data["return_1y"], marker_color="steelblue"))
            if "return_3y" in returns_data.columns:
                fig.add_trace(go.Bar(name="3年累计%", x=returns_data["fund_name"], y=returns_data["return_3y"], marker_color="lightblue"))
            fig.update_layout(title="历史收益率对比", barmode="group", height=350, xaxis_tickangle=-30)
            st.plotly_chart(fig, use_container_width=True)

        with col2:
            risk_data = perf_top[["fund_name", "sharpe_ratio", "max_drawdown"]].dropna()
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=risk_data["max_drawdown"], y=risk_data["sharpe_ratio"],
                mode="markers+text", text=risk_data["fund_name"],
                textposition="top center", marker=dict(size=12, color="steelblue"),
            ))
            fig.update_layout(title="风险收益图（右上角最优）", height=350,
                               xaxis_title="最大回撤%（越小越好）",
                               yaxis_title="夏普比率（越高越好）")
            st.plotly_chart(fig, use_container_width=True)

st.divider()

# 五大投资大师分析
st.subheader("五大投资大师策略分析")

from src.recommender.signals import generate_market_signal

@st.cache_data(ttl=3600)
def load_signal():
    # save=False：展示页面只读，不写库；数据更新由 run.py / 4_投资建议.py 负责
    return generate_market_signal(save=False)

try:
    with st.spinner("加载大师分析..."):
        signal = load_signal()
    masters = signal.get("masters", {})

    master_cols = st.columns(5)
    master_items = [
        ("格雷厄姆", masters.get("graham", {}), "🏛️"),
        ("巴菲特", masters.get("buffett", {}), "🐂"),
        ("博格", masters.get("bogle", {}), "📦"),
        ("彼得林奇", masters.get("lynch", {}), "🔍"),
        ("西格尔", masters.get("siegel", {}), "📈"),
    ]

    for col, (name, data, icon) in zip(master_cols, master_items):
        with col:
            score = data.get("score", 5)
            label = data.get("label", "")
            action = data.get("action", "")
            color = "🟢" if score >= 7 else "🟡" if score >= 5 else "🔴"
            st.metric(f"{icon} {name}", f"{color} {score}/10")
            st.caption(f"**{label}**\n\n{action}")

    st.divider()
    st.subheader("大师洞见详情")
    for name, data, icon in master_items:
        with st.expander(f"{icon} {name} — {data.get('action', '')}"):
            for insight in data.get("insights", []):
                st.write(f"• {insight}")

except Exception as e:
    st.warning(f"大师分析加载失败: {e}")

st.caption("综合评分 = 绩效(25%) + 风险(20%) + 策略匹配(20%) + 市场时机(20%) + 成本(15%)")
