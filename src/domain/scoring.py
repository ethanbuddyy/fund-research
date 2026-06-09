"""
共享纯函数：基金评分计算核心。

scorer.py（生产）和 backtester/engine.py（回测）共用这里的实现，
避免两处独立维护导致口径漂移。所有函数均为无副作用的纯计算，不读写数据库。
"""
import numpy as np
import pandas as pd


def category_percentile(
    df: pd.DataFrame,
    col: str,
    group_col: str,
    low_is_good: bool = False,
    min_group: int = 3,
) -> pd.Series:
    """类别内百分位排名，映射到 0–10。

    - NaN（真正缺失）→ 强制 0 分，不参与有效排名竞争（na_option='bottom'）。
    - low_is_good=False → 越大越好；low_is_good=True → 越小越好。
    - 类别内基金数 < min_group 时退回全局排名，避免单基金假满分。
    """
    asc = not low_is_good
    result = pd.Series(0.0, index=df.index)
    global_ranks = df[col].rank(pct=True, ascending=asc, na_option="bottom")

    for _, idx in df.groupby(group_col).groups.items():
        if len(idx) >= min_group:
            ranks = df.loc[idx, col].rank(pct=True, ascending=asc, na_option="bottom")
        else:
            ranks = global_ranks.loc[idx]
        result.loc[idx] = ranks * 10

    result[df[col].isna()] = 0.0
    return result.clip(0, 10)


def consistency_score(ann_returns: list) -> float:
    """跨期收益稳定性评分（0–10）。

    基于已有期间数据，衡量正收益占比和期间离散度。
    需要至少 2 个期间数据，否则返回中性值 5.0。
    """
    avail = [v for v in ann_returns if v is not None]
    if len(avail) < 2:
        return 5.0
    pos_ratio = sum(1 for v in avail if v > 0) / len(avail)
    std = float(np.std(avail))
    return float(np.clip(pos_ratio * 7.0 + max(0.0, 1.0 - std / 30.0) * 3.0, 0.0, 10.0))


def cost_score(expense_ratio: float, cfg: dict) -> float:
    """费率评分（0–10）：费率越低分越高。"""
    params = cfg.get("strategy_params", {}).get("cost_filter", {})
    pref = params.get("preferred_expense_ratio", 0.005)
    max_er = params.get("max_expense_ratio", 0.015)

    if expense_ratio <= pref:
        return 10.0
    elif expense_ratio <= max_er:
        return 10.0 - (expense_ratio - pref) / (max_er - pref) * 5.0
    else:
        return max(0.0, 5.0 - (expense_ratio - max_er) * 100.0)


# 仓位档位表（单一真相源）：档位名 → (核心, 卫星, 现金)，三者和为 1.0。
# classify_signal、报告情景渲染、AI 情景审查均引用此表，确保绝对仓位数字
# 由确定性逻辑给出，LLM 只负责"选哪个档"而不自行计算百分比（杜绝算术矛盾）。
POSITION_TIERS: dict[str, tuple[float, float, float]] = {
    "重仓进取": (0.70, 0.25, 0.05),
    "标配稳健": (0.60, 0.30, 0.10),
    "谨慎防守": (0.50, 0.20, 0.30),
    "减仓防守": (0.35, 0.15, 0.50),
}


def classify_signal(composite_raw: float) -> tuple[str, float, float, float]:
    """将综合评分映射为信号档位和仓位建议。

    Returns:
        (composite_signal, core_alloc, satellite_alloc, cash_alloc)
    """
    if composite_raw >= 7.0:
        name = "重仓进取"
    elif composite_raw >= 5.0:
        name = "标配稳健"
    elif composite_raw >= 3.0:
        name = "谨慎防守"
    else:
        name = "减仓防守"
    core, satellite, cash = POSITION_TIERS[name]
    return name, core, satellite, cash


