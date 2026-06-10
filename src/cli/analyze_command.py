"""单基金综合研判子命令：逻辑调度 + 终端输出。"""
import os
from typing import Optional, cast

from ..domain.types import MarketSignal


def run_analyze(query: str) -> None:
    from ..analysis.fund_deep_analysis import analyze_fund
    from ..analysis.fund_lookup import search_funds, resolve_fund_code
    from ..holdings.checker import load_signal_from_db
    from ..reports.report_builder import build_fund_report
    from ..collectors.on_demand_collector import fetch_on_demand

    fund_code = resolve_fund_code(query)

    if fund_code is None:
        hits = search_funds(query)
        if not hits:
            print(f"[研判] 未找到基金「{query}」。请确认代码或关键词，或先运行 python3 run.py 更新数据库。")
            return
        if len(hits) > 1:
            print(f"[研判] 「{query}」匹配到多只基金，请指定代码：")
            for h in hits[:8]:
                print(f"  {h['fund_code']}  {h['fund_name']}")
            return
        fund_code = hits[0]["fund_code"]

    print(f"[研判] 正在分析基金 {fund_code} ...")
    fetch_on_demand(fund_code)
    market_signal = load_signal_from_db()
    result = analyze_fund(fund_code, cast(Optional[MarketSignal], market_signal or None))
    print_analysis(result)

    try:
        path = build_fund_report(result)
        print(f"\n[报告] 研判报告已生成：{path}")
    except Exception as e:
        print(f"\n[警告] Markdown 报告生成失败：{e}")

    # 研判结论 + 地区展望沉淀进检索库（fail-soft）
    try:
        from ..retrieval.ingest import ingest_fund_analysis
        ingest_fund_analysis(result)
    except Exception as e:
        print(f"[检索] 研判语料沉淀跳过（不影响主流程）：{e}")


_EMOJI = {"重仓进取": "🟢", "标配稳健": "🔵", "谨慎防守": "🟠", "减仓防守": "🔴"}
_GRADE_EMOJI = {
    "优质候选": "🟢", "合格候选": "🔵", "有明显短板": "🟡",
    "不建议配置": "🟠", "剔除": "🔴",
}
_LABEL_EMOJI = {"强势": "🟢", "偏强": "🔵", "中性": "🟡", "偏弱": "🟠", "弱势": "🔴"}
_DIM_NAMES = {
    "performance": ("业绩质量", 20), "risk": ("风险控制", 20),
    "manager": ("基金经理", 15), "strategy": ("策略稳定", 15),
    "attribution": ("收益归因", 10), "structure": ("规模流动", 10),
    "cost": ("费用成本", 10),
}


def _m(v, fmt=".2f") -> str:
    """NaN/None 安全的终端数值格式化。"""
    return f"{v:{fmt}}" if v is not None and not (isinstance(v, float) and v != v) else "—"


def print_analysis(result: dict) -> None:
    """终端打印 9 模块研判摘要（每模块由一个 _print_* 子函数负责，便于阅读/维护）。"""
    info   = result["fund_info"]
    concl  = result["conclusion"]
    sig_info = concl.get("fit_signal") or {}

    name = info.get("fund_name", result["fund_code"])
    sig_str = ""
    if sig_info:
        sig_str = f"  [{_EMOJI.get(sig_info['composite_signal'], '⚪')} {sig_info['composite_signal']}]"

    print()
    print("═" * 60)
    print(f"  单基金综合研判：{name} [{result['fund_code']}]")
    print(f"  {info.get('asset_class', '')}  基准：{info.get('benchmark', '—')}  地区：{info.get('region', '—')}{sig_str}")
    print("═" * 60)

    _print_overview(info)
    _print_scores(result["scores"], concl)
    _print_vetoes(result["vetoes"])
    _print_metrics(result["performance"], result["advanced_metrics"])
    _print_risk(result["performance"], result["advanced_metrics"], result["peer_context"])
    _print_holdings(result["holdings"])
    _print_attribution(result["advanced_metrics"])
    _print_conclusion(concl, sig_info)
    _print_region_outlook(result.get("region_outlook"))

    print()
    print("  注：以 SP500 为代理基准计算 alpha/beta/IR，QDII 应以其实际基准（如纳斯达克100）为准。")
    print("  结论仅供参考，不构成投资建议。")
    print("═" * 60)


