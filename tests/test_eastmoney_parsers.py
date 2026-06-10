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


# ─────────────────────────────────────────────────────────────
# 畸形/缺失输入的降级路径（issue #6）——这些解析点过去靠 except 兜底但无专测
# ─────────────────────────────────────────────────────────────

class TestParseHoldings:
    def test_all_vars_missing_returns_none(self):
        assert em._parse_holdings("var Unrelated = 1;", "001") is None

    def test_partial_stock_codes_only(self):
        js = 'var stockCodes = ["600519", "000858"];'
        out = em._parse_holdings(js, "001")
        assert out is not None
        assert out["stock_codes"] == "600519,000858"
        assert out["date"]  # 缺日期时回填当天，不为 None

    def test_non_numeric_ratio_skipped_not_crash(self):
        js = ('var Data_assetAllocation = {"categories":["2026"],'
              '"series":[{"name":"股票占净比","data":["N/A"]}]};')
        # 唯一可解析项是非数字 → 跳过 → 整体无有效数据 → None
        assert em._parse_holdings(js, "001") is None

    def test_numeric_ratio_parsed(self):
        js = ('var Data_assetAllocation = {"categories":["2026Q1"],'
              '"series":[{"name":"股票占净比","data":[88.5]},'
              '{"name":"债券占净比","data":[5.0]}]};')
        out = em._parse_holdings(js, "001")
        assert out["stock_ratio"] == 88.5 and out["bond_ratio"] == 5.0
        assert out["date"] == "2026Q1"


class TestParseTurnover:
    def test_missing_var_returns_empty(self):
        assert em._parse_turnover("var X = 1;", "001") == []

    def test_pair_list_format(self):
        out = em._parse_turnover('var hsltList = [["2024", 1.23], ["2025", 2.5]];', "001")
        assert {r["year"] for r in out} == {2024, 2025}
        assert out[0]["turnover_rate"] == 1.23

    def test_dict_format(self):
        out = em._parse_turnover('var hsltList = [{"year": "2023", "value": 0.8}];', "001")
        assert out == [{"fund_code": "001", "year": 2023, "turnover_rate": 0.8}]

    def test_out_of_range_year_and_bad_values_skipped(self):
        js = 'var hsltList = [["1800", 1.0], ["2024", "bad"], ["2025", 3.3]];'
        out = em._parse_turnover(js, "001")
        assert out == [{"fund_code": "001", "year": 2025, "turnover_rate": 3.3}]


class TestParseFeeSplit:
    def test_missing_returns_none(self):
        assert em._parse_fee_split("var Nothing = 1;", "001") is None

    def test_dict_manage_fee(self):
        js = 'var feeInfo = {"manageFee": "0.75%", "trustFee": "0.25%"};'
        out = em._parse_fee_split(js, "001")
        assert out["mgmt_fee"] == 0.0075 and out["custody_fee"] == 0.0025

    def test_regex_fallback_from_js_text(self):
        js = "其它文本… 管理费率：1.50% 托管费率：0.30% …"
        out = em._parse_fee_split(js, "001")
        # 兜底正则命中管理费率（先匹配者返回）
        assert out is not None
        assert out["mgmt_fee"] == 0.015


class TestParseRateStr:
    def test_percent_string(self):
        assert em._parse_rate_str("0.75%") == 0.0075

    def test_already_decimal(self):
        assert em._parse_rate_str(0.0075) == 0.0075

    def test_percent_number_normalized(self):
        # >0.1 视为百分比形式 → 除以 100
        assert em._parse_rate_str(0.75) == 0.0075

    def test_none_and_garbage(self):
        assert em._parse_rate_str(None) is None
        assert em._parse_rate_str("暂无") is None
