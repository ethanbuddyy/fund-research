"""统一数据更新流水线。

run.py 和 scheduler.py 共用此处的编排逻辑，避免重复维护两条几乎相同的流程。
新增采集步骤、修改顺序、调整重试逻辑只需改这一处。
"""


def run_update(logger=None) -> dict:
    """执行完整数据更新流程并返回最新市场信号。

    Args:
        logger: 可选 logging.Logger；传 None 时使用 print 输出。

    Returns:
        market_signal dict（来自 generate_market_signal）。
    """
    def _log(msg: str):
        if logger:
            logger.info(msg)
        else:
            print(msg)

    from src.utils.database import init_database
    init_database()
    _log("[OK] 数据库初始化完成")

    from src.collectors.macro_collector import collect_macro_data
    collect_macro_data()
    _log("[1/5] 宏观数据更新完成")

    from src.collectors.global_macro_collector import collect_global_macro
    collect_global_macro()
    _log("[1.5/5] 全球宏观(World Bank/OECD)更新完成")

    from src.collectors.market_collector import collect_market_data
    collect_market_data()
    _log("[2/5] 市场数据更新完成")

    from src.collectors.fund_screener import screen_funds, save_pool
    pool = screen_funds()
    if pool:
        save_pool(pool)
        pool_codes = [p["fund_code"] for p in pool]
        _log(f"[3/5] 规则筛选基金池完成：{len(pool)} 只")
    else:
        from src.collectors.fund_collector import collect_fund_data
        collect_fund_data()
        pool_codes = None
        _log("[3/5] 基金数据更新完成（核心池）")

    from src.collectors.eastmoney_collector import collect_eastmoney
    collect_eastmoney(pool_codes)
    _log("[3.2/5] 天天基金真实净值/持仓富集完成")

    from src.collectors.valuation_collector import collect_valuation_data
    collect_valuation_data()
    _log("[3.5/5] 真实估值数据更新完成")

    from src.analyzers.fund_analyzer import analyze_all_funds
    analyze_all_funds()
    _log("[4/5] 基金绩效分析完成")

    from src.recommender.signals import generate_market_signal
    from src.recommender.scorer import score_all_funds
    from src.recommender.portfolio import build_portfolio_recommendation
    signal = generate_market_signal()
    scores_df = score_all_funds(signal)
    portfolio = build_portfolio_recommendation(signal)
    _log(f"[5/5] 投资信号生成完成 → {signal.get('composite_signal', '—')}")

    return signal, scores_df, portfolio