def _print_overview(info: dict) -> None:
    print("\n【一】产品概况")
    inception = info.get("inception_date") or "—"
    tenure = info.get("tenure_years")
    tenure_str = f"{tenure:.1f} 年（代理）" if tenure else "—"
    aum = info.get("total_assets")
    aum_str = f"{aum/1e8:.1f} 亿" if aum and aum > 0 else "—"
    er = info.get("expense_ratio")
    er_str = f"{er*100:.2f}%" if er else "—"
    print(f"  成立日期：{inception}  |  成立年限：{tenure_str}  |  规模：{aum_str}")
    print(f"  费率：{er_str}  |  类型：{info.get('fund_type', '—')}")


def _print_scores(scores: dict, concl: dict) -> None:
    total = scores["total"]
    grade = concl["grade"]
    grade_emoji = _GRADE_EMOJI.get(grade, "⚪")
    print(f"\n【二】综合评分  →  {grade_emoji} {total:.1f} / 100  【{grade}】")
    print(f"  {'维度':<10}  {'得分':>6}  {'满分':>4}  {'数据'}")
    print(f"  {'─'*10}  {'─'*6}  {'─'*4}  {'─'*10}")
    for key, (label, max_s) in _DIM_NAMES.items():
        s = scores.get(key, {})
        raw = s.get("score", "—")
        covers = {d.get("coverage", "?") for d in (s.get("details") or {}).values()}
        cover_str = "✅" if covers <= {"COMPUTED"} else "⚠️ 部分" if "UNAVAILABLE" in covers else "~"
        print(f"  {label:<10}  {raw:>6.1f}  {max_s:>4}  {cover_str}")
    print(f"  {'─'*10}  {'─'*6}")
    print(f"  {'合计':<10}  {total:>6.1f}  {'100':>4}")


def _print_vetoes(vetoes: list) -> None:
    print("\n【三】一票否决检查")
    if not vetoes:
        print("  ✅ 无触发")
        return
    for v in vetoes:
        emoji = "🚨" if v["severity"] == "hard" else "⚠️"
        print(f"  {emoji} [{v['id']}] {v['condition']}：{v['detail']}")


def _print_metrics(perf: dict, adv: dict) -> None:
    print("\n【四】关键指标")
    print(f"  近1年收益：{_m(perf.get('return_1y'))}%  "
          f"近3年收益：{_m(perf.get('return_3y'))}%  "
          f"近5年收益：{_m(perf.get('return_5y'))}%")
    print(f"  年化收益：{_m(perf.get('annualized_return'))}%  "
          f"最大回撤：{_m(perf.get('max_drawdown'))}%  "
          f"波动率：{_m(perf.get('volatility'))}%")
    print(f"  夏普比率：{_m(perf.get('sharpe_ratio'))}  "
          f"卡玛比率：{_m(adv.get('calmar_ratio'))}  "
          f"下行捕获：{_m(adv.get('downside_capture'))}")
    print(f"  年化Alpha：{_m(adv.get('alpha_annual'))}%  "
          f"Beta：{_m(adv.get('beta'))}  "
          f"IR：{_m(adv.get('information_ratio'))}")
    rwr = adv.get("rolling_win_rate")
    rwr_str = f"{rwr*100:.1f}%" if rwr and rwr == rwr else "—"
    print(f"  滚动3年胜率：{rwr_str}  "
          f"（基于 {adv.get('data_months', 0)} 个月数据，代理基准：SP500）")


def _print_risk(perf: dict, adv: dict, peer: dict) -> None:
    print("\n【五】风险特征")
    peer_dd = (peer.get("stats") or {}).get("max_drawdown", {}).get("mean")
    peer_dd_str = f"{peer_dd:.1f}%" if peer_dd else "—"
    print(f"  最大回撤 {_m(perf.get('max_drawdown'))}%  vs  同类均值 {peer_dd_str}")
    peer_sharpe = (peer.get("stats") or {}).get("sharpe_ratio", {}).get("median")
    print(f"  夏普 {_m(perf.get('sharpe_ratio'))}  vs  同类中位数 {_m(peer_sharpe)}")
    dc = adv.get("downside_capture")
    if dc is not None:
        dc_label = "优" if dc <= 0.7 else "良" if dc <= 0.85 else "一般" if dc <= 1.0 else "差"
        print(f"  下行捕获率 {dc:.3f}（{dc_label}）—— 市场下跌期间本基金的跌幅 / 市场跌幅")


