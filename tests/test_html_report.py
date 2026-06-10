"""HTML 报告安全测试 —— 钉死「外部/AI 文本必须转义」这一安全属性。

存储型 XSS 防护（html_report_builder._e）此前无任何回归测试：任何人把 `_e()`
改回裸 f-string 插值，其余测试仍全绿。这里直接调用各 section 渲染函数，喂入含
`<script>` / `<img onerror>` 的基金名、AI 叙事、推荐逻辑、情景文本，断言：
  - 危险标签被转义为实体（出现 `&lt;script&gt;`）
  - 原始可执行标签不出现在输出（不出现 `<script>`）
"""
import src.reports.html_report_builder as h
from src.domain.factor_config import FACTOR_WEIGHTS
from tests._report_fixtures import make_signal, make_portfolio, make_ai_portfolio


XSS = "<script>alert(1)</script>"
IMG = "<img src=x onerror=alert(2)>"


def _assert_escaped(html_out: str, raw: str):
    assert raw not in html_out, f"未转义的原始标签泄漏到 HTML: {raw!r}"


class TestFundsSectionEscaping:
    def _portfolio(self):
        return {
            "core_funds": [{
                "fund_code": "001", "fund_name": f"{XSS}基金", "role": "核心",
                "weight": 50, "score": 8, "expense_ratio": 0.01, "signal": "买入",
            }],
            "satellite_funds": [],
            "ai_decision": {
                "fund_rationales": [{"fund_code": "001", "cycle_fit": IMG}],
                "position_sizing_notes": [f"建议 {XSS}"],
                "rebalance_triggers": [{"condition": XSS, "action": IMG}],
                "scenario_analysis": {"bull_case": XSS, "base_case": "", "bear_case": ""},
            },
            "investment_notes": [f"备注 {XSS}"],
            "top_picks": [],
        }

    def test_fund_name_escaped(self):
        out = h._section_funds(self._portfolio())
        _assert_escaped(out, XSS)
        assert "&lt;script&gt;" in out

    def test_fund_rationale_escaped(self):
        out = h._section_funds(self._portfolio())
        _assert_escaped(out, IMG)
        assert "&lt;img" in out

    def test_allocation_notes_escaped(self):
        out = h._section_allocation(self._portfolio(), 50, 30, 20)
        _assert_escaped(out, XSS)

    def test_action_plan_escaped(self):
        out = h._section_action({}, self._portfolio())
        _assert_escaped(out, XSS)
        _assert_escaped(out, IMG)

    def test_scenario_escaped(self):
        out = h._section_scenario(self._portfolio())
        _assert_escaped(out, XSS)


class TestMarketNarrativeEscaping:
    def test_narrative_escaped_and_newline_to_br(self):
        sig = {
            "ai_analysis": {
                "market_narrative": f"第一行\n{XSS}",
                "primary_contradiction": IMG,
            }
        }
        out = h._section_market(sig, "标配稳健", 5.0)
        _assert_escaped(out, XSS)
        _assert_escaped(out, IMG)
        assert "&lt;script&gt;" in out
        assert "<br>" in out  # 换行仍转为 <br>（在转义之后注入，不被转义）


class TestNumericFieldsNotBroken:
    def test_clean_input_renders_without_entities_noise(self):
        """正常无危险字符的输入不应被破坏（基本冒烟）。"""
        port = {
            "core_funds": [{"fund_code": "001", "fund_name": "标普500ETF", "role": "核心",
                            "weight": 60, "score": 8.2, "expense_ratio": 0.005, "signal": "买入"}],
            "satellite_funds": [], "ai_decision": {}, "investment_notes": [], "top_picks": [],
        }
        out = h._section_funds(port)
        assert "标普500ETF" in out
        assert "60" in out


# ─────────────────────────────────────────────────────────────
# 主报告不变量回归（自 MD 迁移）——CLAUDE.md「报告三层结构」契约，现仅守 HTML
# ─────────────────────────────────────────────────────────────

class TestA1SixFactorTable:
    """六因子表：含全球宏观、权重取自 FACTOR_WEIGHTS（禁硬编码）、贡献回归综合分。"""

    def test_table_has_six_factors_including_global_macro(self):
        out = h._section_market(make_signal(), "标配稳健", 5.96)
        assert "六因子评分" in out
        assert "全球宏观" in out

    def test_weights_come_from_factor_config_not_hardcoded(self):
        out = h._section_market(make_signal(), "标配稳健", 5.96)
        # 趋势 27% / 情绪 13.5% 必须出现（取自 FACTOR_WEIGHTS），证明非硬编码 30%/15%
        assert f"{FACTOR_WEIGHTS['trend']*100:g}%" in out      # 27%
        assert f"{FACTOR_WEIGHTS['sentiment']*100:g}%" in out  # 13.5%

    def test_contributions_reconcile_to_composite(self):
        # 六因子加权贡献之和应≈综合分（同源于 signals.py 的 composite_raw）
        s = make_signal()
        contrib = (
            s["macro_adj"] * FACTOR_WEIGHTS["macro"]
            + s["valuation"]["valuation_score"] * FACTOR_WEIGHTS["valuation"]
            + (10 - s["sentiment"]["score"] / 10) * FACTOR_WEIGHTS["sentiment"]
            + s["trend_score"] * FACTOR_WEIGHTS["trend"]
            + s["credit_score"] * FACTOR_WEIGHTS["credit"]
            + s["global_macro_score"] * FACTOR_WEIGHTS["global_macro"]
        )
        assert abs(contrib - s["timing_score"]) < 0.05


