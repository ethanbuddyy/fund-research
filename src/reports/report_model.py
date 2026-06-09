"""共享报告模型 + 跨渲染器复用的业务函数（MD/HTML 单一真相源）。

报告层长期的耦合风险：HTML 直接 import report_builder 的私有函数；MD/HTML 各自
维护仓位档位表/阈值；渲染中途读库/读配置/读快照。本模块把「业务含义」——关键结论 /
主要矛盾 / 市场叙事 / 仓位推导 / 区域暴露 / 审查读取 / 换仓变动 / 信号阈值表——收敛为
**单一真相源**，并提供 `ReportModel` + `build_report_model`：渲染器只消费已备好的模型，
不再各自读 IO，也不再跨文件 import 私有函数。CSS 与纯格式化细节留在各自渲染器。

依赖方向（无环）：report_editor → (domain)；report_model → report_editor；
report_builder / html_report_builder → report_model + report_editor。
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd

from ..domain.labels import (
    vix_elevated, vix_neutral, credit_tight, credit_loose, trend_label, trend_strong,
)
from ..domain.scoring import tier_allocation_str
from .report_editor import canonical_triggers, headline_triggers


# ─────────────────────────────────────────────────────────────
# 共享格式化（MD 口径；HTML 另有自己的 _f/_pct 用于 HTML 转义场景）
# ─────────────────────────────────────────────────────────────

def _num(v, fmt: str = ".2f") -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
        if math.isnan(f):
            return "—"
        return format(f, fmt)
    except (TypeError, ValueError):
        return str(v)


def _score(v) -> str:
    """分数格式：保留1位小数，None → '—'。"""
    return _num(v, ".1f")


def _pct(v, decimals: int = 0) -> str:
    """安全格式化百分比，None/NaN → '—'。"""
    if v is None:
        return "—"
    try:
        f = float(v)
        if math.isnan(f):
            return "—"
        fmt = f"{{:+.{decimals}f}}%" if decimals > 0 else f"{{:.0f}}%"
        return fmt.format(f)
    except (TypeError, ValueError):
        return "—"


# ─────────────────────────────────────────────────────────────
# 共享常量
# ─────────────────────────────────────────────────────────────

_SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1}

_VERDICT_LABEL = {
    "sound": "🟢 未发现实质问题",
    "minor_concerns": "🟡 有需注意的小瑕疵",
    "material_concerns": "🔴 存在实质问题，使用前请人工复核",
}

_CATEGORY_CN = {
    "data_contradiction": "与数据矛盾",
    "unsupported_claim": "无依据断言",
    "overstated_conviction": "过度自信",
    "missing_risk": "遗漏风险",
    "internal_inconsistency": "自相矛盾",
}


# ─────────────────────────────────────────────────────────────
# 信号阈值表（单一真相源：档位仓位取自 POSITION_TIERS，MD/HTML 共用）
# ─────────────────────────────────────────────────────────────

# 综合评分区间 → 档位名（区间边界与 domain.scoring.classify_signal 对齐）
_SCORE_RANGE_TO_TIER = [
    ("综合评分 ≥ 7.0", "重仓进取"),
    ("综合评分 5.0–7.0", "标配稳健"),
    ("综合评分 3.0–5.0", "谨慎防守"),
    ("综合评分 < 3.0", "减仓防守"),
]


def signal_threshold_rows() -> list[tuple[str, str]]:
    """返回 [(评分区间, '档位：核心x%/卫星y%/现金z%'), ...]。

    仓位百分比经 tier_allocation_str 从 POSITION_TIERS 取，杜绝 MD/HTML 各自硬编码
    （消除「两处各维护一份仓位档位表」的漂移风险）。
    """
    return [(cond, f"{tier}：{tier_allocation_str(tier)}") for cond, tier in _SCORE_RANGE_TO_TIER]


# ─────────────────────────────────────────────────────────────
# 共享业务函数（原散落于 report_builder，现收敛于此供两渲染器 import）
# ─────────────────────────────────────────────────────────────

def _key_conclusions(signal: Mapping[str, Any], portfolio: Mapping[str, Any]) -> list[str]:
    """从结构化数据生成 3 条关键结论，确保每条都有数据支撑。"""
    conclusions = []
    composite = signal.get("composite_signal", "标配稳健")
    raw = signal.get("timing_score", 5.0) or 5.0
    cape = signal.get("cape")
    vix = signal.get("vix")
    trend = signal.get("trend_score")
    credit = signal.get("credit_score")
    macro_cycle = signal.get("macro_cycle", "")

    # 结论1：估值 vs 趋势主矛盾
    val_level = signal.get("valuation_level", "")
    val_score = (signal.get("valuation") or {}).get("valuation_score", 5)
    trend_score = trend or 5
    if val_score is not None and trend_score is not None:
        val_label = "高估" if float(val_score) < 5 else "合理"
        trend_lbl = trend_label(trend_score)
        cape_str = f"CAPE {_num(cape, '.1f')}" if cape else val_level
        conclusions.append(
            f"估值偏{val_label}（{cape_str}，估值分 {_score(val_score)}/10）与"
            f"{trend_lbl}（趋势分 {_score(trend_score)}/10）并存——"
            f"综合评分 {_num(raw, '.2f')}/10，触发「{composite}」信号。"
        )

    # 结论2：宏观/信用环境
    fed_dir = signal.get("fed_direction", 0.0) or 0.0
    fed_label = "处降息方向" if fed_dir > 0 else "处加息方向" if fed_dir < 0 else "平稳"
    credit_str = f"信用利差分 {_score(credit)}/10" if credit else "信用利差数据缺失"
    conclusions.append(
        f"宏观周期「{macro_cycle}」，利率{fed_label}（方向修正 {_num(fed_dir, '+.1f')} 分）；"
        f"{credit_str}，"
        + ("流动性环境宽松。" if credit_loose(credit) else
           "信用环境偏紧，需关注风险溢价上升。" if credit_tight(credit) else
           "信用环境中性。")
    )

    # 结论3：组合建议
    core_pct = portfolio.get("core_allocation_pct", 60)
    sat_pct = portfolio.get("satellite_allocation_pct", 30)
    cash_pct = portfolio.get("cash_allocation_pct", 10)
    n_core = len(portfolio.get("core_funds", []))
    n_sat = len(portfolio.get("satellite_funds", []))
    vix_str = f"VIX {_num(vix, '.1f')}" if vix else ""
    conclusions.append(
        f"建议持仓：核心 {core_pct:.0f}%（{n_core} 只宽基）+ 卫星 {sat_pct:.0f}%（{n_sat} 只行业/主动）+ 现金 {cash_pct:.0f}%。"
        + (f"情绪面 {vix_str} 处于中性区间，当前仓位合理。" if vix_neutral(vix) else
           f"{vix_str} 偏高，卫星仓位已相应收缩。" if vix_elevated(vix) else "")
    )

    return conclusions[:3]


def primary_contradiction(signal: Mapping[str, Any]) -> str:
    """当前主要矛盾：优先 AI Phase 1，否则规则层四分支推断。"""
    val = signal.get("valuation", {})
    val_score = val.get("valuation_score", 5)
    trend_score = signal.get("trend_score", 5)
    ai_analysis = signal.get("ai_analysis")
    if ai_analysis and ai_analysis.get("primary_contradiction"):
        return ai_analysis["primary_contradiction"]
    val_high = float(val_score or 5) < 5
    is_trend_strong = trend_strong(trend_score if trend_score is not None else 5)
    if val_high and is_trend_strong:
        return f"高估值（CAPE {_num(signal.get('cape'), '.1f')}，估值分 {_score(val_score)}/10）vs 强趋势（趋势分 {_score(trend_score)}/10）——动量暂时压过估值压力"
    elif val_high:
        return f"高估值压力（CAPE {_num(signal.get('cape'), '.1f')}，估值分 {_score(val_score)}/10）与偏弱的趋势信号并存——谨慎防御"
    elif is_trend_strong:
        return f"估值合理（估值分 {_score(val_score)}/10）+ 强趋势（趋势分 {_score(trend_score)}/10）——进攻型信号"
    else:
        return f"估值与趋势均处中性（估值分 {_score(val_score)}/10，趋势分 {_score(trend_score)}/10）——标配均衡"


def market_narrative(signal: Mapping[str, Any]) -> tuple[str, str]:
    """市场叙事文本与来源标注（AI 增强 / 规则层）。"""
    ai_analysis = signal.get("ai_analysis")
    if ai_analysis and ai_analysis.get("market_narrative"):
        return ai_analysis["market_narrative"], "（AI 增强）"
    narrative = signal.get("narrative", {})
    insights = narrative.get("insights", []) if isinstance(narrative, dict) else []
    text = "\n\n".join(insights[:3]) if insights else "（暂无叙事分析）"
    return text, "（规则层）"


def alloc_logic_text(signal: Mapping[str, Any]) -> str:
    """仓位推导逻辑：按综合信号档位给出一句话解释。"""
    composite = signal.get("composite_signal", "标配稳健")
    raw = signal.get("timing_score", 5.0) or 5.0
    return {
        "重仓进取": f"综合评分 {_num(raw, '.2f')}/10 ≥ 7.0，信号积极，风险资产占比提至上限",
        "标配稳健": f"综合评分 {_num(raw, '.2f')}/10 在 5.0–7.0 区间，维持均衡配置",
        "谨慎防守": f"综合评分 {_num(raw, '.2f')}/10 在 3.0–5.0 区间，降低风险敞口，提高现金",
        "减仓防守": f"综合评分 {_num(raw, '.2f')}/10 < 3.0，大幅减仓，保留流动性应对下行风险",
    }.get(composite, f"综合评分 {_num(raw, '.2f')}/10")


def region_exposure(all_funds: list) -> dict[str, list[str]]:
    """按基金名称关键词归类区域暴露（MD/HTML 共用，单一真相源）。
    值为 ``名称(权重%)`` 字符串列表，保持插入顺序。"""
    region_keywords = {
        "美国/北美": ["标普", "S&P", "纳斯达克", "美国", "SP", "US", "America"],
        "全球发达市场": ["全球", "MSCI", "世界", "Global", "QDII"],
        "亚太/新兴市场": ["亚太", "亚洲", "新兴", "中国", "港", "日本", "印度"],
        "行业/主题": ["科技", "医疗", "能源", "消费", "地产", "半导体", "AI"],
    }
    exposure: dict[str, list[str]] = {}
    for f in all_funds:
        name = f.get("fund_name", "")
        matched = False
        for region, keywords in region_keywords.items():
            if any(kw in name for kw in keywords):
                exposure.setdefault(region, []).append(f"{name}({f.get('weight', 0):.0f}%)")
                matched = True
                break
        if not matched:
            exposure.setdefault("其他", []).append(f"{name}({f.get('weight', 0):.0f}%)")
    return exposure


def review_findings(portfolio: Mapping[str, Any]) -> tuple[str, str, list[dict]]:
    """对抗审查的统一读取口（MD/HTML 共用，单一真相源）。

    返回 ``(verdict_key, summary, findings)``；无审查或判级 sound 且无条目时
    findings 为空。findings 按严重度降序，供「渲染前质量门」标注/前置冲突。
    """
    review = portfolio.get("adversarial_review") or {}
    if not review:
        return "", "", []
    verdict = review.get("overall_verdict", "")
    summary = review.get("summary", "") or ""
    findings = list(review.get("findings") or [])
    findings.sort(key=lambda f: _SEVERITY_RANK.get(f.get("severity"), 0), reverse=True)
    return verdict, summary, findings


def _snapshot_change_note(portfolio: Mapping[str, Any]) -> str:
    """较上期换仓变动。

    上期组合取自编排层注入的 ``portfolio["previous_portfolio"]``（内存版上期快照），
    报告层**不再自行读盘**——这样本期快照何时落盘都不影响对比口径，从根上消除
    「报告生成发生在快照覆盖之后→退化为本期与本期比较」的时序耦合。
    """
    raw = portfolio.get("previous_portfolio")
    try:
        if not raw:
            return "_（首次运行，无历史快照可比较）_"
        prev_core = set(raw.get("core", {}).keys())
        prev_sat = set(raw.get("satellite", {}).keys())
        cur_core = {f["fund_code"] for f in portfolio.get("core_funds", [])}
        cur_sat = {f["fund_code"] for f in portfolio.get("satellite_funds", [])}
        added = (cur_core | cur_sat) - (prev_core | prev_sat)
        removed = (prev_core | prev_sat) - (cur_core | cur_sat)
        if not added and not removed:
            return "_本期持仓与上期相同，未发生换仓。_"
        lines = ["**换仓变动：**"]
        if added:
            lines.append(f"- 新增：{', '.join(sorted(added))}")
        if removed:
            lines.append(f"- 移除：{', '.join(sorted(removed))}")
        return "\n".join(lines)
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────
# ReportModel —— 渲染器消费的展示模型
# ─────────────────────────────────────────────────────────────

@dataclass
class ReportModel:
    """两渲染器共用的展示模型：业务含义已备好，渲染器只取值排版。"""
    # 原始输入（仍按需透传给尚未完全模型化的章节）
    signal: Mapping[str, Any]
    portfolio: Mapping[str, Any]
    scores_df: Optional[pd.DataFrame]
    backtest: Optional[Mapping[str, Any]]
    previous_state: Optional[dict]
    # provenance / config（由入口适配器取好传入，渲染过程不再读 IO）
    prov_data: Mapping[str, Any]
    overall_mode: str
    stale_warnings: list[str]
    config: Mapping[str, Any]
    # 预计算的共享业务值（MD/HTML 同源）
    key_conclusions: list[str]
    primary_contradiction: str
    market_narrative: tuple[str, str]
    alloc_logic: str
    canonical_triggers: list[str]
    headline_triggers: list[str]
    rebalance_change: str


def build_report_model(
    signal: Mapping[str, Any],
    portfolio: Mapping[str, Any],
    scores: Optional[pd.DataFrame],
    backtest: Optional[Mapping[str, Any]],
    previous_state: Optional[dict],
    provenance: Mapping[str, Any],
    config: Mapping[str, Any],
) -> ReportModel:
    """组装 ReportModel（纯函数：不读库/不读配置文件/不读快照/不调 AI）。

    `provenance` 由入口适配器备好，形如 {"data": {...}, "overall_mode": str,
    "stale_warnings": [...]}。`previous_state` 为上期组合快照原文（换仓对比用）。
    关键结论 / 触发条件 / 换仓变动等业务值在此一次算好，MD 与 HTML 同源取用。
    """
    return ReportModel(
        signal=signal,
        portfolio=portfolio,
        scores_df=scores,
        backtest=backtest,
        previous_state=previous_state,
        prov_data=provenance.get("data", {}),
        overall_mode=provenance.get("overall_mode", ""),
        stale_warnings=list(provenance.get("stale_warnings", []) or []),
        config=config,
        key_conclusions=_key_conclusions(signal, portfolio),
        primary_contradiction=primary_contradiction(signal),
        market_narrative=market_narrative(signal),
        alloc_logic=alloc_logic_text(signal),
        canonical_triggers=canonical_triggers(signal, portfolio),
        headline_triggers=headline_triggers(signal, portfolio, 1),
        rebalance_change=_snapshot_change_note(portfolio),
    )
