"""HTML 报告安全测试 —— 钉死「外部/AI 文本必须转义」这一安全属性。

存储型 XSS 防护（html_report_builder._e）此前无任何回归测试：任何人把 `_e()`
改回裸 f-string 插值，其余测试仍全绿。这里直接调用各 section 渲染函数，喂入含
`<script>` / `<img onerror>` 的基金名、AI 叙事、推荐逻辑、情景文本，断言：
  - 危险标签被转义为实体（出现 `&lt;script&gt;`）
  - 原始可执行标签不出现在输出（不出现 `<script>`）
"""
import src.reports.html_report_builder as h


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
        out = h._section_action(self._portfolio())
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
