"""Phase 3：对抗式审查器（Adversarial Reviewer）。

借鉴 Anthropic「自助数据分析」实践中的 adversarial review sub-agent：
在 Phase2 给出投资决策后，由一个**默认怀疑、只负责挑错**的子智能体复核，专抓
「与量化数据矛盾 / 无依据 / 过度自信 / 遗漏风险 / 自相矛盾」五类问题。

这是对抗「静默失败」（答案错但看起来合理、被直接采用）的防线。代价是额外的
token 与延迟，故默认关闭，由 config `ai_analysis.adversarial_review.enabled` 控制，
仅对重要输出按需启用。任何失败都降级为 None，绝不阻断主流程。
"""
import traceback

from .backend import call_with_tools
from .schemas import PHASE3_TOOL
from ..utils.config import load_config

_SYSTEM_ROLE = """\
你是投资决策的对抗式审查员（红队）。你的唯一职责是挑错，不是附和。

审查原则：
- 默认怀疑：每一条主张都要问「数据真的支持它吗？」，证据不足即记为问题。
- 只依据给定的量化事实判断，不引入外部信息，也不臆测未提供的数据。
- 重点抓五类问题：①与给定数据直接矛盾；②无数据支撑的断言；③信心表述超出证据强度；
  ④遗漏了数据中明显的风险；⑤决策内部自相矛盾（如建议仓位与综合信号档位不一致）。
- 引用具体数值来支撑你的每一条质疑，禁止「表述可以更严谨」这类空洞批评。
- 如果决策确实稳健、未发现实质问题，就如实给出 sound 并返回空 findings——不要为凑数而编造问题。

严格性校准：
- material_concerns 仅用于「与数据矛盾」或「无依据且会误导决策」的实质问题。
- 措辞偏乐观但方向无误 → 至多 minor_concerns。\
"""


def _fmt(v, suffix=""):
    return f"{v}{suffix}" if v is not None else "N/A"


def _format_facts(signal: dict, portfolio: dict) -> str:
    """把审查所需的量化事实压缩成一段，供审查员据此核对决策。"""
    val = signal.get("valuation") or {}
    lines = [
        "=== 量化事实（审查基准，决策中的主张必须能追溯到这里）===",
        f"综合信号：{signal.get('composite_signal', 'N/A')}"
        f"（综合分 {_fmt(signal.get('timing_score'))}/10）",
        f"建议仓位：核心 {(signal.get('core_allocation') or 0)*100:.0f}% / "
        f"卫星 {(signal.get('satellite_allocation') or 0)*100:.0f}% / "
        f"现金 {(signal.get('cash_allocation') or 0)*100:.0f}%",
        f"CAPE {_fmt(signal.get('cape'))} | 标普PE {_fmt(val.get('sp500_pe'))} | "
        f"VIX {_fmt(signal.get('vix'))} | 趋势分 {_fmt(signal.get('trend_score'))}/10 | "
        f"信用利差分 {_fmt(signal.get('credit_score'))}/10",
        f"估值判断：{val.get('valuation_level', 'N/A')} | "
        f"宏观周期：{signal.get('macro_cycle', 'N/A')}",
        f"数据可信度：{signal.get('data_source', 'N/A')}（real/partial/mock）",
    ]
    if signal.get("stop_loss_triggered"):
        lines.append("⚠️ 止损已触发：信号已被强制降至「减仓防守」档。")

    all_f = (portfolio.get("core_funds") or []) + (portfolio.get("satellite_funds") or [])
    if all_f:
        lines.append("")
        lines.append("=== 推荐持仓（决策应与这些基金的角色/权重/评分自洽）===")
        lines.append(
            f"组合档位：核心 {_fmt(portfolio.get('core_allocation_pct'))}% / "
            f"卫星 {_fmt(portfolio.get('satellite_allocation_pct'))}% / "
            f"现金 {_fmt(portfolio.get('cash_allocation_pct'))}%"
        )
        for f in all_f:
            lines.append(
                f"- [{f.get('role', '—')}] {f.get('fund_code', '')} "
                f"{f.get('fund_name', '')}：权重 {_fmt(f.get('weight'))}%，"
                f"综合分 {_fmt(f.get('score') or f.get('total_score'))}"
            )
    return "\n".join(lines)


