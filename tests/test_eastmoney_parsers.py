"""eastmoney 非官方接口（pingzhongdata）解析器测试。

天天基金 pingzhongdata 是非官方 JS 接口，字段/格式随时可能变动，是全链路最脆弱
的解析点之一。这里用构造的 JS 文本测试纯解析函数对正常/缺失/畸形输入的行为。
"""
from src.collectors import eastmoney_collector as em


class TestExtractVar:
    def test_extracts_json_array(self):
        js = 'var Data_x = [1,2,3];/*注释*/var Data_y = 5;'
        assert em._extract_var(js, "Data_x") == [1, 2, 3]

    def test_extracts_last_var_at_eof(self):
        js = 'var Data_y = {"a": 1};'
        assert em._extract_var(js, "Data_y") == {"a": 1}

    def test_missing_var_returns_none(self):
        assert em._extract_var("var Foo = 1;", "Bar") is None

    def test_malformed_value_returns_none(self):
        js = "var Data_x = not_json_at_all;var Z = 1;"
        assert em._extract_var(js, "Data_x") is None


class TestParseNav:
    def _js(self, trend, acc=None):
        s = f"var Data_netWorthTrend = {trend};"
        if acc is not None:
            s += f"var Data_ACWorthTrend = {acc};"
        return s

    def test_basic_nav_rows(self):
        # x 为毫秒时间戳（2024-01-02 UTC+8 附近），y 为单位净值
        ms = 1704153600000  # 2024-01-02 00:00 UTC → 北京时间同日
        js = self._js(f'[{{"x": {ms}, "y": 1.23, "equityReturn": 0.5}}]')
        rows = em._parse_nav(js, "001")
        assert len(rows) == 1
        code, date, nav, acc_nav, dr = rows[0]
        assert code == "001"
        assert nav == 1.23
        assert acc_nav == 1.23           # 无 ACWorth → 回退用 nav
        assert dr == 0.5
        assert date.startswith("2024-01")

    def test_acc_nav_merged_by_date(self):
        ms = 1704153600000
        js = self._js(
            f'[{{"x": {ms}, "y": 1.10, "equityReturn": 0.0}}]',
            acc=f'[[{ms}, 2.50]]',
        )
        rows = em._parse_nav(js, "001")
        assert rows[0][3] == 2.50         # 累计净值取自 ACWorthTrend

    def test_empty_trend_returns_empty(self):
        assert em._parse_nav("var Data_netWorthTrend = [];", "001") == []

    def test_missing_trend_var_returns_empty(self):
        assert em._parse_nav("var Other = 1;", "001") == []

    def test_bad_points_skipped(self):
        ms = 1704153600000
        js = self._js(f'[{{"x": {ms}, "y": "bad"}}, {{"x": {ms}, "y": 1.5}}]')
        rows = em._parse_nav(js, "001")
        assert len(rows) == 1             # 坏点跳过，好点保留
        assert rows[0][2] == 1.5

    def test_empty_equity_return_becomes_none(self):
        ms = 1704153600000
        js = self._js(f'[{{"x": {ms}, "y": 1.5, "equityReturn": ""}}]')
        rows = em._parse_nav(js, "001")
        assert rows[0][4] is None
