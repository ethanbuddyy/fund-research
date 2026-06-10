"""阶段1/2 回归：组合状态所有权统一 + 最终信号只落库一次。

覆盖交接单「五、建议测试」中的：
  1. 止损触发后，数据库信号等于 run_update() 返回信号。
  2. 止损仓位来自 POSITION_TIERS，不是编排层硬编码。
  3. 组合构建不会在报告对比前覆盖上期快照。
  4. 上期 A/B、本期 B/C 时，报告显示新增 C、移除 A。
  5. 损坏快照不会静默吞掉，并且主流程可继续。
另含 apply_stop_loss 纯函数性质与 portfolio_state_store 读写/损坏回退。
"""
import json

import pandas as pd
import pytest

from src.domain.scoring import POSITION_TIERS


# ────────────────────────────────────────────────────────────────────
# apply_stop_loss —— 纯函数：取自 POSITION_TIERS，不原地修改
# ────────────────────────────────────────────────────────────────────

class TestApplyStopLoss:
    def _base_signal(self):
        return {
            "composite_signal": "标配稳健", "signal_color": "blue",
            "core_allocation": 0.60, "satellite_allocation": 0.30,
            "cash_allocation": 0.10,
        }

    def test_triggered_uses_position_tiers(self):
        from src.recommender.signals import apply_stop_loss
        sig = self._base_signal()
        out = apply_stop_loss(sig, {"triggered": True, "drawdown_pct": 20.0})

        core, sat, cash = POSITION_TIERS["减仓防守"]
        assert out["composite_signal"] == "减仓防守"
        assert (out["core_allocation"], out["satellite_allocation"],
                out["cash_allocation"]) == (core, sat, cash)
        assert out["stop_loss_triggered"] is True
        # 原对象不被修改（纯函数）
        assert sig["core_allocation"] == 0.60
        assert "stop_loss_triggered" not in sig

    def test_not_triggered_preserves_original(self):
        from src.recommender.signals import apply_stop_loss
        sig = self._base_signal()
        out = apply_stop_loss(sig, {"triggered": False})
        assert out["composite_signal"] == "标配稳健"
        assert out["core_allocation"] == 0.60
        assert out["stop_loss"] == {"triggered": False}
        assert not out.get("stop_loss_triggered")

    def test_none_info_sets_null_stop_loss(self):
        from src.recommender.signals import apply_stop_loss
        out = apply_stop_loss(self._base_signal(), None)
        assert out["stop_loss"] is None
        assert not out.get("stop_loss_triggered")


# ────────────────────────────────────────────────────────────────────
# portfolio_state_store —— 唯一读写入口：首次/往返/损坏回退
# ────────────────────────────────────────────────────────────────────

class TestPortfolioStateStore:
    def _patch_paths(self, monkeypatch, tmp_path):
        import src.utils.portfolio_state_store as store
        monkeypatch.setattr(store, "_SNAPSHOT_PATH", tmp_path / "snap.json")
        monkeypatch.setattr(store, "_NAV_PATH", tmp_path / "nav.json")
        monkeypatch.setattr(store, "_RUNTIME_PATH", tmp_path / "runtime.json")
        return store

    def test_first_run_returns_defaults(self, monkeypatch, tmp_path):
        store = self._patch_paths(monkeypatch, tmp_path)
        assert store.load_previous_portfolio() is None
        assert store.load_nav_state() == {"nav": 100.0, "hwm": 100.0}

    def test_roundtrip(self, monkeypatch, tmp_path):
        store = self._patch_paths(monkeypatch, tmp_path)
        store.save_current_portfolio({"core": {"A": {"score": 1.0}}, "satellite": {}})
        loaded = store.load_previous_portfolio()
        assert loaded["core"] == {"A": {"score": 1.0}}

        store.save_nav_state(105.0, 110.0)
        assert store.load_nav_state() == {"nav": 105.0, "hwm": 110.0}

    def test_atomic_runtime_commit_updates_snapshot_and_nav_together(
        self, monkeypatch, tmp_path
    ):
        store = self._patch_paths(monkeypatch, tmp_path)
        snapshot = {
            "date": "2026-06-10",
            "core": {"A": {"score": 80.0}},
            "satellite": {},
        }

        assert store.commit_runtime_state(
            snapshot, {"nav": 95.0, "hwm": 110.0}
        )
        assert store.load_previous_portfolio() == snapshot
        assert store.load_nav_state() == {"nav": 95.0, "hwm": 110.0}

    def test_corrupt_snapshot_warns_not_silent(self, monkeypatch, tmp_path, capsys):
        store = self._patch_paths(monkeypatch, tmp_path)
        (tmp_path / "snap.json").write_text("{ this is broken", encoding="utf-8")
        assert store.load_previous_portfolio() is None
        out = capsys.readouterr().out
        assert "WARN" in out and "损坏" in out, "损坏快照必须发声，不得静默吞掉"

    def test_corrupt_nav_warns_and_resets(self, monkeypatch, tmp_path, capsys):
        store = self._patch_paths(monkeypatch, tmp_path)
        (tmp_path / "nav.json").write_text("not json", encoding="utf-8")
        assert store.load_nav_state() == {"nav": 100.0, "hwm": 100.0}
        out = capsys.readouterr().out
        assert "WARN" in out and "损坏" in out


