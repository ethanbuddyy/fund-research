"""fund_deep_analysis 纯函数与一票否决（_check_vetoes）测试。

此模块（1048 行、单基金深度评分核心）此前零测试。本文件覆盖：
  - _check_vetoes 各触发条件（含条件3 卡玛比率 f-string 崩溃回归）
  - 评分辅助纯函数（_grade / _annualize_pct / _aum_score / _safe_float / _compute_tenure）
"""
import math

import pytest

from src.analysis import fund_deep_analysis as fda


# ── 一票否决 ──────────────────────────────────────────────────

class TestCheckVetoes:
    def _base(self):
        # 默认无任何否决条件触发的输入
        return dict(
            fund_info={"fund_code": "001", "asset_class": "broad_equity",
                       "tenure_years": 5.0, "expense_ratio": 0.006,
                       "total_assets": 50e8, "stock_ratio": 90},
            perf={"max_drawdown": -20.0, "annualized_return": 8.0},
            adv={"alpha_annual": 1.0, "calmar_ratio": 1.2},
            peer={"stats": {"max_drawdown": {"mean": -22.0}}},
        )

    def test_no_veto_clean_fund(self):
        assert fda._check_vetoes(**self._base()) == []

    def test_tenure_under_1y_triggers(self):
        a = self._base(); a["fund_info"]["tenure_years"] = 0.5
        ids = [v["id"] for v in fda._check_vetoes(**a)]
        assert 1 in ids

    def test_negative_alpha_high_fee_triggers(self):
        a = self._base()
        a["adv"]["alpha_annual"] = -3.0
        a["fund_info"]["expense_ratio"] = 0.015
        ids = [v["id"] for v in fda._check_vetoes(**a)]
        assert 2 in ids

    def test_small_aum_triggers(self):
        a = self._base(); a["fund_info"]["total_assets"] = 1.5e8
        ids = [v["id"] for v in fda._check_vetoes(**a)]
        assert 5 in ids

    def test_name_reality_mismatch_bond_high_stock(self):
        a = self._base()
        a["fund_info"]["asset_class"] = "bond"
        a["fund_info"]["stock_ratio"] = 80
        ids = [v["id"] for v in fda._check_vetoes(**a)]
        assert 4 in ids

    # ── 回归：条件3 卡玛比率 f-string 此前对 calmar=None / float 都会崩溃 ──
    def test_condition3_calmar_none_does_not_crash(self):
        """max_dd 远超同类 + calmar=None：旧代码 f"{None:.2f if ...}" 抛 TypeError。"""
        a = self._base()
        a["perf"]["max_drawdown"] = -50.0      # 远超同类均值 -22 的 1.4 倍
        a["adv"]["calmar_ratio"] = None
        vetoes = fda._check_vetoes(**a)         # 不应抛异常
        v3 = [v for v in vetoes if v["id"] == 3]
        assert v3 and "不可计算" in v3[0]["detail"]

    def test_condition3_calmar_float_formats(self):
        """calmar 为浮点且 < 0.5：旧代码 f"{0.3:.2f if ...}" 抛 ValueError。"""
        a = self._base()
        a["perf"]["max_drawdown"] = -50.0
        a["adv"]["calmar_ratio"] = 0.3
        vetoes = fda._check_vetoes(**a)
        v3 = [v for v in vetoes if v["id"] == 3]
        assert v3 and "0.30" in v3[0]["detail"]


# ── 评分辅助纯函数 ────────────────────────────────────────────

class TestGrade:
    @pytest.mark.parametrize("total,expected", [
        (90, "优质候选"), (85, "优质候选"), (80, "合格候选"),
        (70, "有明显短板"), (55, "不建议配置"), (40, "剔除"),
    ])
    def test_grade(self, total, expected):
        assert fda._grade(total) == expected


class TestAnnualizePct:
    def test_simple_two_year(self):
        # 累计 +21% 两年 ≈ 年化 10%
        assert fda._annualize_pct(21.0, 2) == pytest.approx(10.0, abs=0.05)

    def test_total_loss_floor(self):
        assert fda._annualize_pct(-100.0, 3) == -100.0

    def test_below_total_loss_clamped(self):
        assert fda._annualize_pct(-150.0, 3) == -100.0


class TestSafeFloat:
    @pytest.mark.parametrize("v,expected", [
        (None, None), ("N/A", None), (float("nan"), None),
        ("3.5", 3.5), (4, 4.0),
    ])
    def test_safe_float(self, v, expected):
        out = fda._safe_float(v)
        if expected is None:
            assert out is None
        else:
            assert out == pytest.approx(expected)


class TestAumScore:
    def test_below_min_zero(self):
        assert fda._aum_score(1.0, "broad_equity") == 0.0

    def test_sweet_spot_full(self):
        assert fda._aum_score(50, "growth_equity") == 3

    def test_oversized_sector_penalized(self):
        assert fda._aum_score(200, "growth_equity") == 1


class TestComputeTenure:
    def test_none_input(self):
        assert fda._compute_tenure(None) is None

    def test_garbage_input(self):
        assert fda._compute_tenure("not-a-date") is None

    def test_valid_date_positive(self):
        assert fda._compute_tenure("2015-01-01") > 5
