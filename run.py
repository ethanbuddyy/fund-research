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
        "--recall", metavar="QUERY",
        help="语义检索已沉淀语料（叙事/新闻/研判/历史报告），如：--recall 美联储降息",
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

    # ── 语义检索模式（独立分支，不触发采集）─────────────────────
    if args.recall:
        from src.cli.recall_command import run_recall
        run_recall(args.recall)
        return

    # ── 单基金综合研判模式 ────────────────────────────────────────
    if args.analyze:
        from src.cli.analyze_command import run_analyze
        run_analyze(args.analyze)
        return

    # ── 持仓健康诊断模式（独立分支，不触发完整采集流程）─────────
    if args.check_holdings is not None:
        from src.cli.check_holdings_command import run_check_holdings
        run_check_holdings(args.check_holdings)
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

    # 生成 HTML 投研报告（主报告仅 HTML，不再有 Markdown 孪生），并自动输出至 WSL-output
    try:
        from src.reports.html_report_builder import build_html_report
        import shutil

        html_path = build_html_report(signal, portfolio, scores_df=scores_df, backtest=backtest)
        print(f"\n[报告] HTML：{html_path}")

        _WSL_OUTPUT = "/mnt/e/WSL-output"
        if os.path.isdir(_WSL_OUTPUT):
            shutil.copy(html_path, _WSL_OUTPUT)
            print(f"[报告] 已输出至 {_WSL_OUTPUT}/")
        else:
            print(f"[报告] {_WSL_OUTPUT} 不可访问，跳过输出")
    except Exception as e:
        print(f"\n[警告] 报告生成失败（数据采集流程不受影响）：{e}")


def _run_search(query: str) -> None:
    """基金名称/关键词搜索子流程。"""
    from src.analysis.fund_lookup import search_funds
    hits = search_funds(query)
    if not hits:
        print(f"[搜索] 未找到匹配「{query}」的基金。")
        print("  提示：可运行 python3 run.py 先更新基金数据库，或直接使用6位代码。")
        return
    print(f"[搜索] 找到 {len(hits)} 条结果（关键词：{query}）：")
    print(f"  {'代码':<10}{'名称':<24}{'类型':<12}{'基准':<16}{'地区'}")
    print(f"  {'─'*10}{'─'*24}{'─'*12}{'─'*16}{'─'*8}")
    for h in hits[:15]:
        print(f"  {h['fund_code']:<10}{h['fund_name'][:22]:<24}{h['fund_type'][:10]:<12}{h['benchmark'][:14]:<16}{h['region']}")
    if len(hits) > 15:
        print(f"  ... 共 {len(hits)} 条，显示前 15 条")
    print(f"\n  使用方式：python3 run.py --analyze <代码>")


if __name__ == "__main__":
    main()
