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


# ─────────────────────────────────────────────────────────────
# 对外主入口
# ─────────────────────────────────────────────────────────────

def build_report(
    signal: dict,
    portfolio: dict,
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

    content = "\n\n---\n\n".join(sections)

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
        trend_label = "强趋势" if float(trend_score) >= 6.5 else "弱趋势" if float(trend_score) <= 3.5 else "中性趋势"
        cape_str = f"CAPE {_num(cape, '.1f')}" if cape else val_level
        conclusions.append(
            f"估值偏{val_label}（{cape_str}，估值分 {_score(val_score)}/10）与"
            f"{trend_label}（趋势分 {_score(trend_score)}/10）并存——"
            f"综合评分 {_num(raw, '.2f')}/10，触发「{composite}」信号。"
        )

    # 结论2：宏观/信用环境
    fed_dir = signal.get("fed_direction", 0.0) or 0.0
    fed_label = "降息方向" if fed_dir > 0 else "加息方向" if fed_dir < 0 else "利率平稳"
    credit_str = f"信用利差分 {_score(credit)}/10" if credit else "信用利差数据缺失"
    conclusions.append(
        f"宏观周期「{macro_cycle}」，利率{fed_label}（方向修正 {_num(fed_dir, '+.1f')} 分）；"
        f"{credit_str}，"
        + ("流动性环境宽松。" if credit and float(credit) >= 6 else
           "信用环境偏紧，需关注风险溢价上升。" if credit and float(credit) <= 3.5 else
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
        + (f"情绪面 {vix_str} 处于中性区间，当前仓位合理。" if vix and 15 <= float(vix) <= 25 else
           f"{vix_str} 偏高，卫星仓位已相应收缩。" if vix and float(vix) > 25 else "")
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
        trend_strong = float(trend_score or 5) >= 6.5
        if val_high and trend_strong:
            contradiction = f"高估值（CAPE {_num(signal.get('cape'), '.1f')}，估值分 {_score(val_score)}/10）vs 强趋势（趋势分 {_score(trend_score)}/10）——动量暂时压过估值压力"
        elif val_high:
            contradiction = f"高估值压力（CAPE {_num(signal.get('cape'), '.1f')}，估值分 {_score(val_score)}/10）与偏弱的趋势信号并存——谨慎防御"
        elif trend_strong:
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
    if vix and float(vix) > 25:
        qdii_risks.insert(0, f"**⚠️ 当前 VIX {_num(vix, '.1f')} 偏高**：市场波动加剧，场内 ETF 溢价可能快速扩大，操作需谨慎")
    if credit_score and float(credit_score) <= 3.5:
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
            if float(trend_score) >= 6.5:
                plan_items.append(f"趋势分持续 ≥ 6.5 且 VIX 保持 < 20，可将核心仓位上限从 {core_pct:.0f}% 提至 {min(80, core_pct+10):.0f}%")
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
