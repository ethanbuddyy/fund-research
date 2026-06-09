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
        # 延迟导入避免与 report_builder 形成模块级循环依赖
        from .report_builder import rule_action_items
        out = list(rule_action_items(signal, portfolio))

    return dedupe_keep_order(out)


def headline_triggers(signal: Mapping[str, Any], portfolio: Mapping[str, Any],
                      n: int = 2) -> list[str]:
    """首页「本期最重要触发」——取 canonical_triggers 的前 n 条，避免与正文重复整列。"""
    return canonical_triggers(signal, portfolio)[:max(0, n)]
