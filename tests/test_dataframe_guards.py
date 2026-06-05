"""
DataFrame 防护测试 — 验证各采集器/分析器在真实失败场景下不崩溃。

每个测试类**直接调用生产函数**（打桩 I/O：read_table / requests / yfinance），
不在测试体内复刻逻辑——这样生产代码一旦回归（防护被改坏），测试就会变红。

  - TestNewsCollectorVIX        : VIX close 为 None/NaN 时不抛 TypeError
  - TestNewsCollectorSP500Mom   : SP500 close NaN 或起始值为0 时不抛异常
  - TestNewsCollectorFinnhub    : Finnhub 返回 dict 时不把 key 当文章处理
  - TestFundCollectorNavGuard   : existing.iloc[-1]["nav"] 为 NaN 时不抛 TypeError
  - TestValuationCollectorPE    : yfinance pe 为字符串 "N/A" 时不抛 ValueError
  - TestValuationAnalyzerNaN    : sp500_df close 列全 NaN 时 estimate 函数不崩溃
"""
import numpy as np
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock

from src.collectors import news_collector as nc
from src.collectors import fund_collector as fc
from src.collectors import valuation_collector as vc


# ────────────────────────────────────────────────────────────────────
# 辅助：构造 market_data 表风格的 DataFrame
# ────────────────────────────────────────────────────────────────────

def _market_df(close_values: list, symbol: str = "^GSPC") -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=len(close_values), freq="B").strftime("%Y-%m-%d").tolist()
    return pd.DataFrame({
        "symbol": symbol,
        "name": "Test",
        "date": dates,
        "open": close_values,
        "high": close_values,
        "low": close_values,
        "close": close_values,
        "volume": [1e6] * len(close_values),
    })


def _sentiment_with_market(vix_df: pd.DataFrame, sp500_df: pd.DataFrame) -> dict:
    """调用真实 get_market_sentiment，打桩 read_table 返回构造的行情、禁用所有新闻 key
    （避免联网），从而完整走一遍生产里的 VIX/动量防护分支。"""
    def _fake_read_table(table, where=None, params=None):
        sym = params[0] if params else None
        if sym == "^VIX":
            return vix_df
        if sym == "^GSPC":
            return sp500_df
        return pd.DataFrame()

    with patch.object(nc, "read_table", side_effect=_fake_read_table), \
         patch.object(nc, "_get_key", return_value=""):   # 无新闻 key → 不联网
        return nc.get_market_sentiment()


# ────────────────────────────────────────────────────────────────────
# 1. news_collector: VIX close 为 NULL → 走生产 get_market_sentiment
# ────────────────────────────────────────────────────────────────────

class TestNewsCollectorVIX:
    """VIX close 值为 None 或 NaN 时，生产函数应使用默认值 18.0，不崩溃。"""

    def test_nan_close_returns_default(self, capsys):
        """NaN close → 默认 VIX = 18.0，不抛 TypeError，并打印告警。"""
        result = _sentiment_with_market(_market_df([float("nan")], "^VIX"),
                                        _market_df([5000.0] * 25, "^GSPC"))
        assert result["vix"] == 18.0
        assert "[WARN]" in capsys.readouterr().out

    def test_none_close_raises_without_guard(self):
        """验证前提：不加防护直接 float(None) 确实抛 TypeError。"""
        with pytest.raises(TypeError):
            float(None)

    def test_valid_close_returns_correct_value(self):
        """正常 close 值应正确读取，不受影响。"""
        result = _sentiment_with_market(_market_df([23.5], "^VIX"),
                                        _market_df([5000.0] * 25, "^GSPC"))
        assert result["vix"] == 23.5


# ────────────────────────────────────────────────────────────────────
# 2. news_collector: SP500 动量计算中的 NaN 和 division-by-zero
# ────────────────────────────────────────────────────────────────────

