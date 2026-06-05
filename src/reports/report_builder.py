"""
QDII 基金投研报告生成器

将市场信号、基金评分、组合推荐、回测结论汇聚成一份 Markdown 格式的
可交付投研报告。所有投资结论均追溯到结构化数据源，不依赖 AI 生成无法核实的内容。

调用方式：
    from src.reports.report_builder import build_report
    path = build_report(signal, portfolio, scores_df=scores_df, backtest=backtest_result)
"""
from __future__ import annotations

import math
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from ..domain.labels import (
    vix_elevated, vix_neutral, credit_tight, credit_loose, trend_label,
    trend_strong, TREND_STRONG,
)
from ..domain.types import MarketSignal, PortfolioRecommendation


# ─────────────────────────────────────────────────────────────
# 对外主入口
# ─────────────────────────────────────────────────────────────

def build_report(
    signal: MarketSignal,
    portfolio: PortfolioRecommendation,
    scores_df: Optional[pd.DataFrame] = None,
    backtest: Optional[dict] = None,
    output_dir: str | Path = "reports",
) -> Path:
    """生成 Markdown 投研报告并写入文件，返回文件路径。"""
    from ..utils import provenance as prov_mod

    date_str = signal.get("date", datetime.now().strftime("%Y-%m-%d"))
    prov_data = signal.get("data_quality") or prov_mod.read_all()
    overall_mode = prov_mod.overall_mode()
    stale_warnings = prov_mod.check_staleness()

    sections: list[str] = []

    sections.append(_s1_conclusion(signal, portfolio, overall_mode))
    sections.append(_s2_data_quality(prov_data, overall_mode, stale_warnings))
    sections.append(_s3_market_theme(signal))
    sections.append(_s4_allocation(signal, portfolio))
    sections.append(_s5_fund_table(portfolio, signal))
    sections.append(_s6_alternates(portfolio, signal))
    sections.append(_s7_exposure_risk(portfolio, signal))
    sections.append(_s8_action_plan(signal, portfolio))
    sections.append(_s9_backtest(backtest, signal))
    sections.append(_s10_appendix(signal, scores_df))
    sections.append(_s11_adversarial_review(portfolio))  # 仅在启用并有审查结果时非空

    content = "\n\n---\n\n".join(s for s in sections if s)

    # 报告文件
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{date_str}_fund_research_report.md"
    out_path.write_text(content, encoding="utf-8")
    return out_path


# ─────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────

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


def _signal_emoji(composite: str) -> str:
    return {
        "重仓进取": "🟢",
        "标配稳健": "🔵",
        "谨慎防守": "🟠",
        "减仓防守": "🔴",
    }.get(composite, "⚪")


def _confidence(signal: dict) -> str:
    """根据数据质量和信号强度估算置信度。"""
    mode = signal.get("data_source", "mock")
    raw = signal.get("timing_score", 5.0) or 5.0
    if mode == "mock":
        return "低（含模拟数据）"
    if mode == "partial":
        return "中（部分真实数据）"
    # real data
    if abs(raw - 5.0) >= 2.0:
        return "高"
    return "中"


def _mock_disclaimer(overall_mode: str) -> str:
    if overall_mode == "mock":
        return (
            "\n> ⚠️ **警告：本报告基于模拟/演示数据生成，所有投资结论仅供系统演示，"
            "不可用于实际投资决策。请先运行完整数据采集流程获取真实数据。**\n"
        )
    if overall_mode == "partial":
        return (
            "\n> ⚠️ **注意：部分数据为估算或近似值，结论仅供参考，请结合真实行情审慎判断。**\n"
        )
    return ""


def _key_conclusions(signal: dict, portfolio: dict) -> list[str]:
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
    fed_label = "降息方向" if fed_dir > 0 else "加息方向" if fed_dir < 0 else "利率平稳"
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


def _trigger_conditions(signal: dict, portfolio: dict) -> list[str]:
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


# ─────────────────────────────────────────────────────────────
# 各章节
# ─────────────────────────────────────────────────────────────

def _s1_conclusion(signal: dict, portfolio: dict, overall_mode: str) -> str:
    composite = signal.get("composite_signal", "标配稳健")
    emoji = _signal_emoji(composite)
    raw = signal.get("timing_score", 5.0) or 5.0
    core_pct = portfolio.get("core_allocation_pct", 60)
    sat_pct = portfolio.get("satellite_allocation_pct", 30)
    cash_pct = portfolio.get("cash_allocation_pct", 10)
    conf = _confidence(signal)
    date_str = signal.get("date", datetime.now().strftime("%Y-%m-%d"))

    disclaimer = _mock_disclaimer(overall_mode)
    conclusions = _key_conclusions(signal, portfolio)
    triggers = _trigger_conditions(signal, portfolio)

    conc_md = "\n".join(f"{i+1}. {c}" for i, c in enumerate(conclusions))
    trig_md = "\n".join(f"- {t}" for t in triggers)

    return f"""# QDII 基金投研报告

**报告日期：** {date_str}　　**置信度：** {conf}　　**数据模式：** {overall_mode}
{disclaimer}
---

## 一、首页结论

### {emoji} 本期综合判断：{composite}

| 指标 | 数值 |
|---|---|
| 综合评分 | {_num(raw, '.2f')} / 10 |
| 建议仓位 | 核心 {core_pct:.0f}% / 卫星 {sat_pct:.0f}% / 现金 {cash_pct:.0f}% |
| 置信度 | {conf} |

### 关键结论

{conc_md}

### 本期最重要触发条件

{trig_md}"""


def _s2_data_quality(prov_data: dict, overall_mode: str, stale_warnings: list[str]) -> str:
    rows = ["## 二、数据可信度", "", _mock_disclaimer(overall_mode).strip()]

    rows.append("\n| 数据源 | 模式 | 行数 | 最后更新 | 说明 |")
    rows.append("|---|---|---|---|---|")
    for src in ["macro", "market", "fund", "valuation", "news"]:
        if src in prov_data:
            info = prov_data[src]
            mode = info.get("mode", "—")
            mode_emoji = {"real": "✅", "partial": "⚠️", "mock": "❌"}.get(mode, "")
            rows.append(
                f"| {src} | {mode_emoji} {mode} | {info.get('rows', '—')} | "
                f"{info.get('updated_at', '—')} | {info.get('detail', '')} |"
            )

    if stale_warnings:
        rows.append("\n### ⚠️ 过期数据警告")
        for w in stale_warnings:
            rows.append(f"- {w}")
    else:
        rows.append("\n✅ 所有数据源均在有效期内。")

    return "\n".join(rows)


