"""AI Phase2 输出归一化（_normalize_output）健壮性测试。

LLM 返回的 JSON 结构不可信（DeepSeek 尤甚）。_normalize_output 负责把畸形结构
修成下游可安全访问的形状。下游报告层直接 .get 这些字段，归一化错了会在渲染时
崩溃或丢字段，故必须测试。
"""
from src.ai.phase2_portfolio_advisor import _normalize_output


class TestScenarioAnalysis:
    def test_string_scenario_wrapped_into_base_case(self):
        out = _normalize_output({"scenario_analysis": "整体震荡偏多"})
        sc = out["scenario_analysis"]
        assert sc["base_case"] == "整体震荡偏多"
        assert sc["bull_case"] == "" and sc["bear_case"] == ""

    def test_missing_scenario_filled(self):
        out = _normalize_output({})
        sc = out["scenario_analysis"]
        assert set(sc) == {"base_case", "bull_case", "bear_case"}

    def test_non_dict_non_str_scenario_replaced(self):
        out = _normalize_output({"scenario_analysis": ["a", "b"]})
        assert out["scenario_analysis"]["base_case"] == ""

    def test_valid_scenario_preserved(self):
        good = {"bull_case": "牛", "base_case": "基", "bear_case": "熊"}
        out = _normalize_output({"scenario_analysis": dict(good)})
        assert out["scenario_analysis"] == good


class TestFundRationales:
    def test_missing_required_fields_defaulted(self):
        out = _normalize_output({"fund_rationales": [{"fund_code": "001"}]})
        r = out["fund_rationales"][0]
        for k in ("fund_name", "role", "cycle_fit", "risk_note", "conviction_level"):
            assert k in r
        assert r["role"] == "核心"
        assert r["conviction_level"] == "medium"

    def test_existing_fields_not_overwritten(self):
        out = _normalize_output({"fund_rationales": [
            {"fund_code": "001", "role": "卫星", "conviction_level": "high"}]})
        r = out["fund_rationales"][0]
        assert r["role"] == "卫星"
        assert r["conviction_level"] == "high"

    def test_non_list_rationales_left_untouched_no_crash(self):
        out = _normalize_output({"fund_rationales": "oops"})
        assert out["fund_rationales"] == "oops"  # 不崩溃即可

    def test_non_dict_items_skipped_no_crash(self):
        out = _normalize_output({"fund_rationales": ["x", {"fund_code": "1"}]})
        assert out["fund_rationales"][1]["role"] == "核心"


class TestPassthrough:
    def test_other_keys_preserved(self):
        out = _normalize_output({"position_sizing_notes": ["a"], "foo": 1})
        assert out["position_sizing_notes"] == ["a"]
        assert out["foo"] == 1
