"""阶段5 回归：共享报告模型 + 消除报告层「参与风险」。

覆盖交接单「五、建议测试」#7 + 阶段5「必须消除」清单：
  - MD 与 HTML 的关键触发条件/调仓变化来自同一个 ReportModel。
  - HTML 不再 import report_builder 私有函数；report_editor 不反向 import report_builder。
  - MD/HTML 仓位档位表同源于 POSITION_TIERS（不再各自硬编码）。
  - 渲染章节函数不读数据库/配置/快照（IO 收敛到入口适配器 + build_report_model 之外）。
"""
import html as _html
import inspect
from pathlib import Path

from src.domain.scoring import POSITION_TIERS, tier_allocation_str
from src.reports.report_model import (
    build_backtest_view, build_report_model, ReportModel, signal_threshold_rows,
)
from tests._report_fixtures import make_signal as _signal, make_portfolio as _portfolio

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _model(prev=None):
    sig = _signal()
    p = _portfolio()
    if prev is not None:
        p["previous_portfolio"] = prev
    prov = {"data": {}, "overall_mode": "real", "stale_warnings": []}
    cfg = {"scoring_weights": {}, "strategy_params": {}}
    return sig, p, build_report_model(sig, p, None, None, p.get("previous_portfolio"), prov, cfg)


# ────────────────────────────────────────────────────────────────────
# build_report_model —— 纯组装、字段齐备
# ────────────────────────────────────────────────────────────────────

class TestBuildReportModel:
    def test_returns_model_with_shared_fields(self):
        _sig, _p, m = _model()
        assert isinstance(m, ReportModel)
        assert m.key_conclusions and len(m.key_conclusions) <= 3
        assert m.canonical_triggers          # 无 AI 时退回规则层，非空
        assert m.headline_triggers == m.canonical_triggers[:1]
        assert isinstance(m.primary_contradiction, str) and m.primary_contradiction
        assert m.config == {"scoring_weights": {}, "strategy_params": {}}
        assert m.overall_mode == "real"

    def test_rebalance_change_uses_previous_state(self):
        _sig, _p, m = _model(prev={"core": {"999999": {}}, "satellite": {}})
        # 上期持有 999999、本期没有 → 移除；本期新基 → 新增
        assert "移除" in m.rebalance_change and "999999" in m.rebalance_change

    def test_first_run_rebalance_note(self):
        _sig, _p, m = _model(prev=None)
        assert "首次运行" in m.rebalance_change

    def test_is_pure_no_db(self, monkeypatch):
        """纯组装：即便禁用 DB 连接也能构建模型（证明不读库）。"""
        import src.utils.database as db
        monkeypatch.setattr(db, "get_connection",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("DB touched")))
        _sig, _p, m = _model()
        assert m.key_conclusions

    def test_allocation_shortfall_is_disclosed(self):
        sig = _signal()
        p = _portfolio(
            core_allocation_pct=0,
            satellite_allocation_pct=0,
            cash_allocation_pct=100,
            allocation_shortfall_pct=90,
            core_funds=[],
            satellite_funds=[],
        )
        prov = {"data": {}, "overall_mode": "real", "stale_warnings": []}
        model = build_report_model(
            sig, p, None, None, None, prov,
            {"scoring_weights": {}, "strategy_params": {}},
        )
        assert any("无合格标的" in conclusion for conclusion in model.key_conclusions)


def test_backtest_view_handles_zero_return_and_sorts_factors():
    import pandas as pd

    view = build_backtest_view({
        "strat_metrics": {"annualized_return": 8},
        "ewbh_metrics": {"annualized_return": 5},
        "sp500_metrics": {"annualized_return": 6},
        "signal_stats": pd.DataFrame([{
            "信号": "重仓进取", "出现次数": 2, "SP500次月均收益%": 0,
        }]),
        "factor_attribution": {
            "base_annual_return": 8,
            "factors": {
                "low": {"label": "低", "contribution_pct": -1},
                "high": {"label": "高", "contribution_pct": 2},
            },
        },
    })
    assert view.alpha_equal_weight == 3
    assert view.signal_rows[0][3] == "✗ 失效"
    assert [row["label"] for row in view.factor_rows] == ["高", "低"]