def _s3_market_theme(signal: dict) -> str:
    composite = signal.get("composite_signal", "标配稳健")
    raw = signal.get("timing_score", 5.0) or 5.0
    macro = signal.get("macro", {})
    val = signal.get("valuation", {})
    sent = signal.get("sentiment", {})

    macro_score = macro.get("cycle_score", 5)
    val_score = val.get("valuation_score", 5)
    trend_score = signal.get("trend_score", 5)
    credit_score = signal.get("credit_score", 5)
    fed_dir = signal.get("fed_direction", 0.0) or 0.0
    macro_adj = signal.get("macro_adj", macro_score)

    sentiment_score_raw = sent.get("score", 50)
    contrarian = 10 - (sentiment_score_raw or 50) / 10

    # 主要矛盾描述
    ai_analysis = signal.get("ai_analysis")
    if ai_analysis and ai_analysis.get("primary_contradiction"):
        contradiction = ai_analysis["primary_contradiction"]
    else:
        # 规则层推断主矛盾
        val_high = float(val_score or 5) < 5
        is_trend_strong = trend_strong(trend_score if trend_score is not None else 5)
        if val_high and is_trend_strong:
            contradiction = f"高估值（CAPE {_num(signal.get('cape'), '.1f')}，估值分 {_score(val_score)}/10）vs 强趋势（趋势分 {_score(trend_score)}/10）——动量暂时压过估值压力"
        elif val_high:
            contradiction = f"高估值压力（CAPE {_num(signal.get('cape'), '.1f')}，估值分 {_score(val_score)}/10）与偏弱的趋势信号并存——谨慎防御"
        elif is_trend_strong:
            contradiction = f"估值合理（估值分 {_score(val_score)}/10）+ 强趋势（趋势分 {_score(trend_score)}/10）——进攻型信号"
        else:
            contradiction = f"估值与趋势均处中性（估值分 {_score(val_score)}/10，趋势分 {_score(trend_score)}/10）——标配均衡"

    # Narrative（优先用 AI Phase 1，否则用规则层）
    narrative = signal.get("narrative", {})
    if ai_analysis and ai_analysis.get("market_narrative"):
        narrative_text = ai_analysis["market_narrative"]
        narrative_src = "（AI 增强）"
    else:
        insights = narrative.get("insights", []) if isinstance(narrative, dict) else []
        narrative_text = "\n\n".join(insights[:3]) if insights else "（暂无叙事分析）"
        narrative_src = "（规则层）"

    # 仓位推导
    alloc_logic = {
        "重仓进取": f"综合评分 {_num(raw, '.2f')}/10 ≥ 7.0，信号积极，风险资产占比提至上限",
        "标配稳健": f"综合评分 {_num(raw, '.2f')}/10 在 5.0–7.0 区间，维持均衡配置",
        "谨慎防守": f"综合评分 {_num(raw, '.2f')}/10 在 3.0–5.0 区间，降低风险敞口，提高现金",
        "减仓防守": f"综合评分 {_num(raw, '.2f')}/10 < 3.0，大幅减仓，保留流动性应对下行风险",
    }.get(composite, f"综合评分 {_num(raw, '.2f')}/10")

    global_macro = signal.get("global_macro", {})
    gm_section = ""
    if global_macro.get("available") and global_macro.get("regions"):
        regions = global_macro["regions"]
        strongest = global_macro.get("strongest", "")
        weakest = global_macro.get("weakest", "")
        gm_rows = ["", "### 全球宏观区域对比", "", "| 区域 | GDP增长 | 通胀 | 评分 | 状态 |", "|---|---|---|---|---|"]
        for region, data in regions.items():
            tag = " ★" if region == strongest else (" ▼" if region == weakest else "")
            gm_rows.append(
                f"| {region}{tag} | {_num(data.get('gdp_growth'), '.1f')}% | "
                f"{_num(data.get('inflation'), '.1f')}% | {_score(data.get('score'))}/10 | "
                f"{data.get('label', '—')} |"
            )
        gm_section = "\n".join(gm_rows)

    return f"""## 三、市场主线

### 当前主要矛盾

> {contradiction}

### 五因子得分

| 因子 | 得分 | 权重 | 加权贡献 | 说明 |
|---|---|---|---|---|
| 宏观周期（含利率修正） | {_score(macro_adj)}/10 | 20% | {_num(float(macro_adj or 5)*0.20, '.2f')} | 周期"{macro.get('cycle', '—')}"，利率方向 {_num(fed_dir, '+.1f')} 分 |
| 市场估值（CAPE） | {_score(val_score)}/10 | 20% | {_num(float(val_score or 5)*0.20, '.2f')} | CAPE {_num(signal.get('cape'), '.1f')}，水位"{signal.get('valuation_level', '—')}" |
| 逆向情绪 | {_num(contrarian, '.1f')}/10 | 15% | {_num(contrarian*0.15, '.2f')} | VIX {_num(signal.get('vix'), '.1f')}，{sent.get('label', '—')} |
| 价格趋势 | {_score(trend_score)}/10 | 30% | {_num(float(trend_score or 5)*0.30, '.2f')} | SP500 vs 年线偏离 |
| 信用利差 | {_score(credit_score)}/10 | 15% | {_num(float(credit_score or 5)*0.15, '.2f')} | 高收益债利差 BAMLH0A0HYM2 |
| **综合** | **{_num(raw, '.2f')}/10** | 100% | **{_num(raw, '.2f')}** | → **{composite}** |

### 仓位推导逻辑

{alloc_logic}

### 市场叙事 {narrative_src}

{narrative_text}
{gm_section}"""


def _s4_allocation(signal: dict, portfolio: dict) -> str:
    core_pct = portfolio.get("core_allocation_pct", 60)
    sat_pct = portfolio.get("satellite_allocation_pct", 30)
    cash_pct = portfolio.get("cash_allocation_pct", 10)
    composite = portfolio.get("composite_signal", signal.get("composite_signal", "标配稳健"))
    notes = portfolio.get("investment_notes", [])

    # AI Phase 2 组合论点
    ai_decision = portfolio.get("ai_decision", {})
    portfolio_thesis = ai_decision.get("portfolio_thesis", "") if ai_decision else ""

    notes_md = "\n".join(f"- {n}" for n in notes) if notes else "（暂无配置说明）"

    thesis_section = ""
    if portfolio_thesis:
        thesis_section = f"\n### AI 组合论点\n\n{portfolio_thesis}\n"

    # 换仓比较（读取快照）
    snapshot_note = _snapshot_change_note(portfolio)

    scenario_md = ""
    if ai_decision:
        sc = ai_decision.get("scenario_analysis", {})
        if isinstance(sc, dict) and any(sc.values()):
            scenario_md = f"""
### 情景分析

| 情景 | 描述 |
|---|---|
| 牛市情景 | {sc.get('bull_case', '—')} |
| 基准情景 | {sc.get('base_case', '—')} |
| 熊市情景 | {sc.get('bear_case', '—')} |
"""

    return f"""## 四、资产配置建议

| 类别 | 比例 | 说明 |
|---|---|---|
| 核心（宽基指数） | {core_pct:.0f}% | 稳健底仓，低成本被动跟踪 |
| 卫星（行业/主动） | {sat_pct:.0f}% | 增强收益，适度集中敞口 |
| 现金 | {cash_pct:.0f}% | 防守缓冲，等待更优时机 |
| **合计投资比例** | **{core_pct + sat_pct:.0f}%** | 信号：{composite} |

{snapshot_note}

### 配置逻辑

{notes_md}
{thesis_section}{scenario_md}"""


def _snapshot_change_note(portfolio: dict) -> str:
    from pathlib import Path
    import json
    snap_path = Path(__file__).parent.parent.parent / "data" / "portfolio_snapshot.json"
    try:
        if not snap_path.exists():
            return "_（首次运行，无历史快照可比较）_"
        raw = json.loads(snap_path.read_text(encoding="utf-8"))
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


def _fund_row(f: dict, rationale_map: dict) -> str:
    code = str(f.get("fund_code", ""))
    name = f.get("fund_name", code)
    role = f.get("role", "")
    weight = f.get("weight", 0)
    score = f.get("score") or f.get("total_score")
    er = f.get("expense_ratio")
    er_str = f"{float(er)*100:.2f}%" if er is not None else "—"

    rat = rationale_map.get(code, {})
    reason = rat.get("cycle_fit", "—")
    risk = rat.get("risk_note", "—")
    conviction = {"high": "高", "medium": "中", "low": "低"}.get(rat.get("conviction_level", ""), "")

    return (
        f"| {code} | {name} | {role} | {weight:.1f}% | "
        f"{_score(score)} | "
        f"{_score(f.get('performance_score'))} | "
        f"{_score(f.get('risk_score'))} | "
        f"{_score(f.get('strategy_score'))} | "
        f"{_score(f.get('cost_score'))} | "
        f"{_score(f.get('consistency_score'))} | "
        f"{er_str} | "
        f"{f.get('signal', '—')} | "
        f"{conviction} | "
        f"{reason} | "
        f"{risk} |"
    )


def _s5_fund_table(portfolio: dict, signal: dict) -> str:
    core_funds = portfolio.get("core_funds", [])
    sat_funds = portfolio.get("satellite_funds", [])
    all_funds = core_funds + sat_funds

    if not all_funds:
        return "## 五、推荐基金表\n\n_暂无推荐基金（基金数据尚未采集，请先运行数据更新）_"

    # 构建 AI Phase 2 基金理由映射
    ai_decision = portfolio.get("ai_decision", {})
    rationales = (ai_decision or {}).get("fund_rationales", [])
    rationale_map = {r.get("fund_code", ""): r for r in (rationales or [])}

    header = (
        "| 代码 | 基金名称 | 角色 | 权重 | 综合分 | 绩效 | 风险 | 策略 | 费率分 | 一致性 | 管理费 | 信号 | 置信 | 推荐理由 | 主要风险 |"
    )
    sep = "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"

    rows = [header, sep]
    for f in all_funds:
        rows.append(_fund_row(f, rationale_map))

    return "## 五、推荐基金表\n\n" + "\n".join(rows)


