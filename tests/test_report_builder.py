"""report_builder（单基金研判 / 持仓诊断报告）辅助函数测试 + report_editor 不变量。

主投研报告自 2026-06 起仅产出 HTML，相关三层结构 / 六因子表 / 审查门等不变量回归
已迁至 tests/test_html_report.py。本文件只保留：
  - report_builder 仍在的单基金报告辅助函数（_fmt_num / _fee_table / _dimension_detail_table）
  - report_editor 的触发条件单一真相源逻辑（格式无关，MD/HTML 共用）
"""
import pytest

from src.reports import report_builder as rb
from src.reports import report_editor as ed
from tests._report_fixtures import make_signal, make_portfolio, make_ai_portfolio


class TestFmtNum:
    @pytest.mark.parametrize("v,expected", [
        (None, "—"), (float("nan"), "—"), (0, "0.00"),
        (1.234, "1.23"), (-5.6, "-5.60"),
    ])
    def test_default_two_decimals(self, v, expected):
        assert rb._fmt_num(v) == expected

    def test_custom_decimals(self):
        assert rb._fmt_num(1.2345, 3) == "1.234"
        assert rb._fmt_num(None, 3) == "—"


class TestFeeTable:
    def test_empty_rows(self):
        assert rb._fee_table([], "申购费率") == "**申购费率**：暂无数据\n"

    def test_rows_rendered(self):
        out = rb._fee_table([{"rate_desc": "<7天", "rate": 0.015}], "赎回费率")
        assert "**赎回费率**" in out
        assert "<7天" in out
        assert "1.50%" in out

    def test_missing_rate_dash(self):
        out = rb._fee_table([{"rate_desc": "持有>2年", "rate": None}], "赎回费率")
        assert "—" in out


class TestDimensionDetailTable:
    def test_renders_subitems_with_coverage(self):
        scores = {"risk": {"details": {
            "夏普": {"score": 3.0, "max": 5, "coverage": "COMPUTED", "note": "优秀"},
            "回撤": {"score": 1.5, "max": 5, "coverage": "PROXY"},
        }}}
        out = rb._dimension_detail_table(scores, "risk")
        assert "夏普" in out and "回撤" in out
        assert "✅" in out and "~" in out

    def test_missing_dimension_no_crash(self):
        out = rb._dimension_detail_table({}, "nonexistent")
        assert "子项" in out  # 表头仍在，不崩溃


class TestEditor:
    def test_dedupe_keeps_order(self):
        assert ed.dedupe_keep_order(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]

    def test_canonical_prefers_ai_triggers(self):
        out = ed.canonical_triggers(make_signal(), make_ai_portfolio())
        assert any("VIX突破25且连续3日" in t for t in out)
        assert all("**" not in t for t in out)  # 纯文本，MD/HTML 共用

    def test_canonical_falls_back_to_rules_without_ai(self):
        out = ed.canonical_triggers(make_signal(), make_portfolio())
        assert out  # 无 AI 时退回规则层，非空

    def test_headline_is_subset(self):
        full = ed.canonical_triggers(make_signal(), make_ai_portfolio())
        head = ed.headline_triggers(make_signal(), make_ai_portfolio(), 1)
        assert len(head) == 1 and head[0] == full[0]
