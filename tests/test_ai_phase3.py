"""Phase 3 对抗式审查器测试。

覆盖：
  - _normalize_review 对畸形/缺字段 LLM 输出的健壮归一化
  - is_enabled 默认关闭、显式开启
  - AdversarialReviewer.review 在 ai_decision 为空时直接返回 None（不调用 LLM）
  - 报告层（MD + HTML）对审查结果的渲染，且 HTML 中审查文本被转义（防 XSS）
"""
from unittest.mock import patch

import pytest

from src.ai import phase3_adversarial_reviewer as p3
from src.reports import html_report_builder as h


# ── 归一化 ────────────────────────────────────────────────────

class TestNormalizeReview:
    def test_non_dict_returns_safe_default(self):
        out = p3._normalize_review("garbage")
        assert out["overall_verdict"] == "minor_concerns"
        assert out["findings"] == []

    def test_invalid_verdict_coerced(self):
        out = p3._normalize_review({"overall_verdict": "totally_fine", "findings": []})
        assert out["overall_verdict"] == "minor_concerns"

    def test_invalid_confidence_coerced(self):
        out = p3._normalize_review({"confidence": "absolute", "findings": []})
        assert out["confidence"] == "medium"

    def test_findings_defaults_and_severity_coercion(self):
        out = p3._normalize_review({"findings": [{"claim": "x", "severity": "catastrophic"}]})
        f = out["findings"][0]
        assert f["severity"] == "medium"
        for k in ("category", "issue", "suggested_fix"):
            assert k in f

    def test_non_list_findings_becomes_empty(self):
        out = p3._normalize_review({"findings": "oops"})
        assert out["findings"] == []

    def test_non_dict_findings_items_skipped(self):
        out = p3._normalize_review({"findings": ["str", {"claim": "ok"}]})
        assert len(out["findings"]) == 1

    def test_high_severity_count_derived(self):
        out = p3._normalize_review({"findings": [
            {"severity": "high"}, {"severity": "high"}, {"severity": "low"}]})
        assert out["high_severity_count"] == 2


# ── 开关 / 短路 ───────────────────────────────────────────────

class TestEnabledFlag:
    def test_disabled_by_default(self):
        with patch.object(p3, "load_config", return_value={"ai_analysis": {}}):
            assert p3.is_enabled() is False

    def test_enabled_when_configured(self):
        cfg = {"ai_analysis": {"adversarial_review": {"enabled": True}}}
        with patch.object(p3, "load_config", return_value=cfg):
            assert p3.is_enabled() is True


class TestReviewShortCircuits:
    def test_empty_decision_returns_none_without_llm(self):
        with patch.object(p3, "load_config", return_value={"ai_analysis": {}}), \
             patch.object(p3, "call_with_tools") as mock_call:
            r = p3.AdversarialReviewer().review({}, {}, {})
            assert r is None
            mock_call.assert_not_called()

    def test_review_normalizes_llm_output(self):
        fake = {"overall_verdict": "sound", "confidence": "high",
                "findings": [], "summary": "看起来一致"}
        with patch.object(p3, "load_config", return_value={"ai_analysis": {}}), \
             patch.object(p3, "call_with_tools", return_value=fake):
            r = p3.AdversarialReviewer().review(
                {"composite_signal": "标配稳健"}, {"core_funds": []},
                {"portfolio_thesis": "稳健配置"})
            assert r["overall_verdict"] == "sound"
            assert r["high_severity_count"] == 0

    def test_llm_failure_returns_none(self):
        with patch.object(p3, "load_config", return_value={"ai_analysis": {}}), \
             patch.object(p3, "call_with_tools", side_effect=ValueError("boom")):
            r = p3.AdversarialReviewer().review({}, {}, {"portfolio_thesis": "x"})
            assert r is None


# ── 报告渲染 ──────────────────────────────────────────────────

def _review_with_xss():
    return {
        "overall_verdict": "material_concerns",
        "confidence": "high",
        "summary": "决策与数据存在矛盾 <script>alert(1)</script>",
        "findings": [{
            "claim": "VIX 偏低 <script>x</script>",
            "category": "data_contradiction",
            "severity": "high",
            "issue": "实际 VIX=30 属高位 <img onerror=alert(2)>",
            "suggested_fix": "改为谨慎表述",
        }],
        "high_severity_count": 1,
    }


class TestHtmlRenderingEscaped:
    # 主报告已仅 HTML，对抗审查的渲染回归由本类（HTML）唯一守护；
    # 标签/类别中文 + XSS 转义一并断言（原 MD 渲染测试已废止）。
    def test_present_review_labels_rendered(self):
        out = h._section_adversarial({"adversarial_review": _review_with_xss()})
        assert "AI 对抗审查" in out
        assert "实质问题" in out         # material_concerns 标签
        assert "与数据矛盾" in out        # 类别中文
        assert "VIX" in out

    def test_absent_review_renders_empty(self):
        assert h._section_adversarial({}) == ""

    def test_present_review_escapes_xss(self):
        out = h._section_adversarial({"adversarial_review": _review_with_xss()})
        assert "AI 对抗审查" in out
        # 危险标签必须被转义，原始可执行标签不得出现
        assert "<script>alert(1)" not in out
        assert "<img onerror" not in out
        assert "&lt;script&gt;" in out