def _s6_alternates(portfolio: dict, signal: dict) -> str:
    top_picks = portfolio.get("top_picks", [])
    all_selected = {f["fund_code"] for f in portfolio.get("core_funds", []) + portfolio.get("satellite_funds", [])}
    alternates = [f for f in top_picks if str(f.get("fund_code", "")) not in all_selected][:5]

    if not alternates:
        return "## 六、备选基金\n\n_无额外备选（基金池候选数不足或全部已入选）_"

    composite = signal.get("composite_signal", "标配稳健")
    score_threshold = 10  # 默认门槛

    rows = [
        "## 六、备选基金",
        "",
        f"以下基金综合评分优秀，但未入选本期组合（换仓门槛 {score_threshold} 分，或角色已由更高分基金占据）：",
        "",
        "| 代码 | 基金名称 | 综合分 | 绩效 | 风险 | 策略 | 费率分 | 备注 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for f in alternates:
        code = str(f.get("fund_code", ""))
        name = f.get("fund_name", code)
        score = f.get("total_score") or f.get("score")
        note = "角色重叠（宽基已满3席）" if "标普" in name or "S&P" in name or "全球" in name else "策略匹配稍低或换仓门槛未达"
        rows.append(
            f"| {code} | {name} | {_score(score)} | "
            f"{_score(f.get('performance_score'))} | "
            f"{_score(f.get('risk_score'))} | "
            f"{_score(f.get('strategy_score'))} | "
            f"{_score(f.get('cost_score'))} | "
            f"{note} |"
        )

    return "\n".join(rows)


def _s7_exposure_risk(portfolio: dict, signal: dict) -> str:
    core_funds = portfolio.get("core_funds", [])
    sat_funds = portfolio.get("satellite_funds", [])
    all_funds = core_funds + sat_funds

    composite = signal.get("composite_signal", "标配稳健")
    vix = signal.get("vix")
    credit_score = signal.get("credit_score") or 5

    # 费率统计
    ers = [float(f["expense_ratio"]) for f in all_funds if f.get("expense_ratio") is not None]
    avg_er = sum(ers) / len(ers) if ers else None

    # 区域和类型暴露（从基金名称简单推断）
    region_keywords = {
        "美国/北美": ["标普", "S&P", "纳斯达克", "美国", "SP", "US", "America"],
        "全球发达市场": ["全球", "MSCI", "世界", "Global", "QDII"],
        "亚太/新兴市场": ["亚太", "亚洲", "新兴", "中国", "港", "日本", "印度"],
        "行业/主题": ["科技", "医疗", "能源", "消费", "地产", "半导体", "AI"],
    }
    region_exposure: dict[str, list[str]] = {}
    for f in all_funds:
        name = f.get("fund_name", "")
        matched = False
        for region, keywords in region_keywords.items():
            if any(kw in name for kw in keywords):
                region_exposure.setdefault(region, []).append(f"{name}({f.get('weight', 0):.0f}%)")
                matched = True
                break
        if not matched:
            region_exposure.setdefault("其他", []).append(f"{name}({f.get('weight', 0):.0f}%)")

    region_rows = []
    for region, items in region_exposure.items():
        region_rows.append(f"| {region} | {', '.join(items)} |")

    concentration = len(all_funds)
    conc_note = (
        "集中度适中" if 3 <= concentration <= 6
        else "集中度偏高，建议适当分散" if concentration < 3
        else "持仓较分散，跟踪成本上升"
    )

    qdii_risks = [
        "**汇率风险**：QDII 资产以外币计价，人民币汇率波动直接影响净值，近期汇率走势需关注",
        "**溢价/折价**：场内 ETF 型 QDII 可能出现较大溢价（换汇受限时尤甚），避免高溢价时买入",
        "**限购风险**：部分 QDII 在额度紧张时暂停大额申购，建议提前确认可购状态",
        "**申赎成本**：开放式 QDII 申购费 0.6–1.5%，赎回费 0.5%，频繁操作显著侵蚀收益",
        "**流动性风险**：规模较小的 QDII 日均成交量低，大额买卖可能影响价格",
    ]
    if vix_elevated(vix):
        qdii_risks.insert(0, f"**⚠️ 当前 VIX {_num(vix, '.1f')} 偏高**：市场波动加剧，场内 ETF 溢价可能快速扩大，操作需谨慎")
    if credit_tight(credit_score):
        qdii_risks.insert(0, "**⚠️ 信用利差偏高**：全球信用环境趋紧，高收益债 QDII 需特别警惕流动性冲击")

    region_table = "\n".join(["| 区域 | 基金 |", "|---|---|"] + region_rows) if region_rows else "_无持仓数据_"
    qdii_risks_md = "\n".join(f"- {r}" for r in qdii_risks)
    er_str = f"{avg_er*100:.2f}%" if avg_er is not None else "—"

    return f"""## 七、组合暴露与风险

### 区域暴露

{region_table}

### 组合特征

| 项目 | 数值 |
|---|---|
| 持仓基金数 | {concentration} 只（{conc_note}） |
| 加权平均管理费 | {er_str} |
| 信号强度 | {composite} |

### QDII 特有风险

{qdii_risks_md}"""


def _s8_action_plan(signal: dict, portfolio: dict) -> str:
    composite = signal.get("composite_signal", "标配稳健")
    vix = signal.get("vix") or 18
    credit_score = signal.get("credit_score") or 5.0
    sat_pct = portfolio.get("satellite_allocation_pct", 30)
    cash_pct = portfolio.get("cash_allocation_pct", 10)
    core_pct = portfolio.get("core_allocation_pct", 60)
    trend_score = signal.get("trend_score") or 5.0
    raw = signal.get("timing_score") or 5.0

    # AI Phase 2 中的仓位管理建议（最优先）
    ai_decision = portfolio.get("ai_decision", {})
    ai_notes = (ai_decision or {}).get("position_sizing_notes", [])
    ai_triggers = (ai_decision or {}).get("rebalance_triggers", [])

    if ai_notes or ai_triggers:
        # 用 AI 生成的可执行条目
        items = []
        for note in ai_notes:
            items.append(f"- {note}")
        for trig in ai_triggers:
            cond = trig.get("condition", "")
            action = trig.get("action", "")
            if cond and action:
                items.append(f"- **触发条件**：{cond} → **操作**：{action}")
        plan_md = "\n".join(items)
        src_note = "_（以上条目由 AI Phase 2 生成，基于当期市场量化数据）_"
    else:
        # 规则层生成
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

        plan_md = "\n".join(f"- {item}" for item in plan_items)
        src_note = "_（以上条目由规则层生成；开启 AI 分析后将提供更精细的操作建议）_"

    return f"""## 八、行动计划

{plan_md}

{src_note}

> 注：所有操作条目均基于当期量化信号（综合评分 {_num(raw, '.2f')}/10，{composite}）制定，
> 下次数据更新后应重新评估触发状态。"""


def _s9_backtest(backtest: Optional[dict], signal: dict) -> str:
    if backtest is None:
        return (
            "## 九、回测与策略验证\n\n"
            "_本次运行未执行回测（回测耗时较长，默认跳过）。_\n\n"
            "_如需回测验证，请单独运行：`python backtest.py`_"
        )

    if "error" in backtest:
        return f"## 九、回测与策略验证\n\n> ⚠️ 回测失败：{backtest['error']}"

    sm = backtest.get("strat_metrics", {})
    ewbh = backtest.get("ewbh_metrics", {})
    spm = backtest.get("sp500_metrics", {})
    b6040 = backtest.get("b6040_metrics", {})
    df = backtest.get("df")
    sig_stats = backtest.get("signal_stats")
    ds = backtest.get("data_source", "unknown")
    ds_label = {"real": "✅ 真实数据", "partial": "⚠️ 部分真实/近似", "mock": "❌ 含模拟数据(仅演示)"}.get(ds, ds)
    surv = backtest.get("survivorship_note", "")

    start = backtest.get("start_date", "—")
    end = backtest.get("end_date", "—")
    n_periods = backtest.get("n_periods", "—")

    alpha_ewbh = (sm.get("annualized_return", 0) or 0) - (ewbh.get("annualized_return", 0) or 0)
    alpha_sp500 = (sm.get("annualized_return", 0) or 0) - (spm.get("annualized_return", 0) or 0)

    perf_table = f"""### 绩效对比（{start} ～ {end}，{n_periods} 个调仓周期）

| 指标 | 本策略 | 等权买持 | 标普500 | 60/40 |
|---|---|---|---|---|
| 累计收益 | {_pct(sm.get('total_return'), 2)} | {_pct(ewbh.get('total_return'), 2)} | {_pct(spm.get('total_return'), 2)} | {_pct(b6040.get('total_return'), 2)} |
| 年化收益 | {_pct(sm.get('annualized_return'), 2)} | {_pct(ewbh.get('annualized_return'), 2)} | {_pct(spm.get('annualized_return'), 2)} | {_pct(b6040.get('annualized_return'), 2)} |
| 夏普比率 | {_num(sm.get('sharpe_ratio'), '.3f')} | {_num(ewbh.get('sharpe_ratio'), '.3f')} | {_num(spm.get('sharpe_ratio'), '.3f')} | {_num(b6040.get('sharpe_ratio'), '.3f')} |
| 最大回撤 | {_pct(sm.get('max_drawdown'), 2)} | {_pct(ewbh.get('max_drawdown'), 2)} | {_pct(spm.get('max_drawdown'), 2)} | {_pct(b6040.get('max_drawdown'), 2)} |
| 年化波动率 | {_pct(sm.get('volatility'), 2)} | {_pct(ewbh.get('volatility'), 2)} | {_pct(spm.get('volatility'), 2)} | {_pct(b6040.get('volatility'), 2)} |
| 月度胜率 | {_pct(sm.get('win_rate'), 1)} | {_pct(ewbh.get('win_rate'), 1)} | {_pct(spm.get('win_rate'), 1)} | {_pct(b6040.get('win_rate'), 1)} |

**超额收益 vs 等权买持：{_pct(alpha_ewbh, 2)}/年**（衡量择时+选基综合贡献）

**超额收益 vs 标普500：{_pct(alpha_sp500, 2)}/年**"""

    # 信号有效性
    sig_md = ""
    if sig_stats is not None and not (hasattr(sig_stats, "empty") and sig_stats.empty):
        sig_rows = ["### 信号有效性验证", "", "| 信号 | 出现次数 | SP500次月均收益 | 有效性 |", "|---|---|---|---|"]
        try:
            for _, row in sig_stats.iterrows():
                s = str(row.get("信号", ""))
                n = row.get("出现次数", "—")
                sp_r = row.get("SP500次月均收益%", None)
                if s == "重仓进取":
                    ok = "✓ 有效" if sp_r and float(sp_r) > 1.5 else "△ 弱" if sp_r and float(sp_r) > 0 else "✗ 失效"
                elif s in ("谨慎防守", "减仓防守"):
                    ok = "✓ 有效" if sp_r and float(sp_r) < 0.5 else "△ 弱"
                else:
                    ok = "—"
                sig_rows.append(f"| {s} | {n} | {_pct(sp_r, 2)} | {ok} |")
        except Exception:
            sig_rows.append("| 数据解析失败 | — | — | — |")
        sig_md = "\n".join(sig_rows)

    # 年度收益
    annual_md = ""
    if df is not None and not (hasattr(df, "empty") and df.empty):
        try:
            df_copy = df[["strat_return", "sp500_return"]].copy()
            df_copy.index = pd.to_datetime(df_copy.index)
            annual = df_copy.resample("YE").apply(lambda x: (1 + x).prod() - 1) * 100
            rows = ["### 年度收益拆解", "", "| 年份 | 策略 | 标普500 | 差值 |", "|---|---|---|---|"]
            for year, row in annual.iterrows():
                yr = year.year
                strat_r = float(row.get("strat_return", 0))
                sp500_r = float(row.get("sp500_return", 0))
                diff = strat_r - sp500_r
                sign = "▲" if diff >= 0 else "▼"
                rows.append(f"| {yr} | {_pct(strat_r, 1)} | {_pct(sp500_r, 1)} | {sign} {_pct(abs(diff), 1)} |")
            annual_md = "\n".join(rows)
        except Exception:
            annual_md = ""

    surv_note = f"\n> ⚠️ **幸存者偏差**：{surv}" if surv else ""

    # ── 幸存者偏差修正对照组 ──────────────────────────────────────
    surv_corr_md = ""
    corrected = backtest.get("corrected_strat_metrics")
    surv_stats = backtest.get("survivorship_stats", {})
    if corrected:
        bias = (sm.get("annualized_return", 0) or 0) - (corrected.get("annualized_return", 0) or 0)
        avg_premature = surv_stats.get("avg_premature_per_period", 0)
        surv_corr_md = f"""### 幸存者偏差修正对照

> 修正方法：在每个调仓日仅允许使用**成立日期 ≤ 调仓日**的基金参与评分选股，
> 排除当时尚未成立但事后出现在基金池中的基金（平均每期剔除 {avg_premature:.1f} 只）。

| 指标 | 原始策略 | 幸存者修正策略 | 偏差溢价 |
|---|---|---|---|
| 年化收益 | {_pct(sm.get('annualized_return'), 2)} | {_pct(corrected.get('annualized_return'), 2)} | {_pct(bias, 2)}/年 |
| 夏普比率 | {_num(sm.get('sharpe_ratio'), '.3f')} | {_num(corrected.get('sharpe_ratio'), '.3f')} | — |
| 最大回撤 | {_pct(sm.get('max_drawdown'), 2)} | {_pct(corrected.get('max_drawdown'), 2)} | — |

> 偏差溢价 > 0 表示原始回测因纳入「事后才成立的优质基金」而高估了策略收益。
> 修正后结果更贴近真实可交易环境的历史表现。"""

    # ── 因子归因分析（如随 run.py --backtest 一同返回则展示）──────
    factor_attr_md = ""
    attr = backtest.get("factor_attribution")
    if attr and "factors" in attr:
        base_ann = attr.get("base_annual_return", 0)
        rows = [
            "### 因子归因分析（逐因子屏蔽实验）",
            "",
            f"> 基准策略（6因子全开）年化收益：**{_pct(base_ann, 2)}**",
            "> 贡献 = 基准年化 − 屏蔽后年化（正值：该因子有益；负值：该因子拖累）",
            "",
            "| 因子 | 原权重 | 屏蔽后年化 | 边际贡献 | 评级 |",
            "|---|---|---|---|---|",
        ]
        for fname, info in sorted(
            attr["factors"].items(), key=lambda x: -x[1]["contribution_pct"]
        ):
            rows.append(
                f"| {info['label']} "
                f"| {info['base_weight']*100:.1f}% "
                f"| {_pct(info['ablated_annual'], 2)} "
                f"| {_pct(info['contribution_pct'], 2)} "
                f"| {info['contribution_label']} |"
            )
        factor_attr_md = "\n".join(rows)

    return f"""## 九、回测与策略验证

**数据来源：** {ds_label}　　**回测周期：** {start} ～ {end}
{surv_note}

{perf_table}

{surv_corr_md}

{sig_md}

{annual_md}

{factor_attr_md}

> 回测结论仅供参考，不构成投资建议。历史绩效不代表未来表现。"""


_VERDICT_LABEL = {
    "sound": "🟢 未发现实质问题",
    "minor_concerns": "🟡 有需注意的小瑕疵",
    "material_concerns": "🔴 存在实质问题，使用前请人工复核",
}
_SEVERITY_EMOJI = {"high": "🔴", "medium": "🟡", "low": "⚪"}
_CATEGORY_CN = {
    "data_contradiction": "与数据矛盾",
    "unsupported_claim": "无依据断言",
    "overstated_conviction": "过度自信",
    "missing_risk": "遗漏风险",
    "internal_inconsistency": "自相矛盾",
}


def _s11_adversarial_review(portfolio: dict) -> str:
    """AI 对抗审查结论（仅当启用并产出审查结果时渲染，否则返回空串）。"""
    review = portfolio.get("adversarial_review")
    if not review:
        return ""
    verdict = _VERDICT_LABEL.get(review.get("overall_verdict"), review.get("overall_verdict", "—"))
    conf = {"high": "高", "medium": "中", "low": "低"}.get(review.get("confidence"), "—")
    findings = review.get("findings") or []

    head = (
        f"## AI 对抗审查\n\n"
        f"> 由独立的「挑错」子智能体复核 AI 投资决策，专抓与数据矛盾 / 无依据 / "
        f"过度自信 / 遗漏风险 / 自相矛盾。此为可靠性防线，非二次背书。\n\n"
        f"- **审查结论**：{verdict}（审查置信度：{conf}）\n"
    )
    if review.get("summary"):
        head += f"- **小结**：{review['summary']}\n"

    if not findings:
        return head + "\n_未提出具体问题。_"

    rows = ["", "| 严重度 | 类别 | 被质疑的主张 | 问题 | 建议修正 |", "|---|---|---|---|---|"]
    for f in findings:
        sev = _SEVERITY_EMOJI.get(f.get("severity"), "") + (f.get("severity") or "")
        cat = _CATEGORY_CN.get(f.get("category"), f.get("category", "—"))
        rows.append(
            f"| {sev} | {cat} | {(f.get('claim') or '')[:50]} "
            f"| {(f.get('issue') or '')[:80]} | {(f.get('suggested_fix') or '')[:60]} |"
        )
    return head + "\n".join(rows)


def _s10_appendix(signal: dict, scores_df: Optional[pd.DataFrame]) -> str:
    from ..utils.config import load_config
    cfg = load_config()
    weights = cfg.get("scoring_weights", {})
    vp = cfg.get("strategy_params", {}).get("valuation_thresholds", {})

    # 原始指标
    macro = signal.get("macro", {})
    val = signal.get("valuation", {})
    sent = signal.get("sentiment", {})

    raw_rows = [
        f"| Shiller CAPE | {_num(signal.get('cape'), '.2f')} |",
        f"| 标普500 P/E | {_num(signal.get('sp500_pe'), '.1f')} |",
        f"| VIX | {_num(signal.get('vix'), '.1f')} |",
        f"| 巴菲特指标（总市值/GDP） | {_num(val.get('buffett_indicator'), '.2f')} |",
        f"| 股权风险溢价 ERP | {_num(val.get('equity_risk_premium'), '.2f')}% |",
        f"| 联邦基金利率 | {_num(macro.get('fed_rate'), '.2f')}% |",
        f"| 失业率 | {_num(macro.get('unemployment'), '.2f')}% |",
        f"| GDP 增速(YoY) | {_num(macro.get('gdp_growth'), '.2f')}% |",
        f"| 期限利差(10Y-2Y) | {_num(macro.get('yield_curve'), '.2f')}% |",
        f"| 综合评分 | {_num(signal.get('timing_score'), '.3f')}/10 |",
    ]

    # 评分权重
    weight_rows = [
        f"| 业绩（绩效） | {weights.get('performance', 0.30)*100:.0f}% |",
        f"| 风险调整（夏普+回撤+波动） | {weights.get('risk_adjusted', 0.25)*100:.0f}% |",
        f"| 策略匹配（信号适配） | {weights.get('strategy_match', 0.20)*100:.0f}% |",
        f"| 费率效率 | {weights.get('cost_efficiency', 0.15)*100:.0f}% |",
        f"| 跨期一致性 | {weights.get('consistency', 0.10)*100:.0f}% |",
    ]

    # 信号阈值
    threshold_rows = [
        "| 综合评分 ≥ 7.0 | 重仓进取：核心70%/卫星25%/现金5% |",
        "| 综合评分 5.0–7.0 | 标配稳健：核心60%/卫星30%/现金10% |",
        "| 综合评分 3.0–5.0 | 谨慎防守：核心50%/卫星20%/现金30% |",
        "| 综合评分 < 3.0 | 减仓防守：核心35%/卫星15%/现金50% |",
        f"| CAPE 高估线 | {vp.get('cape_overvalued', 30)} |",
        f"| CAPE 低估线 | {vp.get('cape_undervalued', 15)} |",
    ]

    raw_table = "\n".join(["| 指标 | 当期值 |", "|---|---|"] + raw_rows)
    weight_table = "\n".join(["| 维度 | 权重 |", "|---|---|"] + weight_rows)
    threshold_table = "\n".join(["| 条件 | 信号/操作 |", "|---|---|"] + threshold_rows)

    # 数据源
    data_sources = [
        "| 宏观数据 | FRED API（GDP、PCE、FEDFUNDS、UNRATE、BAMLH0A0HYM2 等）|",
        "| 市场数据 | yfinance（^GSPC、^VIX、SP500 历史）|",
        "| 估值数据 | multpl.com CAPE / FRED |",
        "| 全球宏观 | World Bank / OECD |",
        "| 基金数据 | akshare / 天天基金 pingzhongdata |",
        "| 新闻情绪 | Alpha Vantage NEWS_SENTIMENT / Finnhub（含 fallback）|",
    ]
    ds_table = "\n".join(["| 类型 | 来源 |", "|---|---|"] + data_sources)

    return f"""## 十、附录

### 数据源

{ds_table}

### 基金评分权重

{weight_table}

### 综合信号阈值

{threshold_table}

### 当期关键原始指标

{raw_table}

---

_报告由 QDII 基金投研系统自动生成。所有量化结论均可追溯至上述数据源和算法。_
_本报告不构成投资建议，投资者应结合自身风险承受能力独立判断。_"""


# ─────────────────────────────────────────────────────────────
# 单基金综合研判报告
# ─────────────────────────────────────────────────────────────

def build_fund_report(
    analysis_result: dict,
    output_dir: str | Path = "reports",
) -> Path:
    """将 analyze_fund() 的结构化结果生成 Markdown 研判报告，返回文件路径。"""
    fund_code = analysis_result.get("fund_code", "unknown")
    date_str  = datetime.now().strftime("%Y-%m-%d")
    content   = _fund_report_content(analysis_result, date_str)
    out_dir   = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path  = out_dir / f"{date_str}_{fund_code}_analysis.md"
    out_path.write_text(content, encoding="utf-8")
    return out_path


def _load_fund_extra(fund_code: str) -> dict:
    """加载单基金的扩展数据：逐年收益、经理详情、申购/赎回费、换手率、费率分拆。"""
    from ..utils.database import read_table, get_connection
    extra: dict = {
        "year_returns": {},
        "managers": [],
        "purchase_fees": [],
        "redemption_fees": [],
        "turnover": {},
        "mgmt_fee": None,
        "custody_fee": None,
    }

    # 逐年收益
    try:
        yr_df = read_table("fund_year_returns", "fund_code = ? ORDER BY year", (fund_code,))
        if not yr_df.empty:
            extra["year_returns"] = {
                str(int(row["year"])): float(row["return_pct"])
                for _, row in yr_df.iterrows()
                if row["return_pct"] is not None
            }
    except Exception:
        pass

    # 基金经理详情
    try:
        mgr_df = read_table("fund_manager", "fund_code = ?", (fund_code,))
        if not mgr_df.empty:
            extra["managers"] = mgr_df.to_dict("records")
    except Exception:
        pass

    # 申购/赎回费
    try:
        fee_df = read_table("fund_fees", "fund_code = ?", (fund_code,))
        if not fee_df.empty:
            extra["purchase_fees"]   = fee_df[fee_df["fee_type"] == "purchase"].to_dict("records")
            extra["redemption_fees"] = fee_df[fee_df["fee_type"] == "redemption"].to_dict("records")
    except Exception:
        pass

    # 换手率
    try:
        turn_df = read_table("fund_turnover",
                             "fund_code = ? AND turnover_rate IS NOT NULL ORDER BY year",
                             (fund_code,))
        if not turn_df.empty:
            extra["turnover"] = {
                str(int(row["year"])): float(row["turnover_rate"])
                for _, row in turn_df.iterrows()
            }
    except Exception:
        pass

    # 管理费/托管费分拆
    try:
        fl_df = read_table("fund_list", "fund_code = ?", (fund_code,))
        if not fl_df.empty:
            r = fl_df.iloc[0]
            mf = r.get("mgmt_fee")
            cf = r.get("custody_fee")
            extra["mgmt_fee"]    = float(mf)    if mf    is not None and mf    == mf    else None
            extra["custody_fee"] = float(cf)    if cf    is not None and cf    == cf    else None
    except Exception:
        pass

    return extra


# 子项数据覆盖度图标（COMPUTED=实算 / PROXY=代理 / UNAVAILABLE=缺失）
_COV = {"COMPUTED": "✅", "PROXY": "~", "UNAVAILABLE": "⚠️"}

_GRADE_LABEL = {
    "优质候选": "🟢 优质候选", "合格候选": "🔵 合格候选",
    "有明显短板": "🟡 有明显短板", "不建议配置": "🟠 不建议配置", "剔除": "🔴 剔除",
}


def _fmt_num(v, d: int = 2) -> str:
    """NaN 安全的定点格式化：None/NaN → 「—」，否则保留 d 位小数。"""
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v:.{d}f}"


