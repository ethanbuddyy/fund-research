"""report_builder 从 _fund_report_content 巨函数提取出的模块级辅助函数测试。"""
import pytest

from src.reports import report_builder as rb


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
