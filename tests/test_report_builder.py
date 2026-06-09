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


# ─────────────────────────────────────────────────────────────
# A 类正确性回归(2026-06 报告层洗澡)——这些缺口是 bug 长期无报警的原因
# ─────────────────────────────────────────────────────────────

from src.domain.factor_config import FACTOR_WEIGHTS


def _signal():
    return {
        "date": "2026-06-09", "composite_signal": "标配稳健", "timing_score": 5.96,
        "macro_cycle": "扩张", "valuation_level": "极度高估", "cape": 41.57, "vix": 20.3,
        "macro": {"cycle": "扩张", "cycle_score": 8}, "valuation": {"valuation_score": 1.0},
        "sentiment": {"score": 63, "label": "贪婪"}, "trend_score": 8.0, "credit_score": 8.0,
        "global_macro_score": 6.0, "macro_adj": 8.0, "fed_direction": 0.0,
        "global_macro": {"available": True, "strongest": "中国", "weakest": "欧元区",
                         "regions": {"中国": {"gdp_growth": 5.0, "inflation": 0.2, "score": 7, "label": "温和扩张"}}},
        "narrative": "n",
    }


def _portfolio(**over):
    p = {
        "composite_signal": "标配稳健", "core_allocation_pct": 60,
        "satellite_allocation_pct": 30, "cash_allocation_pct": 10, "score_threshold": 10,
        "core_funds": [
            {"fund_code": "012921", "fund_name": "易方达全球成长", "role": "核心", "weight": 20.0,
             "total_score": 84.3, "expense_ratio": 0.014, "signal": "买入"},
            {"fund_code": "161130", "fund_name": "标普500指数LOF", "role": "核心", "weight": 40.0,
             "total_score": 74.5, "expense_ratio": 0.006, "signal": "持有"},
        ],
        "satellite_funds": [
            {"fund_code": "270023", "fund_name": "广发全球精选", "role": "卫星", "weight": 15.0,
             "total_score": 69.8, "expense_ratio": 0.014, "signal": "持有"},
        ],
        "top_picks": [
            {"fund_code": "006479", "fund_name": "易方达标普科技", "total_score": 84.4},
            {"fund_code": "539002", "fund_name": "建信新兴市场", "total_score": 60.0},
        ],
    }
    p.update(over)
    return p


class TestA1SixFactorTable:
    def test_table_has_six_factors_including_global_macro(self):
        out = rb._s3_market_theme(_signal())
        assert "六因子得分" in out
        assert "五因子" not in out
        assert "| 全球宏观 |" in out

    def test_weights_come_from_factor_config_not_hardcoded(self):
        out = rb._s3_market_theme(_signal())
        # 趋势 27% / 情绪 13.5% 必须出现(取自 FACTOR_WEIGHTS),旧硬编码 30%/15% 不应再现于因子行
        assert f"{FACTOR_WEIGHTS['trend']*100:g}%" in out      # 27%
        assert f"{FACTOR_WEIGHTS['sentiment']*100:g}%" in out  # 13.5%

    def test_contributions_reconcile_to_composite(self):
        # 六因子加权贡献之和应≈综合分(同源于 signals.py 的 composite_raw)
        s = _signal()
        contrib = (
            s["macro_adj"] * FACTOR_WEIGHTS["macro"]
            + s["valuation"]["valuation_score"] * FACTOR_WEIGHTS["valuation"]
            + (10 - s["sentiment"]["score"] / 10) * FACTOR_WEIGHTS["sentiment"]
            + s["trend_score"] * FACTOR_WEIGHTS["trend"]
            + s["credit_score"] * FACTOR_WEIGHTS["credit"]
            + s["global_macro_score"] * FACTOR_WEIGHTS["global_macro"]
        )
        assert abs(contrib - s["timing_score"]) < 0.05