def _dimension_detail_table(scores: dict, key: str) -> str:
    """单个评分维度的子项明细表（从 _fund_report_content 提取为模块级，便于阅读/测试）。"""
    s = scores.get(key, {})
    rows = ["| 子项 | 得分 | 满分 | 覆盖 | 备注 |", "|---|---:|---:|---|---|"]
    for sub, d in (s.get("details") or {}).items():
        sc  = d.get("score", "—")
        mx  = d.get("max", "—")
        cov = _COV.get(d.get("coverage", "?"), "?")
        note_parts = []
        for k, v in d.items():
            if k in ("score", "max", "coverage", "note"):
                continue
            note_parts.append(f"{k}={_fmt_num(v) if isinstance(v, float) else v}")
        note = d.get("note") or ("; ".join(note_parts[:3]))
        rows.append(f"| {sub} | {sc:.1f} | {mx} | {cov} | {note[:60]} |")
    return "\n".join(rows)


def _fee_table(fee_rows: list, title: str) -> str:
    """申购/赎回费率表（从 _fund_report_content 提取为模块级）。"""
    if not fee_rows:
        return f"**{title}**：暂无数据\n"
    lines = [f"**{title}**", "", "| 条件 | 费率 |", "|---|---:|"]
    for r in fee_rows:
        desc = r.get("rate_desc") or "—"
        rate = r.get("rate")
        rate_str = f"{rate*100:.2f}%" if rate is not None else "—"
        lines.append(f"| {desc} | {rate_str} |")
    return "\n".join(lines)


