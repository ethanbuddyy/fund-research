"""Phase 1: Market Context Analyzer — 市场解析阶段"""
from typing import Any
from collections.abc import Mapping
import traceback
from .backend import call_with_tools
from .schemas import PHASE1_TOOL
from ..utils.config import load_config

_SYSTEM_ROLE = """\
你是专业的宏观投资分析师，专注于QDII基金的市场周期分析。

你的任务是综合解读量化指标，识别：
1. 当前市场的主要矛盾（互相对立的信号如何权衡）
2. 各因子之间的相互强化或对冲关系
3. 全球宏观背景对资产配置的影响
4. 量化信号未能完全捕捉的尾部风险

分析原则：
- 量化信号提供客观基准，你的任务是识别其背后的"为什么"和矛盾点
- 当高估值与强趋势共存时，不要简单取均值，而要识别主导因子
- 全球宏观分析重点关注各区域周期错位带来的配置机会
- 保持客观，不过度乐观或悲观
- 所有结论必须能追溯到具体的量化数据

market_narrative 写作要求（必须遵守）：
- 必须引用至少3个具体数值（例如：CAPE 34.2、VIX 18.5、信用利差 3.8%）
- 先陈述现状矛盾，再给出倾向性判断，最后说明近期需关注的触发点
- 禁止使用"市场处于关键节点"、"需保持关注"等空洞表述
- 示例格式："CAPE 34.2（历史90%分位）与信用利差3.2%（历史低位）并存——估值压力真实存在，
  但流动性环境尚未出现收紧信号。趋势偏差+6.5%显示动量仍占优，主导因子暂时压过估值压力。
  关注触发点：若VIX突破25或信用利差走阔至4.5%以上，需重新评估进取仓位。"\
"""


def _format_signal_data(signal: Mapping[str, Any]) -> str:
    macro = signal.get("macro", {})
    val = signal.get("valuation", {})
    sent = signal.get("sentiment", {})
    gm = signal.get("global_macro", {})

    fed_dir = signal.get("fed_direction", 0.0)
    if fed_dir < -0.25:
        rate_dir = "降息通道"
    elif fed_dir > 0.25:
        rate_dir = "加息通道"
    else:
        rate_dir = "按兵不动"

    inverted_note = " [已倒挂]" if macro.get("yield_inverted") else ""

    # VIX 历史参考区间（用于让 AI 判断当前 VIX 处于高/低位）
    vix = signal.get("vix")
    if vix is None:
        vix_context = "N/A"
    elif vix < 13:
        vix_context = f"{vix}（极低，历史低于此水平的概率<10%）"
    elif vix < 18:
        vix_context = f"{vix}（低位，市场情绪平静）"
    elif vix < 25:
        vix_context = f"{vix}（中性，正常波动区间）"
    elif vix < 35:
        vix_context = f"{vix}（偏高，市场存在明显恐慌）"
    else:
        vix_context = f"{vix}（极高，历史危机水平）"

    lines = [
        "=== 美国宏观环境 ===",
        f"经济周期：{macro.get('cycle', 'N/A')}（周期分：{macro.get('cycle_score', 'N/A')}/10）",
        f"GDP增长(YoY)：{macro.get('gdp_growth', 'N/A')}%  "
        f"核心通胀({macro.get('inflation_gauge', 'PCE')})：{macro.get('inflation', 'N/A')}%",
        f"联邦基金利率：{macro.get('fed_rate', 'N/A')}%  利率方向：{rate_dir}（方向分：{fed_dir:+.1f}）",
        f"失业率：{macro.get('unemployment', 'N/A')}%  "
        f"期限利差(10Y-2Y)：{macro.get('yield_curve', 'N/A')}%{inverted_note}",
        f"政策环境：{macro.get('policy_env', 'N/A')}",
        "",
        "=== 市场估值 ===",
        f"Shiller CAPE：{val.get('cape', 'N/A')}（历史第{val.get('cape_percentile', 'N/A')}%分位，来源：{val.get('cape_source', 'N/A')}）",
        f"标普P/E：{val.get('sp500_pe', 'N/A')}  "
        f"巴菲特指标：{val.get('buffett_indicator', 'N/A')}  "
        f"ERP：{val.get('equity_risk_premium', 'N/A')}%",
        f"估值判断：{val.get('valuation_level', 'N/A')}（估值分：{val.get('valuation_score', 'N/A')}/10）",
        "",
        "=== 技术与情绪 ===",
        f"VIX：{vix_context}  情绪：{sent.get('label', 'N/A')}",
        f"趋势分(S&P500 vs 年线)：{signal.get('trend_score', 'N/A')}/10  "
        f"信用利差分：{signal.get('credit_score', 'N/A')}/10",
        "",
        "=== 综合量化信号 ===",
        f"综合信号：{signal.get('composite_signal', 'N/A')}（评分{signal.get('timing_score', 0):.2f}/10）",
        f"建议仓位：核心{signal.get('core_allocation', 0)*100:.0f}% / "
        f"卫星{signal.get('satellite_allocation', 0)*100:.0f}% / "
        f"现金{signal.get('cash_allocation', 0)*100:.0f}%",
        f"宏观调整分（含利率方向）：{signal.get('macro_adj', 'N/A')}/10",
    ]

    regions = gm.get("regions", {})
    if regions:
        lines += ["", "=== 全球宏观（各区域）==="]
        strongest = gm.get("strongest", "")
        weakest = gm.get("weakest", "")
        for region, data in regions.items():
            tag = ""
            if region == strongest:
                tag = " ★最强"
            elif region == weakest:
                tag = " ▼最弱"
            lines.append(
                f"{region}{tag}：GDP {data.get('gdp_growth', 'N/A')}% | "
                f"通胀 {data.get('inflation', 'N/A')}% | "
                f"评分{data.get('score', 'N/A')}/10（{data.get('label', 'N/A')}）"
            )

    # RAG：注入检索到的相关证据（retrieval.inject_into_ai 门控；关闭则下方块为空、输出逐字不变）
    try:
        from ..retrieval.recall import evidence_block
        kw = " ".join(
            s for s in [
                macro.get("cycle", ""),
                val.get("valuation_level", ""),
                sent.get("label", ""),
                signal.get("composite_signal", ""),
            ] if s and s != "N/A"
        )
        block = evidence_block(kw, doc_types=["news", "narrative", "region", "report"])
    except Exception:
        block = ""
    if block:
        lines += ["", block]

    return "\n".join(lines)


class MarketContextAnalyzer:
    def __init__(self):
        cfg = load_config().get("ai_analysis", {})
        self.model = cfg.get("phase1_model", "claude-sonnet-4-6")
        self.max_tokens = 4000

    def analyze(self, market_signal: Mapping[str, Any]) -> dict | None:
        try:
            return call_with_tools(
                system=_SYSTEM_ROLE,
                user_parts=[_format_signal_data(market_signal)],
                tool=PHASE1_TOOL,
                model=self.model,
                max_tokens=self.max_tokens,
                cache_system=True,
            )
        except Exception as e:
            module = type(e).__module__ or ""
            is_api_err = module.startswith(("anthropic", "openai")) or isinstance(e, (ValueError, TimeoutError))
            if is_api_err:
                print(f"[AI Phase1] API调用失败，使用规则层 fallback: {e}")
            else:
                print(f"[AI Phase1] 意外程序错误，使用规则层 fallback: {e}")
                traceback.print_exc()
            return None