# ────────────────────────────────────────────────────────────────────
# build 不写盘（快照提交延后到编排层）+ 报告换仓对比用内存上期
# ────────────────────────────────────────────────────────────────────

def _scores_df():
    return pd.DataFrame([
        {"fund_code": "513100", "fund_name": "纳斯达克100ETF(华夏)", "total_score": 82.0,
         "signal": "买入", "performance_score": 8.0, "risk_score": 7.5,
         "strategy_score": 7.0, "consistency_score": 6.5, "cost_score": 9.0},
        {"fund_code": "513500", "fund_name": "标普500ETF(南方)", "total_score": 78.0,
         "signal": "买入", "performance_score": 7.5, "risk_score": 7.8,
         "strategy_score": 7.2, "consistency_score": 6.8, "cost_score": 9.0},
    ])


def _fund_list_df():
    return pd.DataFrame([
        {"fund_code": "513100", "fund_name": "纳斯达克100ETF(华夏)", "fund_type": "ETF",
         "expense_ratio": 0.006},
        {"fund_code": "513500", "fund_name": "标普500ETF(南方)", "fund_type": "ETF",
         "expense_ratio": 0.006},
    ])


def test_build_does_not_write_snapshot_returns_payload(monkeypatch, tmp_path):
    import src.recommender.portfolio as portfolio
    import src.utils.portfolio_state_store as store

    def fake_read_table(table, where="", params=()):
        if table == "fund_scores":
            return _scores_df()
        if table == "fund_list":
            return _fund_list_df()
        return pd.DataFrame()

    monkeypatch.setattr(portfolio, "read_table", fake_read_table)
    monkeypatch.setattr(portfolio, "load_config",
                        lambda: {"rebalancing": {"score_threshold": 10},
                                 "ai_analysis": {"enabled": False}})
    monkeypatch.setattr(portfolio, "_get_latest_navs", lambda codes: {})
    # 即便把 store 路径指向 tmp，build 也绝不应在此创建文件
    monkeypatch.setattr(store, "_SNAPSHOT_PATH", tmp_path / "snap.json")

    signal = {"composite_signal": "标配稳健", "core_allocation": 0.60,
              "satellite_allocation": 0.30, "cash_allocation": 0.10}
    result = portfolio.build_portfolio_recommendation(signal)

    assert not (tmp_path / "snap.json").exists(), "build 不得在报告对比前写快照"
    assert "snapshot_payload" in result
    assert "core" in result["snapshot_payload"]


def test_change_note_uses_in_memory_previous():
    """上期 A/B、本期 B/C → 报告显示新增 C、移除 A，且不读盘。"""
    from src.reports.report_builder import _snapshot_change_note
    portfolio = {
        "core_funds": [{"fund_code": "B"}],
        "satellite_funds": [{"fund_code": "C"}],
        "previous_portfolio": {"core": {"A": {}}, "satellite": {"B": {}}},
    }
    note = _snapshot_change_note(portfolio)
    assert "新增" in note and "C" in note
    assert "移除" in note and "A" in note


def test_change_note_first_run_no_previous():
    from src.reports.report_builder import _snapshot_change_note
    note = _snapshot_change_note({"core_funds": [], "satellite_funds": [],
                                  "previous_portfolio": None})
    assert "首次运行" in note


# ────────────────────────────────────────────────────────────────────
# 集成：止损触发后，落库信号 == run_update() 返回信号（同一最终版本）
# ────────────────────────────────────────────────────────────────────