class TestNewsCollectorSP500Mom:
    """SP500 close 含 NaN 或基准价为 0 时，生产动量计算应保持默认值 0.0。"""

    def _momentum(self, close_values: list) -> float:
        result = _sentiment_with_market(_market_df([18.0], "^VIX"),
                                        _market_df(close_values, "^GSPC"))
        return result["sp500_1m_return"]

    def test_all_nan_close_stays_zero(self):
        """全部 NaN 的 close → 动量 = 0.0，不崩溃。"""
        assert self._momentum([float("nan")] * 25) == 0.0

    def test_partial_nan_uses_valid_rows(self):
        """前段 NaN，后段有效 → 只用有效行计算，不崩溃。"""
        assert self._momentum([float("nan")] * 5 + [100.0] * 20) == pytest.approx(0.0, abs=1e-6)

    def test_base_price_zero_stays_zero(self, capsys):
        """基准价为 0 → 不执行除法，动量 = 0.0。"""
        assert self._momentum([0.0] + [100.0] * 24) == 0.0

    def test_normal_growth_correct(self):
        """正常数据：从 100 涨到 110，1m return ≈ 10%。"""
        assert self._momentum([100.0] * 20 + [110.0] * 5) == pytest.approx(10.0, abs=0.01)


# ────────────────────────────────────────────────────────────────────
# 3. news_collector: Finnhub 返回 dict 时的防护 → 走生产 _fetch_finnhub
# ────────────────────────────────────────────────────────────────────

class TestNewsCollectorFinnhub:
    """Finnhub API 返回 dict（错误响应）时，生产函数应返回 None，不迭代 dict keys。"""

    def _fetch(self, json_payload):
        """调用真实 _fetch_finnhub，打桩缓存（miss）与 requests.get 返回指定 JSON。"""
        fake_resp = MagicMock()
        fake_resp.raise_for_status.return_value = None
        fake_resp.json.return_value = json_payload
        with patch.object(nc, "_load_cache", return_value=None), \
             patch.object(nc, "_save_cache"), \
             patch("requests.get", return_value=fake_resp):
            return nc._fetch_finnhub("dummy_key", "2024-01-01")

    def test_dict_response_returns_none(self):
        """Finnhub 返回错误 dict → 应返回 None，不迭代 dict keys。"""
        assert self._fetch({"error": "API limit reached", "code": 429}) is None

    def test_empty_list_returns_none(self):
        """空列表 → 应返回 None。"""
        assert self._fetch([]) is None

    def test_valid_list_proceeds(self):
        """正常文章列表 → 应返回打分结果 dict。"""
        articles = [{"headline": "stocks rally to record high", "summary": "ok", "category": "general"}]
        result = self._fetch(articles)
        assert result is not None and result.get("articles") == 1

    def test_score_headlines_rejects_dict(self):
        """底层 _score_headlines 直接喂 dict（key 是字符串无 .get）应抛错——
        正是这层防护要拦截的崩溃。"""
        from src.collectors.news_collector import _score_headlines
        with pytest.raises((AttributeError, TypeError)):
            _score_headlines({"headline": "test", "summary": "ok"})


# ────────────────────────────────────────────────────────────────────
# 4. fund_collector: existing.iloc[-1]["nav"] 为 NaN → 走生产补全函数
# ────────────────────────────────────────────────────────────────────

class TestFundCollectorNavGuard:
    """existing DataFrame 末行 nav 为 NaN 时，生产补全逻辑应跳过该基金，不抛 TypeError。"""

    # 选一个 _QDII_TO_ETF 里有映射的代码，使其进入补全分支
    _CODE = "513100"

    def _existing(self, last_nav) -> pd.DataFrame:
        return pd.DataFrame({
            "fund_code": [self._CODE, self._CODE],
            "date":      ["2024-01-01", "2024-01-02"],
            "nav":       [1.0, last_nav],
            "acc_nav":   [1.0, last_nav],
            "daily_return": [0.0, 0.0],
        })

    def _run(self, last_nav):
        """调用真实 _patch_recent_via_yfinance，打桩 FX 与 yf.download 提供下游数据，
        返回 (补全后的该基金行数, 原行数)。NaN 末行应被跳过 → 行数不变。"""
        nav_history = {self._CODE: self._existing(last_nav)}
        fund_list = [{"fund_code": self._CODE}]

        fx = pd.Series({"2024-01-03": 7.0, "2024-01-04": 7.0})
        future = pd.DataFrame(
            {"Close": [500.0, 505.0]},
            index=pd.to_datetime(["2024-01-03", "2024-01-04"]),
        )
        fake_yf = MagicMock()
        fake_yf.download.return_value = future

        before = len(nav_history[self._CODE])
        with patch.dict("sys.modules", {"yfinance": fake_yf}), \
             patch.object(fc, "_fetch_usdcny", return_value=fx):
            out = fc._patch_recent_via_yfinance(fund_list, nav_history)
        return len(out[self._CODE]), before

    def test_nan_nav_skips(self):
        """NaN 末行 nav → 跳过补全（行数不变），不崩溃。"""
        after, before = self._run(float("nan"))
        assert after == before

    def test_valid_nav_proceeds(self):
        """正常末行 nav → 实际拼接补全（行数增加）。"""
        after, before = self._run(1.2345)
        assert after > before


