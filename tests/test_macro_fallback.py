"""单元测试：宏观分析器的数据缺失处理"""
import pytest
from unittest.mock import patch, MagicMock
import pandas as pd
from src.analyzers.macro_analyzer import _determine_cycle, _calc_yoy, _get_latest


class TestDetermineCycleWarnings:
    def test_all_none_triggers_warning(self, capsys):
        """所有指标缺失时应打印 WARN"""
        _determine_cycle(None, None, None, None, 0.2)
        captured = capsys.readouterr()
        assert "[WARN]" in captured.out
        assert "数据缺失" in captured.out

    def test_partial_none_triggers_warning(self, capsys):
        """部分指标缺失时应打印 WARN"""
        _determine_cycle(2.5, None, None, None, 0.2)
        captured = capsys.readouterr()
        assert "[WARN]" in captured.out

    def test_all_present_no_warning(self, capsys):
        """数据完整时不应打印 WARN"""
        _determine_cycle(2.5, 3.0, 5.3, 4.1, 0.2)
        captured = capsys.readouterr()
        assert "[WARN]" not in captured.out

    def test_returns_valid_phase(self):
        """即使数据缺失，也应返回合法的 phase 字段"""
        result = _determine_cycle(None, None, None, None, 0.2)
        assert "phase" in result
        assert result["phase"] in ["扩张", "高峰", "收缩", "衰退", "复苏"]

    def test_returns_score_in_range(self):
        """score 应在合理范围内"""
        for args in [
            (3.0, 2.5, 5.0, 4.0, 0.5),
            (None, None, None, None, -0.5),
            (-1.0, 8.0, 7.0, 6.0, -0.3),
        ]:
            result = _determine_cycle(*args)
            assert 0 < result["score"] <= 10


class TestDataQualityField:
    def test_analyze_macro_cycle_returns_data_quality(self):
        """analyze_macro_cycle 返回值应包含 data_quality 字段"""
        mock_empty = pd.DataFrame()

        with patch("src.analyzers.macro_analyzer.read_table", return_value=mock_empty):
            from src.analyzers.macro_analyzer import analyze_macro_cycle
            result = analyze_macro_cycle()
            assert "data_quality" in result
            assert result["data_quality"] in ("full", "partial")

    def test_partial_when_data_missing(self):
        """数据库为空时 data_quality 应为 partial"""
        mock_empty = pd.DataFrame()

        with patch("src.analyzers.macro_analyzer.read_table", return_value=mock_empty):
            from src.analyzers.macro_analyzer import analyze_macro_cycle
            result = analyze_macro_cycle()
            assert result["data_quality"] == "partial"


class TestCalcYoy:
    def test_returns_none_for_empty(self):
        assert _calc_yoy(pd.DataFrame()) is None
        assert _calc_yoy(None) is None

    def test_returns_none_for_single_row(self):
        df = pd.DataFrame({"date": ["2024-01"], "value": [100.0]})
        assert _calc_yoy(df) is None

    def test_positive_growth(self):
        df = pd.DataFrame({"date": ["2023-01", "2024-01"], "value": [100.0, 102.0]})
        result = _calc_yoy(df)
        assert result is not None
        assert abs(result - 2.0) < 0.01

    def test_handles_nan(self):
        df = pd.DataFrame({"date": ["2023-01", "2024-01"], "value": [float("nan"), 100.0]})
        assert _calc_yoy(df) is None


class TestGetLatest:
    def test_returns_none_for_empty(self):
        assert _get_latest(pd.DataFrame()) is None
        assert _get_latest(None) is None

    def test_returns_latest_value(self):
        df = pd.DataFrame({"date": ["2024-01", "2024-02"], "value": [1.0, 2.0]})
        result = _get_latest(df)
        assert result == 2.0
