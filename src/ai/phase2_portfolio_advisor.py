"""Phase 2: Portfolio Advisor — 投资决策阶段"""
from .backend import call_with_tools
from .schemas import PHASE2_TOOL
from ..utils.config import load_config

_SYSTEM_ROLE = """\
你是专业的基金组合构建顾问，负责将宏观分析转化为具体的基金投资决策。

任务：基于阶段一市场分析，为每只推荐基金提供"为何适合当前周期"的理由，
并输出可执行的仓位管理建议。

决策原则：
- 基金选择已由量化评分系统完成，你的任务是提供定性的"为什么"
- 理由必须具体：引用具体的宏观因子/周期特征，不说空话
- 风险提示要针对该基金的具体特征（费率、跟踪指数、地区集中度）
- 仓位建议要可操作，包含具体触发条件
- 场景分析要基于当前已识别的主要矛盾展开

cycle_fit 写作要求（必须遵守）：
❌ 差示例："该基金在当前市场环境下表现稳健，适合配置。"
✓ 好示例："跟踪纳斯达克100，当前利率方向分-0.8（降息预期），科技成长股受益于估值重估；
   趋势分8/10显示动量仍强；该基金策略匹配分X高于平均，性价比突出。"

risk_note 写作要求：
❌ 差示例："需注意市场风险。"
✓ 好示例："费率1.2%偏高，若市场转为震荡将显著拖累超额收益；
   纳指集中于科技大盘，若AI估值泡沫修正风险集中暴露。"

position_sizing_notes 要求：
- 每条必须含可执行动词（"增持"/"减持"/"止盈"/"补仓"）和具体触发条件
- 示例："若VIX回落至16以下且趋势分维持8分，可将核心仓位上限从60%提至70%"\
"""


def _format_phase1_summary(phase1: dict) -> str:
    risk_lines = []
    for r in phase1.get("risk_factors", [])[:3]:
        risk_lines.append(f"  - [{r.get('severity','?')}] {r.get('risk','')}")

    bias = phase1.get("allocation_bias", {})
    cycle = phase1.get("cycle_phase_assessment", {})
    regional = phase1.get("regional_opportunity_map", {})

    lines = [
        "=== 阶段一市场分析结论 ===",
        f"主要矛盾：{phase1.get('primary_contradiction', 'N/A')}",
        "",
        f"周期研判：{cycle.get('confirmed_phase', 'N/A')}（置信度：{cycle.get('phase_confidence', 'N/A')}）",
        f"研判依据：{cycle.get('phase_reasoning', 'N/A')}",
        f"异常信号：{', '.join(cycle.get('dissonant_signals', [])) or '无'}",
        "",
        f"配置偏向：权益{bias.get('equity_bias','N/A')} / 风格{bias.get('style_preference','N/A')} / 地区{bias.get('geographic_tilt','N/A')}",
        f"偏向理由：{bias.get('reasoning', 'N/A')}",
        "",
        f"偏好区域：{', '.join(regional.get('preferred_regions', [])) or '无特别偏好'}",
        f"回避区域：{', '.join(regional.get('avoid_regions', [])) or '无'}",
        "",
        "主要风险：",
    ] + risk_lines + [
        "",
        f"市场叙事：{phase1.get('market_narrative', 'N/A')}",
    ]
    return "\n".join(lines)


def _format_funds(portfolio: dict, market_signal: dict) -> str:
    lines = [
        "=== 候选基金详情（供本次决策使用）===",
        f"综合信号：{market_signal.get('composite_signal', 'N/A')}，"
        f"建议仓位：核心{market_signal.get('core_allocation', 0)*100:.0f}% / "
        f"卫星{market_signal.get('satellite_allocation', 0)*100:.0f}% / "
        f"现金{market_signal.get('cash_allocation', 0)*100:.0f}%",
        "",
    ]

    def fmt_fund(f: dict, role: str) -> str:
        er = f.get("expense_ratio")
        er_str = f"{er*100:.2f}%" if er is not None else "N/A"
        return (
            f"[{role}] {f.get('fund_code', '')} {f.get('fund_name', '')} "
            f"({f.get('fund_type', 'N/A')}) | "
            f"综合分{f.get('score', f.get('total_score', 'N/A'))} | "
            f"信号:{f.get('signal', 'N/A')} | "
            f"绩效分{f.get('performance_score', 'N/A')} | "
            f"风险分{f.get('risk_score', 'N/A')} | "
            f"策略匹配{f.get('strategy_score', 'N/A')} | "
            f"稳定性{f.get('consistency_score', 'N/A')} | "
            f"费率{er_str}"
        )

    core_funds = portfolio.get("core_funds", [])
    if core_funds:
        lines.append("核心持仓：")
        for f in core_funds:
            lines.append("  " + fmt_fund(f, "核心"))
        lines.append("")

    sat_funds = portfolio.get("satellite_funds", [])
    if sat_funds:
        lines.append("卫星持仓：")
        for f in sat_funds:
            lines.append("  " + fmt_fund(f, "卫星"))
        lines.append("")

    top = portfolio.get("top_picks", [])[:5]
    remaining = [
        f for f in top
        if f.get("fund_code") not in {x.get("fund_code") for x in core_funds + sat_funds}
    ]
    if remaining:
        lines.append("备选池（前5，未入选）：")
        for f in remaining:
            lines.append("  " + fmt_fund(f, "备选"))

    return "\n".join(lines)


def _normalize_output(result: dict) -> dict:
    """修复 DeepSeek 常见输出问题，保证下游访问安全。"""
    # 1. scenario_analysis 有时返回字符串而非 {bull/bear/base_case} 对象
    sc = result.get("scenario_analysis")
    if isinstance(sc, str):
        result["scenario_analysis"] = {"base_case": sc, "bull_case": "", "bear_case": ""}
    elif not isinstance(sc, dict):
        result["scenario_analysis"] = {"base_case": "", "bull_case": "", "bear_case": ""}

    # 2. fund_rationales 中个别基金可能缺少 required 字段
    _RATIONALE_DEFAULTS = {
        "fund_code": "",
        "fund_name": "",
        "role": "核心",
        "cycle_fit": "",
        "risk_note": "",
        "conviction_level": "medium",
    }
    rationales = result.get("fund_rationales")
    if isinstance(rationales, list):
        for r in rationales:
            if isinstance(r, dict):
                for k, v in _RATIONALE_DEFAULTS.items():
                    r.setdefault(k, v)

    return result


class PortfolioAdvisor:
    def __init__(self):
        cfg = load_config().get("ai_analysis", {})
        self.model = cfg.get("phase2_model", "claude-sonnet-4-6")
        self.max_tokens = 6000

    def advise(
        self,
        market_signal: dict,
        ai_phase1: dict,
        portfolio: dict,
    ) -> dict | None:
        try:
            result = call_with_tools(
                system=_SYSTEM_ROLE,
                user_parts=[
                    _format_phase1_summary(ai_phase1),
                    _format_funds(portfolio, market_signal),
                ],
                tool=PHASE2_TOOL,
                model=self.model,
                max_tokens=self.max_tokens,
                cache_system=True,
                cache_first_user=True,
            )
            return _normalize_output(result)
        except Exception as e:
            print(f"[AI Phase2] 决策失败，使用规则层 fallback: {e}")
            return None