_UPSTREAM_NOOPS = [
    ("src.utils.database", "init_database", lambda *a, **k: None),
    ("src.collectors.macro_collector", "collect_macro_data", lambda *a, **k: None),
    ("src.collectors.global_macro_collector", "collect_global_macro", lambda *a, **k: None),
    ("src.collectors.market_collector", "collect_market_data", lambda *a, **k: None),
    ("src.collectors.fund_screener", "screen_funds", lambda *a, **k: []),
    ("src.collectors.fund_collector", "collect_fund_data", lambda *a, **k: None),
    ("src.collectors.eastmoney_collector", "collect_eastmoney", lambda *a, **k: None),
    ("src.collectors.baostock_etf_collector", "collect_etf_nav", lambda *a, **k: 0),
    ("src.collectors.fund_fee_collector", "collect_fund_fees", lambda *a, **k: None),
    ("src.collectors.valuation_collector", "collect_valuation_data", lambda *a, **k: None),
    ("src.analyzers.fund_analyzer", "analyze_all_funds", lambda *a, **k: None),
    ("src.recommender.scorer", "score_all_funds", lambda *a, **k: None),
    ("src.retrieval.ingest", "ingest_run", lambda *a, **k: 0),
]


def test_db_signal_equals_returned_signal_after_stop_loss(monkeypatch):
    for mod, attr, fn in _UPSTREAM_NOOPS:
        monkeypatch.setattr(f"{mod}.{attr}", fn)

    monkeypatch.setattr(
        "src.recommender.signals.generate_market_signal",
        lambda *a, **k: {
            "date": "2026-06-09", "composite_signal": "标配稳健", "signal_color": "blue",
            "core_allocation": 0.60, "satellite_allocation": 0.30, "cash_allocation": 0.10,
        },
    )
    monkeypatch.setattr(
        "src.utils.config.load_config",
        lambda *a, **k: {"risk_management": {"stop_loss_pct": 0.15}},
    )
    monkeypatch.setattr(
        "src.utils.portfolio_tracker.update_and_check",
        lambda *a, **k: {"triggered": True, "drawdown_pct": 20.0,
                         "portfolio_nav": 0.80, "high_water_mark": 1.00},
    )
    monkeypatch.setattr(
        "src.recommender.portfolio.build_portfolio_recommendation",
        lambda sig, *a, **k: {"core_funds": [], "satellite_funds": [],
                              "composite_signal": sig.get("composite_signal")},
    )
    monkeypatch.setattr(
        "src.utils.portfolio_state_store.load_previous_portfolio", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "src.utils.portfolio_state_store.commit_runtime_state", lambda *a, **k: None
    )
    saved = {}
    monkeypatch.setattr(
        "src.recommender.signals.save_market_signal", lambda s: saved.update(dict(s))
    )

    from src.application.update_pipeline import run_update
    signal, _scores, _portfolio = run_update()

    # 止损后唯一最终版本：落库 == 返回
    assert saved, "save_market_signal 未被调用——最终信号未落库"
    assert saved["composite_signal"] == signal["composite_signal"] == "减仓防守"
    for k in ("core_allocation", "satellite_allocation", "cash_allocation"):
        assert saved[k] == signal[k]
    # 仓位档位取自 POSITION_TIERS（禁止编排层硬编码）
    assert (signal["core_allocation"], signal["satellite_allocation"],
            signal["cash_allocation"]) == POSITION_TIERS["减仓防守"]


def test_runtime_state_committed_after_build(monkeypatch):
    """快照提交发生在组合构建之后（携带 snapshot_payload），且只提交一次。"""
    for mod, attr, fn in _UPSTREAM_NOOPS:
        monkeypatch.setattr(f"{mod}.{attr}", fn)

    monkeypatch.setattr(
        "src.recommender.signals.generate_market_signal",
        lambda *a, **k: {"date": "2026-06-09", "composite_signal": "标配稳健",
                         "core_allocation": 0.60, "satellite_allocation": 0.30,
                         "cash_allocation": 0.10},
    )
    monkeypatch.setattr("src.utils.config.load_config", lambda *a, **k: {})  # 止损关闭
    monkeypatch.setattr(
        "src.recommender.portfolio.build_portfolio_recommendation",
        lambda sig, *a, **k: {"core_funds": [], "satellite_funds": [],
                              "snapshot_payload": {"core": {"X": {}}, "satellite": {}}},
    )
    monkeypatch.setattr("src.recommender.signals.save_market_signal", lambda *a, **k: None)
    monkeypatch.setattr(
        "src.utils.portfolio_state_store.load_previous_portfolio", lambda *a, **k: None
    )
    committed = []
    monkeypatch.setattr(
        "src.utils.portfolio_state_store.commit_runtime_state",
        lambda snap, nav=None: committed.append((snap, nav)),
    )

    from src.application.update_pipeline import run_update
    run_update()
    assert committed == [
        ({"core": {"X": {}}, "satellite": {}}, None)
    ], "本期快照与止损净值应在 build 之后恰好提交一次"