# ────────────────────────────────────────────────────────────────────
# 5. valuation_collector: yfinance PE 为字符串 → 走生产转换
# ────────────────────────────────────────────────────────────────────

class TestValuationCollectorPE:
    """yfinance 偶发返回非数字 PE 时，生产转换不应抛 ValueError，而应得到 None。"""

    def _safe_pe(self, trailing, forward=None):
        """调用生产里的健壮 float 转换 helper（与 _collect_pe_via_yfinance 同源）。"""
        return vc._safe_pe_float(trailing if trailing is not None else forward)

    def test_string_na_returns_none(self):
        assert self._safe_pe("N/A") is None

    def test_string_number_parses(self):
        assert self._safe_pe("23.5") == pytest.approx(23.5)

    def test_none_returns_none(self):
        assert self._safe_pe(None, None) is None

    def test_valid_float_passes_through(self):
        assert self._safe_pe(24.5) == pytest.approx(24.5)

    def test_dict_pe_returns_none(self):
        assert self._safe_pe({"value": 24.5}) is None

    def test_old_code_would_crash_on_string(self):
        """验证前提：不加防护时 float('N/A') 确实抛 ValueError。"""
        with pytest.raises(ValueError):
            float("N/A")


# ────────────────────────────────────────────────────────────────────
# 6. valuation analyzer: sp500_df close 列含 NaN
# ────────────────────────────────────────────────────────────────────

class TestValuationAnalyzerNaN:
    """_estimate_cape / _estimate_pe / _calc_buffett_indicator 在 NaN 场景下不崩溃。"""

    def _nan_sp500_df(self, n=5) -> pd.DataFrame:
        """全部 close 为 NaN 的 sp500 DataFrame。"""
        return pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=n, freq="B").strftime("%Y-%m-%d").tolist(),
            "close": [float("nan")] * n,
        })

    def _valid_sp500_df(self) -> pd.DataFrame:
        return pd.DataFrame({
            "date": ["2024-01-01", "2024-02-01"],
            "close": [4800.0, 5000.0],
        })

    def test_estimate_cape_all_nan_returns_default(self):
        """close 列全 NaN → 返回默认值 30.0，不崩溃。"""
        from src.analyzers.valuation import _estimate_cape
        assert _estimate_cape(self._nan_sp500_df()) == 30.0

    def test_estimate_pe_all_nan_returns_default(self):
        """close 列全 NaN → 返回默认值 22.0，不崩溃。"""
        from src.analyzers.valuation import _estimate_pe
        assert _estimate_pe(self._nan_sp500_df()) == 22.0

    def test_estimate_cape_empty_returns_default(self):
        from src.analyzers.valuation import _estimate_cape
        assert _estimate_cape(pd.DataFrame()) == 30.0

    def test_estimate_pe_empty_returns_default(self):
        from src.analyzers.valuation import _estimate_pe
        assert _estimate_pe(pd.DataFrame()) == 22.0

    def test_estimate_cape_valid_data_calculates(self):
        """正常数据 → 计算结果在合理区间 [12, 50]。"""
        from src.analyzers.valuation import _estimate_cape
        result = _estimate_cape(self._valid_sp500_df())
        assert 12 <= result <= 50

    def test_calc_buffett_all_nan_value_falls_back(self):
        """value 列全 NaN → 跌回 sp500 估算路径，不崩溃。"""
        from src.analyzers.valuation import _calc_buffett_indicator
        nan_df = pd.DataFrame({"date": ["2024-01-01"], "value": [float("nan")]})
        result, source = _calc_buffett_indicator(nan_df, nan_df, self._valid_sp500_df())
        assert isinstance(result, float)
        assert source == "estimated"

    def test_calc_buffett_valid_data_returns_real(self):
        """有效 FRED 数据 → 返回 real 来源。"""
        from src.analyzers.valuation import _calc_buffett_indicator
        eq_df = pd.DataFrame({"date": ["2024-01-01"], "value": [45000000.0]})
        gdp_df = pd.DataFrame({"date": ["2024-01-01"], "value": [27000.0]})
        result, source = _calc_buffett_indicator(eq_df, gdp_df, pd.DataFrame())
        assert source == "real"
        assert result > 0