def _print_holdings(hold: dict | None) -> None:
    print("\n【六】持仓穿透")
    if not hold:
        print("  暂无持仓穿透数据（可运行 python3 run.py 更新）")
        return
    sr = hold.get("stock_ratio")
    br = hold.get("bond_ratio")
    cr = hold.get("cash_ratio")
    print(f"  持仓日期：{hold.get('date', '—')}")
    print(f"  股票 {sr:.1f}%  债券 {br:.1f}%  现金 {cr:.1f}%" if all(v is not None for v in [sr, br, cr]) else "  （持仓比例数据不全）")
    codes = hold.get("stock_codes", "")
    if codes:
        all_codes = [c.strip() for c in str(codes).split(",") if c.strip()]
        print(f"  前5大持仓代码：{', '.join(all_codes[:5])}（共 {len(all_codes)} 只）")


def _print_attribution(adv: dict) -> None:
    print("\n【七】收益归因（代理基准：SP500）")
    beta = adv.get("beta")
    if beta is None:
        print("  （NAV 历史数据不足，无法计算；建议先运行 python3 run.py 更新数据）")
        return
    r2 = adv.get("r_squared")
    print(f"  Beta={_m(beta)}  Alpha年化={_m(adv.get('alpha_annual'))}%  R²={_m(r2)}  IR={_m(adv.get('information_ratio'))}")
    r2_pct = r2 * 100 if r2 else 0
    print(f"  市场 beta 解释收益 {r2_pct:.1f}%，其余 {100-r2_pct:.1f}% 来自风格/选基/择时")


def _print_conclusion(concl: dict, sig_info: dict) -> None:
    print("\n【八】配置结论")
    print(f"  {concl['summary']}")
    if sig_info:
        strat_s = sig_info.get("strategy_match_score", 0)
        assess = sig_info.get("assessment", "")
        print(f"  当前市场信号适配：{strat_s:.1f}/10（{assess}）")


def _print_region_outlook(ro: dict | None) -> None:
    if not ro:
        return
    print("\n【九】地区宏观机会评估（4地区横向对比）")
    cov = ro.get("covered_regions", {})
    ranking = ro.get("ranking", [])
    print(f"  {'地区':<10} {'综合':>5}  {'宏观':>5}  {'动量':>5}  {'相对':>5}  {'标签':<6}  {'GDP%':>6}  {'通胀%':>6}  {'近1年':>7}  {'vs美国3年':>9}")
    print(f"  {'─'*10} {'─'*5}  {'─'*5}  {'─'*5}  {'─'*5}  {'─'*6}  {'─'*6}  {'─'*6}  {'─'*7}  {'─'*9}")
    for rk in (ranking if ranking else list(cov.keys())):
        d = cov.get(rk)
        if not d:
            continue
        emoji = _LABEL_EMOJI.get(d["label"], "⚪")
        gdp_s   = f"{d['gdp_growth']:+.1f}" if d.get("gdp_growth") is not None else "  —"
        infl_s  = f"{d['inflation']:+.1f}"  if d.get("inflation")  is not None else "  —"
        r1_s    = f"{d['return_1y']:+.1f}%" if d.get("return_1y")  is not None else "   —"
        vs3_s   = f"{d['vs_us_3y']:+.1f}%"  if d.get("vs_us_3y")  is not None else "      —"
        print(f"  {rk:<10} {d['total']:>5.1f}  {d['macro_score']:>5.1f}  {d['momentum_score']:>5.1f}  {d['relative_score']:>5.1f}  {emoji}{d['label']:<5}  {gdp_s:>6}  {infl_s:>6}  {r1_s:>7}  {vs3_s:>9}")

    focus = ro.get("focus_region", {})
    if focus.get("summary"):
        print(f"\n  本基金地区（{focus.get('name','—')}）：{focus.get('label','—')}（{focus.get('score','—')}/10）")
        print(f"  · {focus['summary']}")

    for n in ro.get("data_notes", [])[:3]:
        print(f"  ⚠️  {n}")