def _fund_report_content(result: dict, date_str: str) -> str:
    info   = result["fund_info"]
    perf   = result["performance"]
    adv    = result["advanced_metrics"]
    scores = result["scores"]
    vetoes = result["vetoes"]
    concl  = result["conclusion"]
    hold   = result["holdings"]
    peer   = result["peer_context"]
    extra  = _load_fund_extra(result["fund_code"])

    name    = info.get("fund_name", result["fund_code"])
    grade   = _GRADE_LABEL.get(concl["grade"], concl["grade"])
    total   = scores["total"]
    sig_fit = concl.get("fit_signal") or {}

    _f = _fmt_num

    # ── 评分表 ─────────────────────────────────────────────────
    dim_names = [
        ("performance", "业绩质量",  20),
        ("risk",        "风险控制",  20),
        ("manager",     "基金经理",  15),
        ("strategy",    "策略稳定",  15),
        ("attribution", "收益归因",  10),
        ("structure",   "规模流动",  10),
        ("cost",        "费用成本",  10),
    ]
    score_rows = ["| 维度 | 得分 | 满分 | 数据覆盖 |", "|---|---:|---:|---|"]
    for key, label, max_s in dim_names:
        s = scores.get(key, {})
        raw = s.get("score", 0)
        covers = {d.get("coverage", "?") for d in (s.get("details") or {}).values()}
        cov_str = "✅ 全量计算" if covers <= {"COMPUTED"} else "⚠️ 部分代理" if "UNAVAILABLE" in covers else "~ 代理"
        score_rows.append(f"| {label} | **{raw:.1f}** | {max_s} | {cov_str} |")
    score_rows.append(f"| **合计** | **{total:.1f}** | **100** | |")
    score_table = "\n".join(score_rows)

    # 详细子项表：_dimension_detail_table(scores, key)（已提为模块级）
    def _detail_table(key: str) -> str:
        return _dimension_detail_table(scores, key)

    # ── 同类对比表 ─────────────────────────────────────────────
    peer_rows = ["| 指标 | 本基金 | 同类中位数 | 同类均值 |", "|---|---:|---:|---:|"]
    ps = peer.get("stats", {})
    metrics_cn = {
        "return_3y": ("近3年累计收益%", perf.get("return_3y")),
        "annualized_return": ("年化收益%", perf.get("annualized_return")),
        "sharpe_ratio": ("夏普比率", perf.get("sharpe_ratio")),
        "max_drawdown": ("最大回撤%", perf.get("max_drawdown")),
        "volatility": ("波动率%", perf.get("volatility")),
    }
    for col, (label, val) in metrics_cn.items():
        p = ps.get(col, {})
        peer_rows.append(
            f"| {label} | {_f(val)} | {_f(p.get('median'))} | {_f(p.get('mean'))} |"
        )
    peer_table = "\n".join(peer_rows)

    # ── 一票否决 ───────────────────────────────────────────────
    veto_md = "**无一票否决触发** ✅" if not vetoes else "\n".join(
        f"- {'🚨' if v['severity']=='hard' else '⚠️'} **[{v['id']}] {v['condition']}**：{v['detail']}"
        for v in vetoes
    )

    # ── 持仓穿透 ───────────────────────────────────────────────
    if hold:
        holding_md = (
            f"- 数据日期：{hold.get('date', '—')}\n"
            f"- 股票比例：{_f(hold.get('stock_ratio'))}%　债券：{_f(hold.get('bond_ratio'))}%　现金：{_f(hold.get('cash_ratio'))}%\n"
        )
        codes = hold.get("stock_codes", "")
        if codes:
            code_list = [c.strip() for c in str(codes).split(",") if c.strip()]
            holding_md += f"- 持仓股票：{', '.join(code_list[:8])}（共 {len(code_list)} 只）\n"
    else:
        holding_md = "_持仓数据暂缺，建议运行 `python run.py` 更新_"

    # ── 信号适配 ───────────────────────────────────────────────
    signal_md = ""
    if sig_fit:
        signal_md = (
            f"\n### 当前市场信号适配\n\n"
            f"- 市场信号：{sig_fit.get('composite_signal', '—')}\n"
            f"- 策略匹配分：{sig_fit.get('strategy_match_score', 0):.1f} / 10\n"
            f"- 评估：**{sig_fit.get('assessment', '—')}**\n"
        )

    _inception_raw = info.get("inception_date")
    inception = (
        "—" if _inception_raw is None
        else "—" if str(_inception_raw).lower() in ("nan", "none", "")
        else str(_inception_raw)
    )
    tenure_raw = info.get("tenure_years")
    tenure = None if (tenure_raw is None or str(tenure_raw).lower() == "nan") else tenure_raw
    aum = info.get("total_assets")
    er = info.get("expense_ratio")
    mgmt_fee    = extra.get("mgmt_fee")    or info.get("mgmt_fee")
    custody_fee = extra.get("custody_fee") or info.get("custody_fee")

    # ── 地区宏观机会 ───────────────────────────────────────────
    _LABEL_EMOJI = {"强势": "🟢", "偏强": "🔵", "中性": "🟡", "偏弱": "🟠", "弱势": "🔴"}
    ro = result.get("region_outlook")
    if ro and ro.get("covered_regions"):
        cov = ro["covered_regions"]
        ranking = ro.get("ranking", list(cov.keys()))
        ro_rows = ["| 地区 | 综合 | 宏观 | 动量 | 相对 | 标签 | GDP% | 通胀% | 近1年 | vs美国3年 |",
                   "|---|---:|---:|---:|---:|---|---:|---:|---:|---:|"]
        for rk in ranking:
            d = cov.get(rk)
            if not d:
                continue
            emoji   = _LABEL_EMOJI.get(d["label"], "⚪")
            gdp_c   = f"{d['gdp_growth']:+.1f}"  if d.get("gdp_growth") is not None else "—"
            infl_c  = f"{d['inflation']:+.1f}"   if d.get("inflation")  is not None else "—"
            r1_c    = f"{d['return_1y']:+.1f}%"  if d.get("return_1y") is not None else "—"
            vs3_c   = f"{d['vs_us_3y']:+.1f}%"   if d.get("vs_us_3y") is not None else "—"
            ro_rows.append(
                f"| {rk} | {d['total']:.1f} | {d['macro_score']:.1f} | {d['momentum_score']:.1f} "
                f"| {d['relative_score']:.1f} | {emoji}{d['label']} "
                f"| {gdp_c} | {infl_c} | {r1_c} | {vs3_c} |"
            )
        ro_table = "\n".join(ro_rows)
        focus = ro.get("focus_region", {})
        focus_md = ""
        if focus.get("summary"):
            focus_md = (
                f"\n**本基金地区（{focus.get('name','—')}）**：{focus.get('label','—')}（{focus.get('score','—')}/10）\n\n"
                f"> {focus['summary']}"
            )
        notes_md = "\n".join(f"> ⚠️ {n}" for n in ro.get("data_notes", [])[:3])
        region_outlook_section = ro_table + focus_md + ("\n\n" + notes_md if notes_md else "")
    else:
        region_outlook_section = "_地区宏观数据不足，请运行 `python run.py` 更新后重新分析。_"

    # ── 逐年收益表 ───────────────────────────────────────────────
    yr_map = extra.get("year_returns") or {}
    if yr_map:
        yr_rows = ["| 年份 | 收益率 |", "|---:|---:|"]
        for yr in sorted(yr_map.keys()):
            ret = yr_map[yr]
            sign = "▲" if ret >= 0 else "▼"
            yr_rows.append(f"| {yr} | {sign} {abs(ret):.2f}% |")
        year_returns_section = "\n### 逐年收益\n\n" + "\n".join(yr_rows)
    else:
        year_returns_section = "\n### 逐年收益\n\n_暂无逐年收益数据（需更新数据后重新分析）_"

    # ── 基金经理详情 ──────────────────────────────────────────────
    mgr_list = extra.get("managers") or []
    if mgr_list:
        mgr_parts = []
        for m in mgr_list:
            name_str = m.get("name", "—")
            start = m.get("work_start_date") or "—"
            aum_m = m.get("total_assets_managed") or "—"
            composite_score = m.get("avg_annual_return")  # 东财综合评分 0-100
            tenure_ret = m.get("return_3y")               # 任期累计收益%（复用 return_3y 字段）
            desc = (m.get("description") or "")[:120]
            mgr_funds = m.get("managed_funds") or ""
            score_str = f"{composite_score:.1f}/100" if composite_score is not None else "—"
            tenure_ret_str = f"{tenure_ret:+.2f}%" if tenure_ret is not None else "—"
            mgr_parts.append(
                f"**{name_str}**　任职时长：{start}　在管规模：{aum_m}\n\n"
                f"| 东财综合评分 | 任期累计收益 |\n"
                f"|---:|---:|\n"
                f"| {score_str} | {tenure_ret_str} |\n\n"
                + (f"> {desc}\n" if desc else "")
                + (f"_在管基金：{mgr_funds[:150]}_\n" if mgr_funds else "")
            )
        manager_section = "\n### 基金经理\n\n" + "\n---\n".join(mgr_parts)
    else:
        manager_section = "\n### 基金经理\n\n_暂无详细经理数据（需更新数据）_"

    # ── 申购/赎回费率（_fee_table 已提为模块级）─────────────────────
    purchase_section  = _fee_table(extra.get("purchase_fees") or [],  "申购费率")
    redemption_section = _fee_table(extra.get("redemption_fees") or [], "赎回费率")

    mgmt_str    = f"{mgmt_fee*100:.3f}%"    if mgmt_fee    is not None else "—"
    custody_str = f"{custody_fee*100:.3f}%" if custody_fee is not None else "—"
    er_str_full = f"{er*100:.3f}%" if er else "—"

    fee_section = f"""### 费率详情

| 费用项目 | 费率 |
|---|---:|
| 管理费率（年） | {mgmt_str} |
| 托管费率（年） | {custody_str} |
| 综合年费率（管理+托管） | {er_str_full} |

{purchase_section}

{redemption_section}

> 注：申购费为直销渠道标准费率，各平台优惠力度不同，实际以购买渠道为准。"""

    # ── 换手率 ────────────────────────────────────────────────────
    turn_map = extra.get("turnover") or {}
    if turn_map:
        turn_rows = ["| 年份 | 换手率 |", "|---:|---:|"]
        for yr in sorted(turn_map.keys()):
            turn_rows.append(f"| {yr} | {turn_map[yr]*100:.1f}% |")
        turnover_section = "### 换手率\n\n" + "\n".join(turn_rows)
    else:
        turnover_section = "### 换手率\n\n_暂无换手率数据（部分基金不披露）_"

    return f"""# 单基金综合研判报告

**基金**：{name}（{result['fund_code']}）　　**研判日期**：{date_str}　　**综合等级**：{grade}

> **一句话结论**：{concl['summary']}

---

## 一、产品概况

| 项目 | 内容 |
|---|---|
| 基金代码 | {result['fund_code']} |
| 基金名称 | {name} |
| 基金类型 | {info.get('fund_type', '—')} |
| 资产类别 | {info.get('asset_class', '—')} |
| 投资地区 | {info.get('region', '—')} |
| 业绩基准 | {info.get('benchmark', '—')} |
| 成立日期 | {inception} |
| 成立年限（代理） | {f"{tenure:.1f} 年" if tenure else "—"} |
| 基金规模 | {f"{aum/1e8:.1f} 亿元" if aum and aum>0 else "—"} |
| 管理费率 | {mgmt_str} |
| 托管费率 | {custody_str} |
| 综合年费率 | {er_str_full} |
| QDII 特殊风险 | 汇率风险、QDII 额度限制、海外市场时差 |

---

## 二、综合评分（7 维 100 分制）

{score_table}

**评分等级说明**：85–100 优质候选 / 75–85 合格候选 / 65–75 有明显短板 / 50–65 不建议配置 / <50 剔除

---

## 三、一票否决检查

{veto_md}

> 说明：一票否决触发时，总分无效，结论强制为"剔除"。

---

## 四、关键量化指标对比

{peer_table}

| 高级指标 | 数值 | 说明 |
|---|---|---|
| 年化 Alpha | {_f(adv.get('alpha_annual'))}% | 相对 SP500 代理基准 |
| Beta | {_f(adv.get('beta'))} | 相对 SP500 |
| 信息比率 IR | {_f(adv.get('information_ratio'))} | 年化超额 / 年化跟踪误差 |
| 下行捕获率 | {_f(adv.get('downside_capture'))} | <1 表示跌幅小于市场 |
| 滚动3年胜率 | {f"{adv['rolling_win_rate']*100:.1f}%" if adv.get('rolling_win_rate') and adv['rolling_win_rate']==adv['rolling_win_rate'] else "—"} | vs SP500，月度滚动36期 |
| 卡玛比率 | {_f(adv.get('calmar_ratio'))} | 年化收益 / 最大回撤 |
| R² | {_f(adv.get('r_squared'))} | 市场 beta 对收益的解释度 |
| 样本月数 | {adv.get('data_months', 0)} | OLS 回归有效月数 |

---

## 五、风险特征详解

### 维度二：风险控制（{scores.get('risk', {}).get('score', 0):.1f} / 20）

{_detail_table("risk")}

---

## 六、持仓穿透

{holding_md}

---

## 七、收益归因

### 维度五：收益来源（{scores.get('attribution', {}).get('score', 0):.1f} / 10）

{_detail_table("attribution")}

> **注**：本系统以 SP500 为代理基准计算 alpha/beta/IR。QDII 应以实际跟踪基准（如纳斯达克100、日经225）为准，建议结合第三方 factor model 验证。Active Share 因缺少完整指数成分持仓数据，当前无法计算。

---

## 八、配置结论

**综合等级**：{grade}　　**总分**：{total:.1f} / 100

**赚钱逻辑**：{concl.get('earn_logic', '—')}

**主要风险**：{'、'.join(concl.get('main_risks', ['—']))}

**建议仓位角色**：{concl.get('role', '—')}
{signal_md}
### 各维度评分详解

#### 业绩质量（{scores.get('performance', {}).get('score', 0):.1f} / 20）
{_detail_table("performance")}

#### 基金经理（{scores.get('manager', {}).get('score', 0):.1f} / 15）
{_detail_table("manager")}

#### 策略稳定性（{scores.get('strategy', {}).get('score', 0):.1f} / 15）
{_detail_table("strategy")}

#### 规模流动性（{scores.get('structure', {}).get('score', 0):.1f} / 10）
{_detail_table("structure")}

#### 费用成本（{scores.get('cost', {}).get('score', 0):.1f} / 10）
{_detail_table("cost")}

---

---

## 九、逐年收益

{year_returns_section}

---

## 十、费率与成本

{fee_section}

---

## 十一、换手率

{turnover_section}

---

## 十二、基金经理

{manager_section}

---

## 十三、地区宏观机会评估

{region_outlook_section}

---

_本报告由单基金研判引擎自动生成，评分框架来源于《基金量化分析评分框架》（7维100分制）和《分析拆解基金方法论》（8模块）。_
_alpha/beta/IR 以 SP500 为代理基准，仅供参考，不构成投资建议。_"""


