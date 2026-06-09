"""阶段4 回归：窄数据访问仓储 + 应用层不再直接拼 SQL。

覆盖交接单「四、阶段4」验收：
  - scheduler.py 不直接执行 SELECT market_signals（改走 SignalRepository）。
  - portfolio.py 不直接查询最新净值（改走 FundRepository）。
  - SignalRepository / FundRepository 的保存/读取行为正确且可内存测试。
"""
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parent.parent


# ────────────────────────────────────────────────────────────────────
# SignalRepository —— 写回/读最新
# ────────────────────────────────────────────────────────────────────

class TestSignalRepository:
    def _sample_signal(self):
        return {
            "date": "2026-06-09", "macro_cycle": "扩张", "valuation_level": "中性",
            "sentiment_label": "中性", "composite_signal": "标配稳健",
            "cape": 30.0, "sp500_pe": 22.0, "vix": 15.0,
            "buffett_indicator": 1.8, "equity_risk_premium": 0.03,
            "core_allocation": 0.60, "satellite_allocation": 0.30, "cash_allocation": 0.10,
            "data_source": "real",
        }

    def test_save_signal_upserts_to_market_signals(self, monkeypatch):
        import src.utils.signal_repository as repo
        captured = {}

        def fake_upsert(df, table, unique_cols):
            captured["df"] = df.copy()
            captured["table"] = table
            captured["unique_cols"] = list(unique_cols)

        monkeypatch.setattr(repo, "upsert_dataframe", fake_upsert)
        repo.save_signal(self._sample_signal())

        assert captured["table"] == "market_signals"
        assert captured["unique_cols"] == ["date"]
        row = captured["df"].iloc[0]
        assert row["composite_signal"] == "标配稳健"
        assert row["core_allocation"] == 0.60
        assert row["notes"] == "data_source=real"

    def test_load_latest_signal_empty_returns_none(self, monkeypatch):
        import src.utils.signal_repository as repo
        monkeypatch.setattr(repo, "read_table", lambda *a, **k: pd.DataFrame())
        assert repo.load_latest_signal() is None

    def test_load_latest_signal_returns_row_dict(self, monkeypatch):
        import src.utils.signal_repository as repo
        monkeypatch.setattr(
            repo, "read_table",
            lambda *a, **k: pd.DataFrame([{"date": "2026-06-09",
                                           "composite_signal": "减仓防守"}]),
        )
        row = repo.load_latest_signal()
        assert row is not None
        assert row["composite_signal"] == "减仓防守"


# ────────────────────────────────────────────────────────────────────
# FundRepository —— 最新净值读取（解析与降级）
# ────────────────────────────────────────────────────────────────────

class _FakeCursor:
    def __init__(self, val):
        self._val = val

    def fetchone(self):
        return None if self._val is None else [self._val]


class _FakeConn:
    def __init__(self, navs):
        self._navs = navs
        self.closed = False

    def execute(self, sql, params):
        return _FakeCursor(self._navs.get(params[0]))

    def close(self):
        self.closed = True


class TestFundRepository:
    def test_get_latest_navs_parses_rows(self, monkeypatch):
        import src.utils.fund_repository as repo
        fake = _FakeConn({"513100": 1.23, "513500": None})
        monkeypatch.setattr("src.utils.database.get_connection", lambda: fake)

        out = repo.get_latest_navs(["513100", "513500", "164906"])
        assert out == {"513100": 1.23}   # None 与缺失均不入结果
        assert fake.closed is True        # 连接被关闭

    def test_get_latest_navs_error_returns_empty(self, monkeypatch):
        import src.utils.fund_repository as repo

        def boom():
            raise RuntimeError("db down")

        monkeypatch.setattr("src.utils.database.get_connection", boom)
        assert repo.get_latest_navs(["513100"]) == {}


# ────────────────────────────────────────────────────────────────────
# 委托与「应用层不再拼 SQL」不变量
# ────────────────────────────────────────────────────────────────────

def test_portfolio_get_latest_navs_delegates_to_fund_repository(monkeypatch):
    import src.recommender.portfolio as portfolio
    sentinel = {"X": 9.9}
    monkeypatch.setattr("src.utils.fund_repository.get_latest_navs",
                        lambda codes: sentinel)
    assert portfolio._get_latest_navs(["X"]) is sentinel


def test_scheduler_does_not_query_market_signals_directly():
    """验收：scheduler.py 不再出现 market_signals 原始 SQL。"""
    src = (_REPO_ROOT / "scheduler.py").read_text(encoding="utf-8")
    assert "market_signals" not in src


def test_portfolio_does_not_query_nav_history_directly():
    """验收：portfolio.py 不再直接查 fund_nav_history。"""
    src = (_REPO_ROOT / "src" / "recommender" / "portfolio.py").read_text(encoding="utf-8")
    assert "fund_nav_history" not in src


def test_portfolio_tracker_does_not_query_nav_history_directly():
    src = (_REPO_ROOT / "src" / "utils" / "portfolio_tracker.py").read_text(encoding="utf-8")
    assert "fund_nav_history" not in src