class TestA2ReviewGate:
    """对抗审查门：sound 时横幅为空；非 sound 时横幅 + 行动复核提示发声。"""

    def test_banner_empty_when_sound(self):
        p = make_portfolio(adversarial_review={"overall_verdict": "sound", "findings": []})
        assert h._review_banner_html(p) == ""

    def test_banner_surfaces_nonsound_verdict(self):
        p = make_portfolio(adversarial_review={
            "overall_verdict": "minor_concerns", "summary": "卫星档位与清仓矛盾",
            "findings": [{"severity": "medium", "category": "internal_inconsistency",
                          "issue": "档位要求20%却清仓", "suggested_fix": "改为减仓"}]})
        b = h._review_banner_html(p)
        assert "对抗审查" in b and "卫星档位与清仓矛盾" in b

    def test_action_caveat_lists_conflicts(self):
        p = make_portfolio(adversarial_review={
            "overall_verdict": "minor_concerns",
            "findings": [{"severity": "medium", "category": "internal_inconsistency",
                          "issue": "档位要求20%却清仓", "suggested_fix": "改为减仓"}]})
        c = h._action_caveat_html(p)
        assert "执行前须先复核" in c


class TestA3AlternatesReason:
    """备选池未入选原因须由评分推导，不按名称猜测。"""

    def test_no_name_guessing_and_reason_from_scores(self):
        out = h._section_alternates(make_portfolio())
        assert "未入选原因" in out
        # 006479(84.4)>在持最低 69.8 → 被门槛挡；539002(60.0)<69.8 → 评分低于已入选
        assert "未超在持最低分" in out
        assert "综合分低于已入选" in out


class TestA4WeightedFee:
    """组合费率按权重加权；权重全 0 时退回等权标注。"""

    def test_weighted_average_label(self):
        out = h._section_risk(make_portfolio(), make_signal())
        assert "加权平均费率" in out

    def test_falls_back_to_equal_weight_label(self):
        p = make_portfolio()
        for f in p["core_funds"] + p["satellite_funds"]:
            f["weight"] = 0
        out = h._section_risk(p, make_signal())
        assert "等权" in out


class TestA5SilentLossVoiced:
    """AI 在场却缺某基金理由时，必须发声而非静默留白。"""

    def test_missing_rationale_voiced_when_ai_present(self):
        # 270023 在 ai_decision.fund_rationales 中缺失 → 应出现「未生成该基金理由」
        out = h._section_funds(make_ai_portfolio())
        assert "未生成该基金理由" in out

    def test_no_false_alarm_when_ai_off(self):
        # 无 ai_decision → 不应误报缺失理由
        out = h._section_funds(make_portfolio())
        assert "未生成该基金理由" not in out


class TestThreeLayerStructure:
    """四层正文有序 + 审计附录折叠 + 触发单一出处 + 情景省略操作。"""

    def _build(self, tmp_path, p=None):
        p = p or make_ai_portfolio()
        path = h.build_html_report(make_signal(), p, output_dir=str(tmp_path))
        return path.read_text(encoding="utf-8")

    def test_four_layers_present_in_order(self, tmp_path):
        t = self._build(tmp_path)
        i1 = t.index("一、本期决策")
        i2 = t.index("二、为什么")
        i3 = t.index("三、买什么·卖什么")
        i4 = t.index("四、何时改变")
        assert i1 < i2 < i3 < i4

    def test_audit_content_folded(self, tmp_path):
        t = self._build(tmp_path)
        assert "<details" in t and "审计附录" in t

    def test_triggers_not_duplicated_as_full_list(self, tmp_path):
        # 第二条触发只在「四、何时改变」出现，不在它之前重复
        t = self._build(tmp_path)
        when = t.split("四、何时改变")[1]
        before = t.split("四、何时改变")[0]
        assert "核心PCE反弹至3.5%以上" in when
        assert "核心PCE反弹至3.5%以上" not in before

    def test_scenario_table_omits_actions(self, tmp_path):
        t = self._build(tmp_path)
        # 情景卡只到目标档位，不含 fund_actions（操作收归行动计划）
        assert "清仓卫星" not in t.split("情景分析")[1].split("</div>")[0] if "情景分析" in t else True


class TestScenarioOmitsActions:
    def test_section_scenario_no_actions(self):
        out = h._section_scenario(make_ai_portfolio())
        assert "目标档位" in out
        assert "清仓卫星" not in out