class TestA2ReviewGate:
    def test_banner_empty_when_sound(self):
        p = _portfolio(adversarial_review={"overall_verdict": "sound", "findings": []})
        assert rb.review_banner(p) == ""

    def test_banner_surfaces_nonsound_verdict(self):
        p = _portfolio(adversarial_review={
            "overall_verdict": "minor_concerns", "summary": "卫星档位与清仓矛盾",
            "findings": [{"severity": "medium", "category": "internal_inconsistency",
                          "issue": "档位要求20%却清仓", "suggested_fix": "改为减仓"}]})
        b = rb.review_banner(p)
        assert "对抗审查" in b and "卫星档位与清仓矛盾" in b
        assert "内部矛盾" in b  # 自相矛盾被单独点名

    def test_action_caveat_lists_conflicts(self):
        p = _portfolio(adversarial_review={
            "overall_verdict": "minor_concerns",
            "findings": [{"severity": "medium", "category": "internal_inconsistency",
                          "issue": "档位要求20%却清仓", "suggested_fix": "改为减仓"}]})
        c = rb.review_action_caveat(p)
        assert "执行前须先复核" in c and "档位要求20%却清仓" in c

    def test_review_banner_precedes_full_review_in_appendix(self):
        # 新三层结构：警示横幅在第一层(决策)，完整审查表收进折叠审计附录
        p = _portfolio(adversarial_review={
            "overall_verdict": "minor_concerns", "summary": "需注意的矛盾",
            "findings": [{"severity": "low", "category": "unsupported_claim", "issue": "y"}]})
        md = rb.build_report(_signal(), p, output_dir="/tmp/_rpt_test")
        text = md.read_text(encoding="utf-8")
        # 横幅在「二、为什么」之前(即第一层决策内)
        assert "对抗审查" in text.split("## 二、为什么")[0]
        # 完整审查表在折叠附录内
        assert "AI 对抗审查" in text.split("<details>")[1]


class TestA3AlternatesReason:
    def test_no_name_guessing(self):
        out = rb._s6_alternates(_portfolio(), _signal())
        assert "宽基已满3席" not in out
        assert "未入选原因" in out

    def test_reason_derived_from_scores(self):
        out = rb._s6_alternates(_portfolio(), _signal())
        # 006479(84.4)>在持最低 69.8 → 被门槛挡;539002(60.0)<69.8 → 评分低于已入选
        assert "未超在持最低分" in out
        assert "综合分低于已入选" in out


class TestA4WeightedFee:
    def test_weighted_average_differs_from_simple(self):
        # 012921 1.4%@20% + 161130 0.6%@40% + 270023 1.4%@15%
        out = rb._s7_exposure_risk(_portfolio(), _signal())
        assert "加权平均管理费" in out  # 有有效权重 → 标注为加权
        # 加权 = (1.4*20+0.6*40+1.4*15)/75 = (28+24+21)/75 = 0.9733%
        assert "0.97%" in out

    def test_falls_back_to_equal_weight_label(self):
        p = _portfolio()
        for f in p["core_funds"] + p["satellite_funds"]:
            f["weight"] = 0
        out = rb._s7_exposure_risk(p, _signal())
        assert "等权" in out


class TestA5SilentLossVoiced:
    def test_missing_rationale_voiced_when_ai_present(self):
        rmap = {"012921": {"cycle_fit": "ok", "risk_note": "r"}}  # 270023 缺失
        row = rb._fund_row({"fund_code": "270023", "fund_name": "广发", "weight": 15.0}, rmap)
        assert "未生成该基金理由" in row

    def test_no_false_alarm_when_ai_off(self):
        # rationale_map 全空(AI 关闭)→ 不应误报,留「—」
        row = rb._fund_row({"fund_code": "270023", "fund_name": "广发", "weight": 15.0}, {})
        assert "未生成该基金理由" not in row


class TestA6Copy:
    def test_no_duplicate_rate_word(self):
        out = "\n".join(rb._key_conclusions(_signal(), _portfolio()))
        assert "利率利率" not in out


# ─────────────────────────────────────────────────────────────
# B/C 三层结构重构回归
# ─────────────────────────────────────────────────────────────

from src.reports import report_editor as ed


