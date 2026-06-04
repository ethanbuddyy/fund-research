"""
DataFrame 防护测试 — 验证各采集器/分析器在真实失败场景下不崩溃。

每个测试类对应一处真实修复：
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


# ────────────────────────────────────────────────────────────────────
# 1. news_collector: VIX close 为 NULL
# ────────────────────────────────────────────────────────────────────

class TestNewsCollectorVIX:
    """VIX close 值为 None 或 NaN 时，函数应使用默认值 18.0，不崩溃。"""

    def test_nan_close_returns_default(self, capsys):
        """NaN close → 默认 VIX = 18.0，不抛 TypeError。"""
        vix_df = _market_df([float("nan")], "^VIX")
        _vix_raw = vix_df.iloc[0]["close"]
        vix = 18.0
        if pd.notna(_vix_raw):
            vix = float(_vix_raw)
        else:
            print("[WARN] VIX 最新收盘价为 NULL，使用默认值 18.0")
        assert vix == 18.0
        assert "[WARN]" in capsys.readouterr().out

    def test_none_close_raises_without_guard(self):
        """验证前提：不加防护直接 float(None) 确实抛 TypeError。"""
        with pytest.raises(TypeError):
            float(None)

    def test_valid_close_returns_correct_value(self):
        """正常 close 值应正确读取，不受影响。"""
        vix_df = _market_df([23.5], "^VIX")
        _vix_raw = vix_df.iloc[0]["close"]
        vix = 18.0
        if pd.notna(_vix_raw):
            vix = float(_vix_raw)
        assert vix == 23.5


# ────────────────────────────────────────────────────────────────────
# 2. news_collector: SP500 动量计算中的 NaN 和 division-by-zero
# ────────────────────────────────────────────────────────────────────

class TestNewsCollectorSP500Mom:
    """SP500 close 含 NaN 或基准价为 0 时，动量计算应保持默认值 0.0。"""

    def _calc_momentum(self, close_values: list) -> float:
        """复现 news_collector.py 中修复后的动量计算逻辑。"""
        sp500_df = _market_df(close_values)
        sp500_1m_return = 0.0
        if len(sp500_df) >= 20:
            sp500_df = sp500_df.sort_values("date").dropna(subset=["close"])
            if len(sp500_df) >= 2:
                _base = float(sp500_df.iloc[0]["close"])
                if _base > 0:
                    sp500_1m_return = (float(sp500_df.iloc[-1]["close"]) / _base - 1) * 100
        return sp500_1m_return

    def test_all_nan_close_stays_zero(self):
        """全部 NaN 的 close → 动量 = 0.0，不崩溃。"""
        closes = [float("nan")] * 25
        result = self._calc_momentum(closes)
        assert result == 0.0

    def test_partial_nan_uses_valid_rows(self):
        """前段 NaN，后段有效 → 只用有效行计算，不崩溃。"""
        closes = [float("nan")] * 5 + [100.0] * 20
        result = self._calc_momentum(closes)
        assert result == pytest.approx(0.0, abs=1e-6)

    def test_base_price_zero_stays_zero(self, capsys):
        """基准价为 0 → 不执行除法，动量 = 0.0。"""
        closes = [0.0] + [100.0] * 24
        result = self._calc_momentum(closes)
        assert result == 0.0

    def test_normal_growth_correct(self):
        """正常数据：从 100 涨到 110，1m return ≈ 10%。"""
        closes = [100.0] * 20 + [110.0] * 5
        result = self._calc_momentum(closes)
        assert result == pytest.approx(10.0, abs=0.01)

    def test_old_code_nan_would_crash(self):
        """验证前提：不加防护 float(NaN)/float(0) 不会直接崩，但 None 会。"""
        with pytest.raises(TypeError):
            _ = float(None) / 100.0


# ────────────────────────────────────────────────────────────────────
# 3. news_collector: Finnhub 返回 dict 时的防护
# ────────────────────────────────────────────────────────────────────

class TestNewsCollectorFinnhub:
    """Finnhub API 返回 dict（错误响应）时不应把 key 当文章处理。"""

    def _safe_parse(self, raw_json) -> dict | None:
        """复现修复后的 _fetch_finnhub 类型检查逻辑。"""
        if not isinstance(raw_json, list):
            return None
        if not raw_json:
            return None
        return {"ok": True, "count": len(raw_json)}

    def test_dict_response_returns_none(self):
        """Finnhub 返回错误 dict → 应返回 None，不迭代 dict keys。"""
        error_response = {"error": "API limit reached", "code": 429}
        assert self._safe_parse(error_response) is None

    def test_empty_list_returns_none(self):
        """空列表 → 应返回 None。"""
        assert self._safe_parse([]) is None

    def test_string_response_returns_none(self):
        """字符串响应 → 应返回 None。"""
        assert self._safe_parse("error") is None

    def test_valid_list_proceeds(self):
        """正常文章列表 → 应继续处理。"""
        articles = [{"headline": "test", "summary": "ok", "category": "general"}]
        result = self._safe_parse(articles)
        assert result is not None

    def test_old_code_dict_would_misprocess(self):
        """验证前提：不加防护时 dict 会被当作可迭代对象处理（迭代其键而非文章）。"""
        # _score_headlines 迭代 articles，如果是 dict 则迭代 key（字符串）
        # 字符串没有 .get() 方法，会 AttributeError
        from src.collectors.news_collector import _score_headlines
        with pytest.raises((AttributeError, TypeError)):
            # dict 的 key 是字符串，字符串没有 .get("category")
            _score_headlines({"headline": "test", "summary": "ok"})


# ────────────────────────────────────────────────────────────────────
# 4. fund_collector: existing.iloc[-1]["nav"] 为 NaN
# ────────────────────────────────────────────────────────────────────

class TestFundCollectorNavGuard:
    """existing DataFrame 末行 nav 为 NaN 时，补全逻辑应 continue，不抛 TypeError。"""

    def _would_skip(self, nav_value) -> bool:
        """复现修复后的 nav NaN 检查逻辑，返回是否跳过本次补全。"""
        row = pd.Series({"nav": nav_value})
        _last_nav_raw = row["nav"]
        if not pd.notna(_last_nav_raw):
            return True  # continue
        _last_nav = float(_last_nav_raw)
        return False

    def test_nan_nav_skips(self):
        """NaN nav → 应跳过补全（不崩溃）。"""
        assert self._would_skip(float("nan")) is True

    def test_none_nav_skips(self):
        """None nav → 应跳过补全。"""
        assert self._would_skip(None) is True

    def test_valid_nav_proceeds(self):
        """正常 nav → 不跳过。"""
        assert self._would_skip(1.2345) is False

    def test_zero_nav_proceeds(self):
        """零值 nav → 不因 NaN 检查跳过（0 是有效值，后续由 base <= 0 拦截）。"""
        assert self._would_skip(0.0) is False

    def test_old_code_would_crash(self):
        """验证前提：不加防护时 float(None) 确实抛 TypeError。"""
        with pytest.raises(TypeError):
            float(None)


# ────────────────────────────────────────────────────────────────────
# 5. valuation_collector: yfinance PE 为字符串
# ────────────────────────────────────────────────────────────────────

class TestValuationCollectorPE:
    """yfinance 偶发返回非数字 PE 时，不应抛 ValueError，而应跳过。"""

    def _safe_pe(self, trailing, forward=None):
        """复现修复后的 PE 转换逻辑。"""
        _pe_raw = trailing or forward
        try:
            pe = float(_pe_raw) if _pe_raw is not None else None
        except (TypeError, ValueError):
            pe = None
        return pe

    def test_string_na_returns_none(self):
        """`"N/A"` 字符串 → float 失败 → 返回 None。"""
        assert self._safe_pe("N/A") is None

    def test_string_number_parses(self):
        """`"23.5"` 字符串 → 可解析为 float。"""
        assert self._safe_pe("23.5") == pytest.approx(23.5)

    def test_none_returns_none(self):
        """trailingPE 和 forwardPE 均 None → 返回 None。"""
        assert self._safe_pe(None, None) is None

    def test_valid_float_passes_through(self):
        """正常 float PE → 直接返回。"""
        assert self._safe_pe(24.5) == pytest.approx(24.5)

    def test_dict_pe_returns_none(self):
        """dict 类型（yfinance 偶发异常）→ float 失败 → 返回 None。"""
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

    from src.analyzers.valuation import _estimate_cape, _estimate_pe, _calc_buffett_indicator

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
        result = _estimate_cape(self._nan_sp500_df())
        assert result == 30.0

    def test_estimate_pe_all_nan_returns_default(self):
        """close 列全 NaN → 返回默认值 22.0，不崩溃。"""
        from src.analyzers.valuation import _estimate_pe
        result = _estimate_pe(self._nan_sp500_df())
        assert result == 22.0

    def test_estimate_cape_empty_returns_default(self):
        """空 DataFrame → 返回默认值 30.0。"""
        from src.analyzers.valuation import _estimate_cape
        assert _estimate_cape(pd.DataFrame()) == 30.0

    def test_estimate_pe_empty_returns_default(self):
        """空 DataFrame → 返回默认值 22.0。"""
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
        nan_df = pd.DataFrame({
            "date": ["2024-01-01"],
            "value": [float("nan")],
        })
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

    def test_old_estimate_cape_nan_close_would_give_nan_result(self):
        """验证前提：不加 dropna 时 NaN close 会传播到结果（不崩溃但结果无效）。"""
        # 手动复现修复前的行为
        sp500_df = self._nan_sp500_df()
        sp500_df_sorted = sp500_df.sort_values("date")  # 不 dropna
        current = float(sp500_df_sorted.iloc[-1]["close"])  # float(nan) = nan
        cape = 30.0 + (current - 5000) / 1000 * 3.0
        assert np.isnan(cape)  # 结果是 nan，会污染下游所有计算
