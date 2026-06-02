"""全球各区域宏观周期分析（基于 World Bank + OECD CLI）。

为多区域 QDII 提供本地宏观背景：每个区域给出 GDP增长/通胀/失业/CLI 与一个
0-10 的宏观健康评分及周期标签，并汇总最强/最弱区域，供信号与组合解读使用。
"""
import pandas as pd
from ..utils.database import read_table


# QDII 资产规模视角的地区权重（美股占 QDII 总规模约 40%）
_REGION_WEIGHTS = {
    "美国": 0.40, "全球": 0.20, "日本": 0.12,
    "欧洲": 0.12, "德国": 0.08, "亚洲": 0.08,
}


def compute_global_macro_score(global_macro_result: dict) -> float:
    """从 analyze_global_macro() 结果计算加权全球宏观综合评分（0–10）。

    按 QDII 资产规模权重对各区域健康分加权平均；无数据区域跳过但不归零。
    返回 5.0 表示中性（无数据或全量数据不足）。
    """
    if not global_macro_result.get("available"):
        return 5.0

    regions = global_macro_result.get("regions") or {}
    weighted_sum = 0.0
    total_weight = 0.0

    for region, info in regions.items():
        score = info.get("score")
        if score is None or info.get("label") == "数据不足":
            continue
        w = _REGION_WEIGHTS.get(region, 0.05)
        weighted_sum += w * float(score)
        total_weight += w

    return round(weighted_sum / total_weight, 2) if total_weight > 0 else 5.0


def analyze_global_macro() -> dict:
    """返回 {regions: {区域名: {...}}, strongest, weakest, available}。"""
    df = read_table("global_macro")
    if df is None or df.empty:
        return {"available": False, "regions": {}, "strongest": None, "weakest": None}

    regions = {}
    for region, g in df.groupby("region"):
        gdp = _latest(g, "gdp_growth")
        inf = _latest(g, "inflation")
        unemp = _latest(g, "unemployment")
        cli = _latest(g, "cli")
        score, label = _region_score(gdp, inf, unemp, cli)
        regions[region] = {
            "gdp_growth": gdp,
            "inflation": inf,
            "unemployment": unemp,
            "cli": cli,
            "score": score,
            "label": label,
        }

    # 仅对有真实数据的区域排名（排除“数据不足”）
    scored = [kv for kv in regions.items() if kv[1]["label"] != "数据不足"]
    ranked = sorted(scored, key=lambda kv: kv[1]["score"], reverse=True)
    strongest = ranked[0][0] if ranked else None
    weakest = ranked[-1][0] if ranked else None
    return {"available": True, "regions": regions,
            "strongest": strongest, "weakest": weakest}


def _latest(g: pd.DataFrame, indicator: str):
    sub = g[g["indicator"] == indicator].sort_values("date")
    if sub.empty:
        return None
    val = sub.iloc[-1]["value"]
    return round(float(val), 2) if pd.notna(val) else None


def _region_score(gdp, inf, unemp, cli) -> tuple[float, str]:
    """综合 GDP/通胀/失业/CLI → 0-10 宏观健康分 + 周期标签。"""
    # 完全无数据时不臆断为“放缓”，明确标注数据不足
    if all(x is None for x in (gdp, inf, unemp, cli)):
        return 5.0, "数据不足"

    score = 5.0

    # 增长
    if gdp is not None:
        if gdp >= 3.0:   score += 2.0
        elif gdp >= 1.5: score += 1.0
        elif gdp >= 0:   score += 0.0
        else:            score -= 2.0

    # 通胀（温和最佳，过高过低都扣分）
    if inf is not None:
        if 1.0 <= inf <= 3.0:  score += 1.0
        elif inf > 5.0:        score -= 1.5
        elif inf > 3.0:        score -= 0.5
        elif inf < 0:          score -= 1.0   # 通缩

    # 失业（越低越好）
    if unemp is not None:
        if unemp <= 4.0:   score += 1.0
        elif unemp <= 6.0: score += 0.0
        else:              score -= 1.0

    # OECD CLI（>100 扩张，<100 放缓）
    if cli is not None:
        if cli >= 100.5:   score += 1.0
        elif cli < 99.5:   score -= 1.0

    score = max(0.0, min(10.0, score))

    if   score >= 7.5: label = "扩张"
    elif score >= 5.5: label = "温和扩张"
    elif score >= 4.0: label = "放缓"
    else:              label = "收缩"
    return round(score, 1), label