def tier_allocation_str(tier: str) -> str:
    """档位名 → '核心60%/卫星30%/现金10%'；未知档位返回空串。"""
    alloc = POSITION_TIERS.get(tier)
    if not alloc:
        return ""
    c, s, h = alloc
    return f"核心{c*100:.0f}%/卫星{s*100:.0f}%/现金{h*100:.0f}%"


def format_scenario_case(case, include_actions: bool = True) -> str:
    """把单个情景（结构化 dict 或旧式纯字符串）渲染为一行可读文本。

    结构化字段：trigger（触发条件）/ target_tier（目标档位，取自 POSITION_TIERS）/
    fund_actions（基金方向）。目标档位的绝对仓位由 tier_allocation_str 确定性填充，
    LLM 不在文字里写百分比，从根上消除"加减对不齐"的算术矛盾。
    兼容降级：若模型仍返回纯字符串，原样透传。

    include_actions=False 时省略「操作」段——情景表只回答「会怎样」，
    具体操作收归「何时改变」唯一出处，避免同一指令多处重复（报告层去重）。
    """
    if isinstance(case, str):
        return case
    if not isinstance(case, dict):
        return "—"
    parts = []
    if case.get("trigger"):
        parts.append(f"触发：{case['trigger']}")
    tier = case.get("target_tier")
    if tier:
        alloc = tier_allocation_str(tier)
        parts.append(f"目标档位：{tier}（{alloc}）" if alloc else f"目标档位：{tier}")
    if include_actions and case.get("fund_actions"):
        parts.append(f"操作：{case['fund_actions']}")
    return " ｜ ".join(parts) if parts else "—"


def apply_user_profile(
    core: float, satellite: float, cash: float, profile: dict
) -> tuple[float, float, float]:
    """根据用户风险偏好/投资期限调整信号档位的仓位建议。

    调整逻辑：
      1. risk_tolerance 决定权益整体偏移量（conservative -10%、aggressive +10%）
      2. investment_horizon_years < 5 再额外收紧（-5% 或 -10%）
      3. 按当前 core:satellite 比例拆分偏移量，保持相对结构
      4. 强制满足 max_equity_pct 上限和 min_cash_pct 下限，最后归一化

    Returns:
        (adjusted_core, adjusted_satellite, adjusted_cash) 三者之和为 1.0
    """
    if not profile:
        return core, satellite, cash

    risk = (profile.get("risk_tolerance") or "moderate").lower()
    horizon = float(profile.get("investment_horizon_years") or 10)
    max_equity = float(profile.get("max_equity_pct") or 0.90)
    min_cash = float(profile.get("min_cash_pct") or 0.05)

    equity_shift = {"conservative": -0.10, "moderate": 0.0, "aggressive": 0.10}.get(risk, 0.0)
    if horizon < 3:
        equity_shift -= 0.10
    elif horizon < 5:
        equity_shift -= 0.05

    equity = core + satellite
    equity_new = float(np.clip(equity + equity_shift, 0.0, max_equity))
    ratio = (core / equity) if equity > 0 else 0.6
    adj_core = equity_new * ratio
    adj_sat = equity_new * (1 - ratio)
    adj_cash = max(min_cash, 1.0 - equity_new)

    total = adj_core + adj_sat + adj_cash
    return (
        round(adj_core / total, 3),
        round(adj_sat / total, 3),
        round(adj_cash / total, 3),
    )


def credit_score_from_spread(spread: float) -> float:
    """高收益债利差 → 信用评分（0–10）。"""
    if spread < 3.0:
        return 8.0
    elif spread < 4.0:
        return 6.5
    elif spread < 5.5:
        return 5.0
    elif spread < 8.0:
        return 3.5
    else:
        return 2.0


def trend_score_from_deviation(deviation: float) -> float:
    """SP500 vs 年线偏离幅度 → 趋势评分（0–10）。"""
    if deviation > 0.08:
        return 8.0
    elif deviation > 0.02:
        return 6.5
    elif deviation > -0.02:
        return 5.0
    elif deviation > -0.08:
        return 3.5
    else:
        return 2.0