# ────────────────────────────────────────────────────────────────────
# 仓位档位表同源 POSITION_TIERS
# ────────────────────────────────────────────────────────────────────

def test_signal_threshold_rows_from_position_tiers():
    rows = dict(signal_threshold_rows())
    for tier in ("重仓进取", "标配稳健", "谨慎防守", "减仓防守"):
        alloc = tier_allocation_str(tier)
        assert any(tier in cond_desc and alloc in cond_desc
                   for cond_desc in rows.values()), f"{tier} 档位未取自 POSITION_TIERS"
    # 与 POSITION_TIERS 真实数值一致（防硬编码漂移）
    c, s, h = POSITION_TIERS["重仓进取"]
    assert f"核心{c*100:.0f}%/卫星{s*100:.0f}%/现金{h*100:.0f}%" in tier_allocation_str("重仓进取")


# ────────────────────────────────────────────────────────────────────
# #7：HTML 主报告的触发/调仓/档位表忠实渲染自同一个 ReportModel
# （主报告已仅 HTML，MD 孪生废止；单一真相源从「MD≡HTML」变为「ReportModel→HTML」）
# ────────────────────────────────────────────────────────────────────

class TestHtmlSingleSource:
    def _render(self, tmp_path):
        from src.reports.html_report_builder import build_html_report
        sig = _signal()
        p = _portfolio()
        p["previous_portfolio"] = {"core": {"999999": {}}, "satellite": {}}
        ht = build_html_report(sig, p, output_dir=str(tmp_path)).read_text(encoding="utf-8")
        prov = {"data": {}, "overall_mode": "real", "stale_warnings": []}
        m = build_report_model(sig, p, None, None, p["previous_portfolio"], prov,
                               {"scoring_weights": {}, "strategy_params": {}})
        return ht, m

    def test_triggers_from_model(self, tmp_path):
        ht, m = self._render(tmp_path)
        assert m.canonical_triggers
        for t in m.canonical_triggers:
            assert _html.escape(t) in ht, f"HTML 缺触发（转义后）：{t}"

    def test_rebalance_change_rendered(self, tmp_path):
        ht, _m = self._render(tmp_path)
        assert "999999" in ht

    def test_position_tier_table_rendered(self, tmp_path):
        ht, _m = self._render(tmp_path)
        alloc = tier_allocation_str("重仓进取")  # 核心70%/卫星25%/现金5%
        assert alloc in ht


# ────────────────────────────────────────────────────────────────────
# 消除耦合：跨文件私有 import / 反向 import / 渲染层 IO
# ────────────────────────────────────────────────────────────────────

def test_html_does_not_import_report_builder():
    src = (_REPO_ROOT / "src" / "reports" / "html_report_builder.py").read_text(encoding="utf-8")
    # 只检查真实的 import 语句行（排除注释/文档串里对 report_builder 的文字提及）
    import_lines = [ln.strip() for ln in src.splitlines()
                    if ln.strip().startswith(("from ", "import "))]
    assert not any("report_builder" in ln for ln in import_lines)


def test_report_editor_does_not_import_report_builder():
    src = (_REPO_ROOT / "src" / "reports" / "report_editor.py").read_text(encoding="utf-8")
    assert "from .report_builder import" not in src


def test_report_layer_does_not_read_snapshot_file():
    for f in ("report_builder.py", "html_report_builder.py", "report_model.py", "report_editor.py"):
        src = (_REPO_ROOT / "src" / "reports" / f).read_text(encoding="utf-8")
        assert "portfolio_snapshot.json" not in src, f"{f} 仍直接读快照文件"


def test_render_helpers_do_not_read_config_or_db():
    """HTML 主报告渲染章节不读配置/数据库（IO 只在入口适配器 build_html_report）。"""
    from src.reports import html_report_builder as hb

    for fn in (hb._section_appendix, hb._section_data_quality, hb._render,
               hb._section_market, hb._section_funds):
        src = inspect.getsource(fn)
        assert "load_config" not in src, f"{fn.__name__} 仍读配置"
        assert "read_table" not in src and "get_connection" not in src, f"{fn.__name__} 仍读库"
