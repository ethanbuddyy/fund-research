"""一键启动入口：数据采集 → 信号生成 → 基金评分 → 组合推荐 → 投研报告"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    import argparse
    parser = argparse.ArgumentParser(description="基金投资私人幕僚系统")
    parser.add_argument(
        "--backtest", action="store_true",
        help="生成报告时附带走向前回测分析（约需 1–2 分钟，结果注入报告第九章）",
    )
    parser.add_argument(
        "--analyze", metavar="FUND",
        help="单基金综合研判（支持代码或名称关键词），如：--analyze 513100 或 --analyze 纳斯达克",
    )
    parser.add_argument(
        "--search", metavar="QUERY",
        help="搜索基金代码，如：--search 纳斯达克  或  --search 标普500",
    )
    parser.add_argument(
        "--check-holdings", metavar="SOURCE", nargs="?", const="config/my_holdings.yaml",
        help=(
            "持仓健康诊断模式。"
            "可传 YAML 文件路径（默认 config/my_holdings.yaml）"
            "或内联格式 'code1:weight1,code2:weight2,...'"
        ),
    )
    args = parser.parse_args()

    # ── 基金搜索模式 ─────────────────────────────────────────────
    if args.search:
        _run_search(args.search)
        return

    # ── 单基金综合研判模式 ────────────────────────────────────────
    if args.analyze:
        _run_analyze(args.analyze)
        return

    # ── 持仓健康诊断模式（独立分支，不触发完整采集流程）─────────
    if args.check_holdings is not None:
        _run_check_holdings(args.check_holdings)
        return

    print("=" * 60)
    print("  基金投资私人幕僚系统 — 启动中")
    print("=" * 60)

    from src.application.update_pipeline import run_update
    signal, scores_df, portfolio = run_update()

    from src.utils import provenance
    print()
    print(provenance.banner())

    print(f"\n[信号] 综合市场信号：{signal.get('composite_signal', '—')}")
    print(f"  经济周期：{signal.get('macro_cycle', '—')}")
    print(f"  估值水位：{signal.get('valuation_level', '—')}")
    print(
        f"  建议仓位：核心{signal.get('core_allocation', 0)*100:.0f}%"
        f" | 卫星{signal.get('satellite_allocation', 0)*100:.0f}%"
        f" | 现金{signal.get('cash_allocation', 0)*100:.0f}%"
    )

    # 回测分析（可选，--backtest 触发）
    backtest = None
    if args.backtest:
        print("\n[回测] 开始走向前回测分析...")
        try:
            from src.backtester.engine import run_backtest
            backtest = run_backtest()
            print("[回测] 分析完成，结果将注入报告第九章")
        except Exception as e:
            print(f"[回测] 回测失败（报告第九章将显示'未执行'）: {e}")

    # 生成 Markdown + HTML 投研报告，并自动输出至 WSL-output
    try:
        from src.reports.report_builder import build_report
        from src.reports.html_report_builder import build_html_report
        import shutil

        report_path = build_report(signal, portfolio, scores_df=scores_df, backtest=backtest)
        html_path   = build_html_report(signal, portfolio, scores_df=scores_df, backtest=backtest)
        print(f"\n[报告] Markdown：{report_path}")
        print(f"[报告] HTML    ：{html_path}")

        _WSL_OUTPUT = "/mnt/e/WSL-output"
        import os
        if os.path.isdir(_WSL_OUTPUT):
            shutil.copy(report_path, _WSL_OUTPUT)
            shutil.copy(html_path,   _WSL_OUTPUT)
            print(f"[报告] 已输出至 {_WSL_OUTPUT}/")
        else:
            print(f"[报告] {_WSL_OUTPUT} 不可访问，跳过输出")
    except Exception as e:
        print(f"\n[警告] 报告生成失败（数据采集流程不受影响）：{e}")


def _run_check_holdings(source: str):
    """持仓健康诊断子流程。"""
    from src.holdings.checker import (
        check_holdings, load_signal_from_db, parse_holdings_str
    )
    from src.reports.report_builder import build_holdings_report

    # ── 加载持仓 ──────────────────────────────────────────────
    holdings: list[dict] = []
    source = source.strip()

    if ":" in source and not source.endswith(".yaml") and not source.endswith(".yml"):
        # 内联格式：code1:w1,code2:w2
        try:
            holdings = parse_holdings_str(source)
        except ValueError as e:
            print(f"[错误] 持仓格式解析失败：{e}")
            return
    else:
        # YAML 文件
        import yaml
        yaml_path = source if os.path.isabs(source) else os.path.join(
            os.path.dirname(os.path.abspath(__file__)), source
        )
        if not os.path.exists(yaml_path):
            print(f"[错误] 找不到持仓文件：{yaml_path}")
            print("  请先编辑 config/my_holdings.yaml 填入你的实际持仓，")
            print("  或使用内联格式：python run.py --check-holdings 'code1:40,code2:60'")
            return
        try:
            with open(yaml_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            holdings = data.get("holdings", [])
        except Exception as e:
            print(f"[错误] YAML 读取失败：{e}")
            return

    if not holdings:
        print("[错误] 持仓列表为空，请检查配置文件或输入格式")
        return

    # ── 获取市场信号 ──────────────────────────────────────────
    print("[持仓诊断] 读取最新市场信号...")
    market_signal = load_signal_from_db()
    if not market_signal:
        print("[持仓诊断] 数据库无市场信号，正在采集数据（首次运行）...")
        from src.application.update_pipeline import run_update
        market_signal, _, _ = run_update()

    # ── 执行诊断 ──────────────────────────────────────────────
    try:
        result = check_holdings(holdings, market_signal)
    except ValueError as e:
        print(f"[错误] {e}")
        return

    # ── 打印 CLI 报告 ─────────────────────────────────────────
    _print_check_result(result)

    # ── 生成 Markdown 报告 ────────────────────────────────────
    try:
        report_path = build_holdings_report(result)
        print(f"\n[报告] 持仓诊断报告已生成：{report_path}")
    except Exception as e:
        print(f"\n[警告] Markdown 报告生成失败：{e}")


def _print_check_result(result: dict):
    """在终端打印持仓诊断摘要。"""
    _EMOJI = {"重仓进取": "🟢", "标配稳健": "🔵", "谨慎防守": "🟠", "减仓防守": "🔴"}
    _VERDICT_EMOJI = {"green": "🟢 GREEN", "yellow": "🟡 YELLOW", "red": "🔴 RED"}
    _SIGNAL_LABEL = {"买入": "买入↑", "增持": "增持↑", "持有": "持有·", "观望": "观望△", "回避": "回避✗"}

    composite = result.get("composite_signal", "未知")
    sig_emoji = _EMOJI.get(composite, "⚪")
    sig_date = result.get("signal_date", "")

    print()
    print("═" * 56)
    print(f"  持仓健康诊断  [{sig_emoji} {composite}  {sig_date}]")
    print("═" * 56)

    # 持仓明细
    print("\n  持仓明细：")
    print(f"  {'代码':<12}{'名称':<16}{'权重':>6}  {'评分':>6}  {'信号':<6}  {'策略匹配':>8}")
    print(f"  {'─'*12}{'─'*16}{'─'*6}  {'─'*6}  {'─'*6}  {'─'*8}")
    for h in result["holdings"]:
        code = h["fund_code"]
        name = (h.get("fund_name") or code)[:14]
        w = f"{h['weight']:.1f}%"
        sc = h.get("score")
        score_str = f"{sc['total_score']:.1f}" if sc and sc.get("total_score") is not None else "—"
        sig = _SIGNAL_LABEL.get(h.get("signal") or "", "—")
        strat = f"{h.get('strategy_score', 0):.1f}/10" if code != "cash" else "—"
        print(f"  {code:<12}{name:<16}{w:>6}  {score_str:>6}  {sig:<6}  {strat:>8}")

    # 组合分析
    ana = result["analytics"]
    print("\n  组合分析：")
    ac_str = "  ".join(f"{k} {v:.1f}%" for k, v in ana["asset_class_distribution"].items())
    rg_str = "  ".join(f"{k} {v:.1f}%" for k, v in ana["region_distribution"].items())
    print(f"  资产类别：{ac_str}")
    print(f"  地区分布：{rg_str}")

    hhi = ana["hhi"]
    hhi_label = "分散" if hhi < 0.4 else "中等" if hhi < 0.65 else "集中"
    print(f"  集中度（HHI）：{hhi:.2f}（{hhi_label}）")

    ws = ana.get("weighted_score")
    print(f"  加权综合评分：{ws:.1f} / 100" if ws is not None else "  加权综合评分：— （评分数据不足）")
    print(f"  加权策略匹配：{ana['weighted_strategy_score']:.1f} / 10")
    wer = ana.get("weighted_expense_ratio")
    print(f"  加权费率：{wer:.2f}%" if wer is not None else "  加权费率：—")
    print(f"  现金仓位：{ana['cash_pct']:.1f}%（市场建议 {ana['recommended_cash_pct']:.1f}%）")

    # Gap 分析
    gap = result["gap"]
    print("\n  vs 系统推荐 Top-5：")
    if gap["in_recommendation"]:
        for r in gap["in_recommendation"]:
            print(f"  ✓ {r['code']} {r['name']} — 与系统推荐重叠")
    if gap["not_in_recommendation"]:
        codes = "、".join(gap["not_in_recommendation"][:4])
        print(f"  △ {codes} — 不在当前推荐池")
    if gap["missing_recommended"]:
        names = "、".join(r["name"] for r in gap["missing_recommended"][:3])
        print(f"  + 推荐池中你尚未持有：{names}")
    if gap["overlap_count"] == 0 and not gap["not_in_recommendation"]:
        print("  （持仓基金均不在数据库，无法对比）")

    # 健康裁决
    verdict = result["verdict"]
    overall = _VERDICT_EMOJI.get(verdict["overall"], verdict["overall"])
    print(f"\n  健康裁决：{overall}")

    if verdict["issues"]:
        print("  问题：")
        for iss in verdict["issues"]:
            print(f"    · {iss}")
    if verdict["strengths"]:
        print("  亮点：")
        for s in verdict["strengths"]:
            print(f"    · {s}")
    if verdict["actions"]:
        print("  建议操作：")
        for a in verdict["actions"]:
            print(f"    · {a}")

    print()
    print("  注：诊断基于系统数据库中的基金评分，结果仅供参考，不构成投资建议。")
    print("═" * 56)


def _run_search(query: str):
    """基金名称/关键词搜索子流程。"""
    from src.analysis.fund_lookup import search_funds
    hits = search_funds(query)
    if not hits:
        print(f"[搜索] 未找到匹配「{query}」的基金。")
        print("  提示：可运行 python run.py 先更新基金数据库，或直接使用6位代码。")
        return
    print(f"[搜索] 找到 {len(hits)} 条结果（关键词：{query}）：")
    print(f"  {'代码':<10}{'名称':<24}{'类型':<12}{'基准':<16}{'地区'}")
    print(f"  {'─'*10}{'─'*24}{'─'*12}{'─'*16}{'─'*8}")
    for h in hits[:15]:
        print(f"  {h['fund_code']:<10}{h['fund_name'][:22]:<24}{h['fund_type'][:10]:<12}{h['benchmark'][:14]:<16}{h['region']}")
    if len(hits) > 15:
        print(f"  ... 共 {len(hits)} 条，显示前 15 条")
    print(f"\n  使用方式：python run.py --analyze <代码>")


def _fetch_on_demand(fund_code: str) -> bool:
    """按需采集单只基金的净值数据，并实时刷新费率。返回 True 表示有数据。"""
    from src.utils.database import read_table, upsert_dataframe
    from src.utils.fund_universe import CORE_QDII_FUNDS
    import pandas as pd
    from datetime import datetime, timedelta

    # ── 1. 实时拉取费率（每次分析都刷新，不依赖静态库）─────────────
    _refresh_expense_ratio(fund_code)

    # ── 2. 净值采集（已有足够数据则跳过）──────────────────────────
    nav = read_table("fund_nav_history", "fund_code = ? LIMIT 25", (fund_code,))
    if len(nav) >= 20:
        # 有净值但绩效可能过期，也重算一次
        _recompute_performance(fund_code)
        return True

    _ETF_TYPES = {"ETF", "LOF", "ETF联接", "增强指数"}
    universe_map = {f["fund_code"]: f for f in CORE_QDII_FUNDS}
    fund_meta = universe_map.get(fund_code)
    fund_type = fund_meta.get("fund_type", "") if fund_meta else ""

    # 确保 fund_list 中有记录（不写费率，费率由 _refresh_expense_ratio 管）
    existing = set(read_table("fund_list")["fund_code"].astype(str).tolist())
    if fund_code not in existing and fund_meta:
        upsert_dataframe(pd.DataFrame([{
            "fund_code":  fund_code,
            "fund_name":  fund_meta["fund_name"],
            "fund_type":  fund_type,
            "benchmark":  fund_meta.get("benchmark", ""),
            "updated_at": datetime.now().strftime("%Y-%m-%d"),
        }]), "fund_list", ["fund_code"])

    if fund_type in _ETF_TYPES:
        print(f"  [按需采集] {fund_code} 从 yfinance 拉取历史净值...")
        try:
            from src.collectors.baostock_etf_collector import _yf_ticker
            import yfinance as yf
            ticker = _yf_ticker(fund_code)
            df = yf.download(
                ticker,
                start=(datetime.now() - timedelta(days=365 * 5)).strftime("%Y-%m-%d"),
                auto_adjust=True, progress=False,
            )
            if not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df = df[["Close"]].reset_index()
                df.columns = ["date", "nav"]
                df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.strftime("%Y-%m-%d")
                df["acc_nav"] = df["nav"]
                df["daily_return"] = df["nav"].pct_change() * 100
                df["fund_code"] = fund_code
                upsert_dataframe(
                    df[["fund_code", "date", "nav", "acc_nav", "daily_return"]].dropna(subset=["nav"]),
                    "fund_nav_history", ["fund_code", "date"],
                )
                print(f"  [按需采集] {len(df)} 条净值（yfinance）")
        except Exception as e:
            print(f"  [按需采集] yfinance 失败: {e}")
    else:
        print(f"  [按需采集] {fund_code} 从天天基金拉取历史净值...")
        try:
            from src.collectors.eastmoney_collector import collect_eastmoney
            r = collect_eastmoney([fund_code])
            if r.get("nav_rows", 0):
                print(f"  [按需采集] {r['nav_rows']} 条净值（天天基金）")
        except Exception as e:
            print(f"  [按需采集] 天天基金失败: {e}")

    return _recompute_performance(fund_code)


def _refresh_expense_ratio(fund_code: str):
    """从天天基金 F10 费率页实时拉取管理费+托管费，更新 fund_list。"""
    import re, requests
    from src.utils.database import upsert_dataframe
    import pandas as pd
    from datetime import datetime

    try:
        url = f"https://fundf10.eastmoney.com/jjfl_{fund_code}.html"
        headers = {"Referer": "https://fundf10.eastmoney.com/",
                   "User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=8)
        if r.status_code != 200:
            return

        # 管理费率、托管费率
        mgmt = re.search(r'管理费率</td><td[^>]*>([\d.]+)%', r.text)
        cust = re.search(r'托管费率</td><td[^>]*>([\d.]+)%', r.text)
        sale = re.search(r'销售服务费率</td><td[^>]*>([\d.]+)%', r.text)

        if mgmt and cust:
            total = float(mgmt.group(1)) + float(cust.group(1))
            if sale:
                total += float(sale.group(1))
            total_ratio = round(total / 100, 6)

            upsert_dataframe(pd.DataFrame([{
                "fund_code":    fund_code,
                "expense_ratio": total_ratio,
                "updated_at":   datetime.now().strftime("%Y-%m-%d"),
            }]), "fund_list", ["fund_code"])
            print(f"  [费率] {fund_code} 管理{mgmt.group(1)}%+托管{cust.group(1)}% = {total:.2f}% (已更新)")
    except Exception:
        pass  # 费率更新失败不阻断主流程


def _recompute_performance(fund_code: str) -> bool:
    """重算绩效指标并写回 fund_performance。返回是否有足够数据。"""
    from src.utils.database import read_table, upsert_dataframe
    import pandas as pd

    nav_check = read_table("fund_nav_history", "fund_code = ? LIMIT 25", (fund_code,))
    if len(nav_check) < 20:
        return False
    try:
        from src.analyzers.fund_analyzer import _calc_performance
        perf = _calc_performance(fund_code)
        if perf:
            upsert_dataframe(pd.DataFrame([perf]), "fund_performance", ["fund_code"])
    except Exception:
        pass
    return True


def _run_analyze(query: str):
    """单基金综合研判子流程，支持代码或名称关键词。"""
    from src.analysis.fund_deep_analysis import analyze_fund
    from src.analysis.fund_lookup import search_funds, resolve_fund_code
    from src.holdings.checker import load_signal_from_db
    from src.reports.report_builder import build_fund_report

    # 尝试解析 query 为基金代码
    fund_code = resolve_fund_code(query)

    if fund_code is None:
        hits = search_funds(query)
        if not hits:
            print(f"[研判] 未找到基金「{query}」。请确认代码或关键词，或先运行 python run.py 更新数据库。")
            return
        if len(hits) > 1:
            print(f"[研判] 「{query}」匹配到多只基金，请指定代码：")
            for h in hits[:8]:
                print(f"  {h['fund_code']}  {h['fund_name']}")
            return
        fund_code = hits[0]["fund_code"]

    print(f"[研判] 正在分析基金 {fund_code} ...")

    # 按需采集（数据库无数据时自动拉取）
    _fetch_on_demand(fund_code)

    market_signal = load_signal_from_db()

    result = analyze_fund(fund_code, market_signal or None)
    _print_analysis(result)

    try:
        path = build_fund_report(result)
        print(f"\n[报告] 研判报告已生成：{path}")
    except Exception as e:
        print(f"\n[警告] Markdown 报告生成失败：{e}")


def _print_analysis(result: dict):
    """终端打印 8 模块研判摘要。"""
    _EMOJI = {"重仓进取": "🟢", "标配稳健": "🔵", "谨慎防守": "🟠", "减仓防守": "🔴"}
    _GRADE_EMOJI = {
        "优质候选": "🟢", "合格候选": "🔵", "有明显短板": "🟡",
        "不建议配置": "🟠", "剔除": "🔴",
    }

    info    = result["fund_info"]
    perf    = result["performance"]
    adv     = result["advanced_metrics"]
    scores  = result["scores"]
    vetoes  = result["vetoes"]
    concl   = result["conclusion"]
    hold    = result["holdings"]
    peer    = result["peer_context"]

    name = info.get("fund_name", fund_code := result["fund_code"])
    sig_info = concl.get("fit_signal") or {}
    sig_str = ""
    if sig_info:
        sig_str = f"  [{_EMOJI.get(sig_info['composite_signal'], '⚪')} {sig_info['composite_signal']}]"

    print()
    print("═" * 60)
    print(f"  单基金综合研判：{name} [{result['fund_code']}]")
    print(f"  {info.get('asset_class', '')}  基准：{info.get('benchmark', '—')}  地区：{info.get('region', '—')}{sig_str}")
    print("═" * 60)

    # 【一】产品概况
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

    # 【二】综合评分
    total = scores["total"]
    grade = concl["grade"]
    grade_emoji = _GRADE_EMOJI.get(grade, "⚪")
    print(f"\n【二】综合评分  →  {grade_emoji} {total:.1f} / 100  【{grade}】")
    dim_names = {
        "performance": ("业绩质量", 20),
        "risk":        ("风险控制", 20),
        "manager":     ("基金经理", 15),
        "strategy":    ("策略稳定", 15),
        "attribution": ("收益归因", 10),
        "structure":   ("规模流动", 10),
        "cost":        ("费用成本", 10),
    }
    print(f"  {'维度':<10}  {'得分':>6}  {'满分':>4}  {'数据'}")
    print(f"  {'─'*10}  {'─'*6}  {'─'*4}  {'─'*10}")
    for key, (label, max_s) in dim_names.items():
        s = scores.get(key, {})
        raw = s.get("score", "—")
        # 数据覆盖概览
        covers = set()
        for d in (s.get("details") or {}).values():
            covers.add(d.get("coverage", "?"))
        cover_str = "✅" if covers <= {"COMPUTED"} else "⚠️ 部分" if "UNAVAILABLE" in covers else "~"
        print(f"  {label:<10}  {raw:>6.1f}  {max_s:>4}  {cover_str}")
    print(f"  {'─'*10}  {'─'*6}")
    print(f"  {'合计':<10}  {total:>6.1f}  {'100':>4}")

    # 【三】一票否决
    print("\n【三】一票否决检查")
    hard_v = [v for v in vetoes if v.get("severity") == "hard"]
    if not vetoes:
        print("  ✅ 无触发")
    else:
        for v in vetoes:
            emoji = "🚨" if v["severity"] == "hard" else "⚠️"
            print(f"  {emoji} [{v['id']}] {v['condition']}：{v['detail']}")

    # 【四】关键指标
    print("\n【四】关键指标")
    _m = lambda v, fmt=".2f": f"{v:{fmt}}" if v is not None and not (isinstance(v, float) and v != v) else "—"
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

    # 【五】风险特征
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

    # 【六】持仓穿透
    print("\n【六】持仓穿透")
    if hold:
        sr = hold.get("stock_ratio")
        br = hold.get("bond_ratio")
        cr = hold.get("cash_ratio")
        date_str = hold.get("date", "—")
        print(f"  持仓日期：{date_str}")
        print(f"  股票 {sr:.1f}%  债券 {br:.1f}%  现金 {cr:.1f}%" if all(v is not None for v in [sr, br, cr]) else "  （持仓比例数据不全）")
        codes = hold.get("stock_codes", "")
        if codes:
            code_list = [c.strip() for c in str(codes).split(",") if c.strip()][:5]
            print(f"  前5大持仓代码：{', '.join(code_list)}（共 {len([c for c in str(codes).split(',') if c.strip()])} 只）")
    else:
        print("  暂无持仓穿透数据（可运行 python run.py 更新）")

    # 【七】收益归因
    print("\n【七】收益归因（代理基准：SP500）")
    beta = adv.get("beta")
    alpha = adv.get("alpha_annual")
    r2 = adv.get("r_squared")
    ir = adv.get("information_ratio")
    if beta is not None:
        print(f"  Beta={_m(beta)}  Alpha年化={_m(alpha)}%  R²={_m(r2)}  IR={_m(ir)}")
        r2_pct = r2 * 100 if r2 else 0
        print(f"  市场 beta 解释收益 {r2_pct:.1f}%，其余 {100-r2_pct:.1f}% 来自风格/选基/择时")
    else:
        print("  （NAV 历史数据不足，无法计算；建议先运行 python run.py 更新数据）")

    # 【八】配置结论
    print("\n【八】配置结论")
    print(f"  {concl['summary']}")
    if sig_info:
        strat_s = sig_info.get("strategy_match_score", 0)
        assess = sig_info.get("assessment", "")
        print(f"  当前市场信号适配：{strat_s:.1f}/10（{assess}）")

    # 【九】地区宏观机会
    ro = result.get("region_outlook")
    if ro:
        print("\n【九】地区宏观机会评估（4地区横向对比）")
        cov = ro.get("covered_regions", {})
        ranking = ro.get("ranking", [])
        _LABEL_EMOJI = {"强势": "🟢", "偏强": "🔵", "中性": "🟡", "偏弱": "🟠", "弱势": "🔴"}
        print(f"  {'地区':<10} {'综合':>5}  {'宏观':>5}  {'动量':>5}  {'相对':>5}  {'标签':<6}  {'GDP%':>6}  {'通胀%':>6}  {'近1年':>7}  {'vs美国3年':>9}")
        print(f"  {'─'*10} {'─'*5}  {'─'*5}  {'─'*5}  {'─'*5}  {'─'*6}  {'─'*6}  {'─'*6}  {'─'*7}  {'─'*9}")
        order = ranking if ranking else list(cov.keys())
        for rk in order:
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

        notes = ro.get("data_notes", [])
        if notes:
            for n in notes[:3]:
                print(f"  ⚠️  {n}")

    print()
    print("  注：以 SP500 为代理基准计算 alpha/beta/IR，QDII 应以其实际基准（如纳斯达克100）为准。")
    print("  结论仅供参考，不构成投资建议。")
    print("═" * 60)


if __name__ == "__main__":
    main()