def _ai_portfolio():
    return _portfolio(ai_decision={
        "portfolio_thesis": "晚期扩张组合论点。",
        "fund_rationales": [
            {"fund_code": "012921", "cycle_fit": "全球分散", "risk_note": "费率高", "conviction_level": "high"},
            {"fund_code": "161130", "cycle_fit": "压舱", "risk_note": "纯美股", "conviction_level": "medium"},
        ],
        "rebalance_triggers": [
            {"condition": "VIX突破25且连续3日", "action": "切换谨慎防守"},
            {"condition": "核心PCE反弹至3.5%以上", "action": "减161130"},
        ],
        "scenario_analysis": {
            "bull_case": {"trigger": "VIX回落16以下", "target_tier": "重仓进取", "fund_actions": "增持012921"},
            "base_case": {"trigger": "VIX维持16-22", "target_tier": "标配稳健", "fund_actions": "维持"},
            "bear_case": {"trigger": "VIX突破25", "target_tier": "谨慎防守", "fund_actions": "清仓卫星"},
        },
    })


class TestEditor:
    def test_dedupe_keeps_order(self):
        assert ed.dedupe_keep_order(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]

    def test_canonical_prefers_ai_triggers(self):
        out = ed.canonical_triggers(_signal(), _ai_portfolio())
        assert any("VIX突破25且连续3日" in t for t in out)
        assert all("**" not in t for t in out)  # 纯文本，MD/HTML 共用

    def test_canonical_falls_back_to_rules_without_ai(self):
        out = ed.canonical_triggers(_signal(), _portfolio())
        assert out  # 无 AI 时退回规则层，非空

    def test_headline_is_subset(self):
        full = ed.canonical_triggers(_signal(), _ai_portfolio())
        head = ed.headline_triggers(_signal(), _ai_portfolio(), 1)
        assert len(head) == 1 and head[0] == full[0]


class TestThreeLayerStructure:
    def _build(self, p=None):
        p = p or _ai_portfolio()
        path = rb.build_report(_signal(), p, output_dir="/tmp/_rpt_bc")
        return path.read_text(encoding="utf-8")

    def test_four_layers_present_in_order(self):
        t = self._build()
        i1 = t.index("## 一、本期决策")
        i2 = t.index("## 二、为什么")
        i3 = t.index("## 三、买什么·卖什么")
        i4 = t.index("## 四、何时改变")
        assert i1 < i2 < i3 < i4

    def test_audit_content_folded(self):
        t = self._build()
        assert "<details>" in t and "审计附录" in t
        tail = t.split("<details>")[1]
        # 数据可信度 / 备选 / 算法参数 / 通用风险 都进折叠区
        for kw in ["数据可信度", "备选基金", "算法参数", "QDII 通用风险"]:
            assert kw in tail

    def test_triggers_not_duplicated_as_full_list(self):
        # 触发整列只在「四、何时改变」出现一次；首页只放最关键 1 条（teaser）
        t = self._build()
        when = t.split("## 四、何时改变")[1]
        before = t.split("## 四、何时改变")[0]
        # 第二条触发(非首页 teaser 的那条)只在「何时改变」出现，不在它之前重复整列
        assert "核心PCE反弹至3.5%以上" in when
        assert "核心PCE反弹至3.5%以上" not in before

    def test_scenario_table_omits_actions(self):
        t = self._build()
        # 情景表只到目标档位，不含 fund_actions 文本（操作收归何时改变）
        seg = t.split("情景分析")[1].split("各情景")[0]
        assert "清仓卫星" not in seg
        assert "目标档位" in seg

    def test_fund_table_slim_plus_cards(self):
        t = self._build()
        assert "| 代码 | 基金名称 | 角色 | 权重 | 综合分 | 信号 |" in t
        assert "#### 个基研判" in t
        # 旧 15 列宽表表头不应再现
        assert "推荐理由 | 主要风险" not in t

    def test_no_legacy_section_numbers_in_body(self):
        t = self._build()
        for legacy in ["## 五、推荐基金表", "## 七、组合暴露与风险", "## 八、行动计划", "## 十、附录"]:
            assert legacy not in t