# ─────────────────────────────────────────────────────────────
# 持仓健康诊断报告（独立入口，不依赖 build_report）
# ─────────────────────────────────────────────────────────────

def build_holdings_report(
    check_result: dict,
    output_dir: str | Path = "reports",
) -> Path:
    """将 check_holdings() 的结构化结果生成 Markdown 报告文件，返回文件路径。"""
    date_str = datetime.now().strftime("%Y-%m-%d")
    content = _holdings_report_content(check_result, date_str)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{date_str}_holdings_check.md"
    out_path.write_text(content, encoding="utf-8")
    return out_path


def _holdings_report_content(result: dict, date_str: str) -> str:
    _EMOJI = {"重仓进取": "🟢", "标配稳健": "🔵", "谨慎防守": "🟠", "减仓防守": "🔴"}
    _VERDICT_CN = {"green": "✅ 健康", "yellow": "⚠️ 关注", "red": "🚨 警示"}
    _SIGNAL_LABEL = {"买入": "买入↑", "增持": "增持↑", "持有": "持有·", "观望": "观望△", "回避": "回避✗"}

    composite = result.get("composite_signal", "未知")
    sig_emoji = _EMOJI.get(composite, "⚪")
    sig_date = result.get("signal_date", "")
    ana = result["analytics"]
    gap = result["gap"]
    verdict = result["verdict"]
    overall_cn = _VERDICT_CN.get(verdict["overall"], verdict["overall"])

    # ── 持仓明细表 ──────────────────────────────────────────
    rows = ["| 代码 | 名称 | 权重 | 综合评分 | 信号 | 策略匹配 | 费率 | 说明 |",
            "|---|---|---:|---:|---|---:|---:|---|"]
    for h in result["holdings"]:
        code = h["fund_code"]
        name = h.get("fund_name") or code
        w = f"{h['weight']:.1f}%"
        sc = h.get("score")
        score_str = _score(sc["total_score"]) if sc and sc.get("total_score") is not None else "—"
        sig = _SIGNAL_LABEL.get(h.get("signal") or "", "—") if code != "cash" else "—"
        strat = f"{h.get('strategy_score', 0):.1f}/10" if code != "cash" else "—"
        er = h.get("expense_ratio")
        er_str = f"{er*100:.2f}%" if er else "—"
        issue = h.get("issue") or ""
        rows.append(f"| {code} | {name} | {w} | {score_str} | {sig} | {strat} | {er_str} | {issue} |")
    fund_table = "\n".join(rows)

    # ── 资产分布表 ──────────────────────────────────────────
    ac_rows = ["| 资产类别 | 权重 |", "|---|---:|"]
    for k, v in sorted(ana["asset_class_distribution"].items(), key=lambda x: -x[1]):
        ac_rows.append(f"| {k} | {v:.1f}% |")
    ac_table = "\n".join(ac_rows)

    rg_rows = ["| 地区 | 权重 |", "|---|---:|"]
    for k, v in sorted(ana["region_distribution"].items(), key=lambda x: -x[1]):
        rg_rows.append(f"| {k} | {v:.1f}% |")
    rg_table = "\n".join(rg_rows)

    # ── Gap 表 ─────────────────────────────────────────────
    gap_lines = []
    if gap["in_recommendation"]:
        for r in gap["in_recommendation"]:
            gap_lines.append(f"- ✓ **{r['name']}**（{r['code']}）— 与系统推荐重叠")
    if gap["not_in_recommendation"]:
        for c in gap["not_in_recommendation"]:
            gap_lines.append(f"- △ {c} — 不在当前推荐池")
    if gap["missing_recommended"]:
        gap_lines.append("\n**推荐池中尚未持有：**")
        for r in gap["missing_recommended"]:
            gap_lines.append(f"- {r['name']}（{r['code']}）")
    gap_section = "\n".join(gap_lines) if gap_lines else "（无评分数据，无法对比）"

    # ── 裁决区 ─────────────────────────────────────────────
    issues_md = "\n".join(f"- 🔸 {i}" for i in verdict["issues"]) or "无"
    strengths_md = "\n".join(f"- 🔹 {s}" for s in verdict["strengths"]) or "无"
    actions_md = "\n".join(f"- {a}" for a in verdict["actions"]) or "无"

    hhi = ana["hhi"]
    hhi_label = "分散良好" if hhi < 0.4 else "中等集中" if hhi < 0.65 else "高度集中"
    ws = ana.get("weighted_score")
    wer = ana.get("weighted_expense_ratio")

    return f"""# 持仓健康诊断报告

**诊断日期**：{date_str}　　**市场信号**：{sig_emoji} {composite}（{sig_date}）　　**健康裁决**：{overall_cn}

---

## 一、持仓明细

{fund_table}

---

## 二、组合分析

### 资产类别分布

{ac_table}

### 地区分布

{rg_table}

### 关键指标

| 指标 | 数值 | 说明 |
|---|---|---|
| 集中度（HHI） | {hhi:.3f} | {hhi_label}（0=完全分散，1=单一集中） |
| 加权综合评分 | {_score(ws) if ws is not None else '—'} / 100 | 有评分数据的基金加权均分 |
| 加权策略匹配 | {ana['weighted_strategy_score']:.2f} / 10 | 与当前市场信号的适配度 |
| 加权费率 | {f"{wer:.2f}%" if wer is not None else "—"} | 持仓平均年费率 |
| 现金仓位 | {ana['cash_pct']:.1f}% | 市场建议 {ana['recommended_cash_pct']:.1f}% |
| 数据库覆盖率 | {ana['in_db_coverage_pct']:.1f}% | 有评分数据的持仓权重占比 |

---

## 三、与系统推荐对比

{gap_section}

---

## 四、健康裁决：{overall_cn}

### 问题

{issues_md}

### 亮点

{strengths_md}

### 建议操作

{actions_md}

---

_本报告由持仓诊断引擎自动生成，基于系统数据库中的基金评分与当前市场信号。_
_所有结论仅供参考，不构成投资建议，投资者应结合自身风险承受能力独立判断。_"""
