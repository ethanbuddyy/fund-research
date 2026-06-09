"""核心数据结构的类型契约（TypedDict）。

`signal` / `portfolio` 在旧代码里是跨 8+ 模块传递的无类型巨型 dict，字段全靠
各处 `dict.get("...")` 口头约定——改字段名时 IDE/类型检查器无法发现下游引用。

这里用 `TypedDict(total=False)` 把字段契约写下来：
  * 运行时零成本——TypedDict 实例就是普通 dict，不改变任何行为；
  * 但开启类型检查（mypy/pyright/IDE）后，拼错 key、漏传字段、改名遗漏的
    下游引用都会被静态发现。

`total=False` 表示所有字段可选（生产链路确实会按数据可得性增删字段，如 AI 阶段
开关关闭时无 `ai_decision`），避免对既有构造点产生误报。
"""
from __future__ import annotations

from typing import Any, Optional, TypedDict


class MarketSignal(TypedDict, total=False):
    """generate_market_signal() 的返回结构（生产链路口径）。

    字段与 src/recommender/signals.py 的构造点一一对应；回测引擎里的
    `_compute_signal` 是另一套（无前视）口径，字段不完全相同，不复用本契约。
    """
    date: str
    data_source: str                 # real / partial / mock
    data_quality: dict[str, Any]
    macro_cycle: Optional[str]
    valuation_level: Optional[str]
    sentiment_label: Optional[str]
    composite_signal: str            # 重仓进取 / 标配稳健 / 谨慎防守 / 减仓防守
    signal_color: str
    cape: Optional[float]
    sp500_pe: Optional[float]
    vix: Optional[float]
    buffett_indicator: Optional[float]
    equity_risk_premium: Optional[float]
    core_allocation: float
    satellite_allocation: float
    cash_allocation: float
    user_profile_applied: bool
    user_profile: Optional[dict[str, Any]]
    timing_score: float
    trend_score: float
    credit_score: float
    global_macro_score: float
    fed_direction: Optional[str]
    macro_adj: float
    macro: dict[str, Any]
    global_macro: dict[str, Any]
    valuation: dict[str, Any]
    sentiment: dict[str, Any]
    narrative: dict[str, Any]
    ai_analysis: Optional[dict[str, Any]]
    # ── 由 update_pipeline 在止损追踪后追加 ──
    stop_loss: Optional[dict[str, Any]]
    stop_loss_triggered: bool


class PortfolioRecommendation(TypedDict, total=False):
    """build_portfolio_recommendation() 的返回结构。"""
    composite_signal: Optional[str]
    core_allocation_pct: float
    satellite_allocation_pct: float
    cash_allocation_pct: float
    core_funds: list[dict[str, Any]]
    satellite_funds: list[dict[str, Any]]
    total_invested_pct: float
    top_picks: list[dict[str, Any]]
    investment_notes: list[str]
    ai_decision: dict[str, Any]      # 仅 AI 阶段二开启且 phase1 成功时存在
    adversarial_review: Optional[dict[str, Any]]  # 仅 Phase3 对抗审查开启且有结果时存在
