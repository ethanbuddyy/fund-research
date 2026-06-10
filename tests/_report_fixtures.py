"""报告层测试共享夹具（非 test_ 文件，pytest 不收集为用例）。

主报告自 2026-06 起仅 HTML，故六因子表/三层结构/审查门等不变量回归改打在
html_report_builder 上；report_editor 与单基金报告辅助函数仍由 test_report_builder 守。
两边共用同一份 signal/portfolio，集中在此避免漂移。
"""


def make_signal() -> dict:
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


def make_portfolio(**over) -> dict:
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


def make_ai_portfolio() -> dict:
    return make_portfolio(ai_decision={
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
