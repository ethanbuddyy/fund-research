"""报告渲染前的「编辑 / 质量门」——决策摘要 / 证据 / 审计附录三层之间的去重与统一。

报告层长期的问题不是信息太少，而是**同一套结论被多处重复渲染**（触发条件曾在
首页 / 配置逻辑 / 情景 / 行动计划四处各出现一遍，阈值还不统一）。这里把「何时改变」
的触发条件收归**唯一真相源** `canonical_triggers`，正文各处一律引用它，杜绝结构性重复。

设计原则：
- 只做**去重 / 统一 / 排序 / 截断**，不改任何数字、不调用 LLM（遵守纪律 #2）。
- AI 的 `rebalance_triggers` 若存在则优先（更精细），否则退回规则层 `rule_action_items`。
- 与对抗审查（Phase3）的衔接由 report_builder 的 review_* 系列负责，本模块不重复。
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..domain.labels import trend_strong, TREND_STRONG


def dedupe_keep_order(items: list[str]) -> list[str]:
    """保序去重：去掉完全相同的条目（结构性重复的根因），保留首次出现顺序。"""
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        key = (it or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def canonical_triggers(signal: Mapping[str, Any], portfolio: Mapping[str, Any]) -> list[str]:
    """「何时改变」的唯一触发条件列表（去重、统一格式）。

    优先用 AI Phase2 的 rebalance_triggers（条件→操作）；无 AI 时退回规则层
    rule_action_items。返回的列表是正文「何时改变」一节的**唯一**数据源，
    首页只取其中最关键的 1~2 条，不再各处重复渲染。
    """
    ai = portfolio.get("ai_decision") or {}
    ai_trigs = ai.get("rebalance_triggers") or []
    out: list[str] = []
    for t in ai_trigs:
        cond = (t or {}).get("condition")
        action = (t or {}).get("action")
        if cond and action:
            # 纯文本(无 markdown 粗体)：MD 与 HTML 共用同一份字符串，真正单一真相源
            out.append(f"触发：{cond} → 操作：{action}")

    if not out:
        out = list(rule_action_items(signal, portfolio))

    return dedupe_keep_order(out)


def headline_triggers(signal: Mapping[str, Any], portfolio: Mapping[str, Any],
                      n: int = 2) -> list[str]:
    """首页「本期最重要触发」——取 canonical_triggers 的前 n 条，避免与正文重复整列。"""
    return canonical_triggers(signal, portfolio)[:max(0, n)]


# ─────────────────────────────────────────────────────────────
# 规则层触发条件（无 AI 时的 fallback；曾在 report_builder，现内聚于触发真相源，
# 以消除 report_editor ⇄ report_builder 的反向延迟导入）
# ─────────────────────────────────────────────────────────────

def _trigger_conditions(signal: Mapping[str, Any], portfolio: Mapping[str, Any]) -> list[str]:
    """生成本期最关键的加仓/减仓触发条件（可执行，非空话）。"""
    composite = signal.get("composite_signal", "标配稳健")
    sat_pct = portfolio.get("satellite_allocation_pct", 30)
    cash_pct = portfolio.get("cash_allocation_pct", 10)
    vix = signal.get("vix") or 18
    credit = signal.get("credit_score") or 5.0

    triggers = [
        f"若 VIX 突破 30，立即将卫星仓位降至 {max(10, sat_pct - 15):.0f}%，现金提至 {min(50, cash_pct + 15):.0f}%",
        f"若信用利差评分降至 3.5 以下（对应利差 > 5.5%），执行防守再平衡，现金仓位提至 {min(50, cash_pct + 20):.0f}%",
    ]
    if composite in ("重仓进取", "标配稳健"):
        triggers.append("若综合信号从当前档位降一级（下次更新触发），于次交易日内完成仓位再平衡")
        triggers.append(f"若推荐基金综合评分较当前下降超过 10 分，且备选池中有评分更高替代品，执行换仓")
    else:
        triggers.append("若综合信号升至「标配稳健」或以上，在确认信号稳定两周后逐步补仓至标准权重")
        triggers.append("若持仓基金季度净值回撤超过 15%，评估是否触发止损换仓")
    return triggers


def rule_action_items(signal: Mapping[str, Any], portfolio: Mapping[str, Any]) -> list[str]:
    """规则层行动条目（无 AI 时的 fallback；MD/HTML 共用，单一真相源）。"""
    composite = signal.get("composite_signal", "标配稳健")
    core_pct = portfolio.get("core_allocation_pct", 60)
    trend_score = signal.get("trend_score") or 5.0

    plan_items = _trigger_conditions(signal, portfolio)

    # 额外规则层动作
    if composite == "重仓进取":
        if trend_strong(trend_score):
            plan_items.append(f"趋势分持续 ≥ {TREND_STRONG:g} 且 VIX 保持 < 20，可将核心仓位上限从 {core_pct:.0f}% 提至 {min(80, core_pct+10):.0f}%")
    elif composite in ("谨慎防守", "减仓防守"):
        plan_items.append(f"若 SP500 连续 3 个月回撤超过 10%，考虑分批补仓核心指数 ETF（等权买持）")

    # 换仓门槛
    plan_items.append("若持仓基金综合评分低于 45 且备选池中有 > 55 分候选，于下次月度评分后执行替换")
    plan_items.append("每季度末重新运行评分，若信号档位不变且持仓无重大事件，维持现有组合")
    return plan_items