def _format_decision(ai_decision: dict) -> str:
    """把 Phase2 决策铺平为待审查文本。"""
    lines = ["=== 待审查的投资决策（Phase2 产出）==="]
    lines.append(f"组合论点：{ai_decision.get('portfolio_thesis', '（无）')}")

    notes = ai_decision.get("position_sizing_notes") or []
    if notes:
        lines.append("仓位建议：")
        lines += [f"  - {n}" for n in notes]

    sc = ai_decision.get("scenario_analysis") or {}
    if isinstance(sc, dict) and any(sc.values()):
        lines.append(
            f"情景：牛 {sc.get('bull_case', '—')} | "
            f"基 {sc.get('base_case', '—')} | 熊 {sc.get('bear_case', '—')}"
        )

    rats = ai_decision.get("fund_rationales") or []
    if rats:
        lines.append("个基逻辑：")
        for r in rats:
            if isinstance(r, dict):
                lines.append(
                    f"  - {r.get('fund_code', '')}（{r.get('conviction_level', '?')}信心）："
                    f"{r.get('cycle_fit', '')}｜风险：{r.get('risk_note', '')}"
                )
    return "\n".join(lines)


def _normalize_review(result: dict) -> dict:
    """保证下游访问安全：补默认字段、修正畸形结构。"""
    if not isinstance(result, dict):
        return {"overall_verdict": "minor_concerns", "confidence": "low",
                "findings": [], "summary": "审查返回结构异常，无法解析。"}

    verdict = result.get("overall_verdict")
    if verdict not in ("sound", "minor_concerns", "material_concerns"):
        result["overall_verdict"] = "minor_concerns"
    if result.get("confidence") not in ("high", "medium", "low"):
        result["confidence"] = "medium"

    findings = result.get("findings")
    if not isinstance(findings, list):
        findings = []
    clean = []
    for f in findings:
        if not isinstance(f, dict):
            continue
        f.setdefault("claim", "")
        f.setdefault("category", "unsupported_claim")
        if f.get("severity") not in ("high", "medium", "low"):
            f["severity"] = "medium"
        f.setdefault("issue", "")
        f.setdefault("suggested_fix", "")
        clean.append(f)
    result["findings"] = clean
    result.setdefault("summary", "")
    # 便于下游与报告快速使用的派生计数
    result["high_severity_count"] = sum(1 for f in clean if f.get("severity") == "high")
    return result


class AdversarialReviewer:
    def __init__(self):
        cfg = load_config().get("ai_analysis", {})
        rcfg = cfg.get("adversarial_review") or {}
        # 审查模型默认沿用 Phase2 模型；可单独覆盖
        self.model = rcfg.get("model") or cfg.get("phase2_model", "claude-sonnet-4-6")
        self.max_tokens = rcfg.get("max_tokens", 3000)

    def review(self, market_signal: dict, portfolio: dict, ai_decision: dict) -> dict | None:
        """对 Phase2 决策做对抗审查，返回结构化审查结果；失败返回 None（不阻断）。"""
        if not ai_decision:
            return None
        try:
            result = call_with_tools(
                system=_SYSTEM_ROLE,
                user_parts=[
                    _format_facts(market_signal, portfolio),
                    _format_decision(ai_decision),
                ],
                tool=PHASE3_TOOL,
                model=self.model,
                max_tokens=self.max_tokens,
                cache_system=True,
                cache_first_user=True,
            )
            return _normalize_review(result)
        except Exception as e:
            module = type(e).__module__ or ""
            is_api_err = module.startswith(("anthropic", "openai")) or isinstance(e, (ValueError, TimeoutError))
            if is_api_err:
                print(f"[AI Phase3] 对抗审查 API 调用失败，跳过审查: {e}")
            else:
                print(f"[AI Phase3] 对抗审查意外错误，跳过审查: {e}")
                traceback.print_exc()
            return None


def is_enabled() -> bool:
    """对抗审查是否启用（默认关闭，需 config 显式开启）。"""
    cfg = load_config().get("ai_analysis", {})
    return bool((cfg.get("adversarial_review") or {}).get("enabled", False))
