"""核心数据结构的类型契约（TypedDict）。

`signal` / `portfolio` 在旧代码里是跨 8+ 模块传递的无类型巨型 dict，字段全靠
各处 `dict.get("...")` 口头约定——改字段名时 IDE/类型检查器无法发现下游引用。

阶段6 收紧：把「必填核心」与「可选扩展」分层（继承式 TypedDict）：
  * 核心字段（事实 + 决策）必填——构造点漏字段、改名遗漏会被 mypy 当场发现；
  * AI / 叙事 / 审查 / 止损覆盖结果可选（按数据可得性增删，不误报）；
  * **MarketFacts 与 MarketDecision 分层**——事实层是确定性算出的原始指标，
    决策层是据此推出的信号/仓位；止损（apply_stop_loss）只覆盖决策层，
    从类型结构上声明「止损不污染原始事实」（交接单阶段6 要求#1）。
运行时零成本：TypedDict 实例就是普通 dict，行为不变；仍兼容旧 dict 输出。
"""
from __future__ import annotations

from typing import Any, Optional, TypedDict


# ─────────────────────────────────────────────────────────────
# 止损 / 快照 / 基金 行级契约
# ─────────────────────────────────────────────────────────────

class StopLossResult(TypedDict, total=False):
    """portfolio_tracker.update_and_check 的返回 / apply_stop_loss 的输入。

    total=False：基准/各早退路径返回的子集都合法，仅约束键名与类型。
    """
    triggered: bool
    portfolio_nav: float
    high_water_mark: float
    drawdown_pct: float
    threshold_pct: float
    period_return_pct: float
    funds_tracked: int
    note: str
    next_nav_state: dict[str, float]


class PortfolioState(TypedDict):
    """data/portfolio_snapshot.json 的结构（本期推荐快照，止损追踪/换仓门槛共用）。

    core/satellite 为 {fund_code: {score, weight_pct, nav}}。
    """
    date: str
    core: dict[str, dict[str, Any]]
    satellite: dict[str, dict[str, Any]]


class FundScore(TypedDict, total=False):
    """fund_scores 表的行契约（scorer.score_funds 产出、报告/组合消费）。

    DataFrame 记录形态，故 total=False；此处作为「行字段单一真相源」文档与类型参考。
    """
    fund_code: str
    fund_name: str
    total_score: float
    performance_score: float
    risk_score: float
    strategy_score: float
    consistency_score: float
    cost_score: float
    signal: str
    recommendation: str


class PortfolioFund(TypedDict, total=False):
    """组合中单只基金的展示契约（portfolio.select_portfolio 产出，报告层渲染）。"""
    fund_code: str
    fund_name: str
    fund_type: str
    signal: str
    score: float
    performance_score: Optional[float]
    risk_score: Optional[float]
    strategy_score: Optional[float]
    consistency_score: Optional[float]
    cost_score: Optional[float]
    expense_ratio: Optional[float]
    weight: float
    role: str


# ─────────────────────────────────────────────────────────────
# 市场信号：事实层（必填） + 决策层（必填） + 可选扩展
# ─────────────────────────────────────────────────────────────

class MarketFacts(TypedDict):
    """市场原始事实（确定性算出，止损不修改）——必填核心。

    与 src/recommender/signals.py:compute_market_signal 的构造点一一对应；
    回测引擎 `_compute_signal` 是另一套（无前视）口径，不复用本契约。
    """
    date: str
    data_source: str                 # real / partial / mock
    data_quality: dict[str, Any]
    macro_cycle: Optional[str]
    valuation_level: Optional[str]
    sentiment_label: Optional[str]
    cape: Optional[float]
    sp500_pe: Optional[float]
    vix: Optional[float]
    buffett_indicator: Optional[float]
    equity_risk_premium: Optional[float]
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


class MarketDecision(TypedDict):
    """由事实推出的决策——止损（apply_stop_loss）只覆盖这一层，不动事实。"""
    composite_signal: str            # 重仓进取 / 标配稳健 / 谨慎防守 / 减仓防守
    signal_color: str
    core_allocation: float
    satellite_allocation: float
    cash_allocation: float


class MarketSignal(MarketFacts, MarketDecision, total=False):
    """事实层 + 决策层 + 可选扩展（用户画像 / 叙事 / AI / 止损覆盖结果）。"""
    user_profile_applied: bool
    user_profile: Optional[dict[str, Any]]
    narrative: dict[str, Any]
    ai_analysis: Optional[dict[str, Any]]
    # ── 由 update_pipeline 在止损追踪后追加 ──
    stop_loss: Optional[StopLossResult]
    stop_loss_triggered: bool


# ─────────────────────────────────────────────────────────────
# 组合推荐：必填核心 + 可选扩展
# ─────────────────────────────────────────────────────────────

class _PortfolioCore(TypedDict):
    """组合推荐的必填核心（select_portfolio / _empty_portfolio 均产出）。"""
    composite_signal: Optional[str]
    core_allocation_pct: float
    satellite_allocation_pct: float
    cash_allocation_pct: float
    core_funds: list[dict[str, Any]]
    satellite_funds: list[dict[str, Any]]
    total_invested_pct: float
    top_picks: list[dict[str, Any]]
    investment_notes: list[str]


class PortfolioRecommendation(_PortfolioCore, total=False):
    """build_portfolio_recommendation() 的返回结构（核心必填 + 可选扩展）。"""
    score_threshold: float           # 换仓门槛分（报告层「未入选原因」据此说明）
    index_only: bool                 # 最终推荐是否仅允许指数化产品
    allocation_shortfall_pct: float # 因无合格标的而转入现金的目标仓位
    ai_decision: dict[str, Any]      # 仅 AI 阶段二开启且 phase1 成功时存在
    adversarial_review: Optional[dict[str, Any]]  # 仅 Phase3 对抗审查开启且有结果时存在
    # ── 状态所有权（阶段1）：由编排层读入/提交，报告层据此对比，不再各自读盘 ──
    previous_portfolio: Optional[dict[str, Any]]  # 上期快照原文，供报告「换仓变动」对比
    snapshot_payload: PortfolioState              # 本期应提交的快照数据，编排层统一落盘
