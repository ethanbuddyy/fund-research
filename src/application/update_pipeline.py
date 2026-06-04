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
    _log("[3.2/5] 天天基金净值/持仓/经理/换手率富集完成")

    # 场内 ETF/LOF QDII（513100 等）走 Baostock 行情通道，与场外基金互不干扰
    from src.collectors.baostock_etf_collector import collect_etf_nav
    etf_count = collect_etf_nav()
    _log(f"[3.3/5] 场内 ETF/LOF 净值采集完成：{etf_count} 只")

    from src.collectors.fund_fee_collector import collect_fund_fees
    collect_fund_fees(pool_codes)
    _log("[3.4/5] 申购/赎回费率采集完成")

    from src.collectors.valuation_collector import collect_valuation_data
    collect_valuation_data()
    _log("[3.5/5] 真实估值数据更新完成")

    from src.analyzers.fund_analyzer import analyze_all_funds
    analyze_all_funds()
    _log("[4/5] 基金绩效分析完成")

    from src.recommender.signals import generate_market_signal
    from src.recommender.scorer import score_all_funds
    from src.recommender.portfolio import build_portfolio_recommendation
    from src.utils.config import load_config
    signal = generate_market_signal()
    scores_df = score_all_funds(signal)
    portfolio = build_portfolio_recommendation(signal)
    _log(f"[5/5] 投资信号生成完成 → {signal.get('composite_signal', '—')}")

    # ── 组合浮亏追踪 + 止损检测（基于上次快照，需在 build_portfolio 之后）
    cfg = load_config()
    stop_loss_pct = float((cfg.get("risk_management") or {}).get("stop_loss_pct") or 0)
    stop_loss_info = None
    if stop_loss_pct > 0:
        try:
            from src.utils.portfolio_tracker import update_and_check
            stop_loss_info = update_and_check(stop_loss_pct)
            if stop_loss_info.get("triggered"):
                # 强制降至"减仓防守"档，覆盖信号和组合仓位
                signal["composite_signal"] = "减仓防守"
                signal["core_allocation"] = 0.35
                signal["satellite_allocation"] = 0.15
                signal["cash_allocation"] = 0.50
                signal["stop_loss_triggered"] = True
                _log(
                    f"[⚠️ 止损] 组合回撤 {stop_loss_info['drawdown_pct']:.1f}% "
                    f"超过阈值 {stop_loss_pct*100:.0f}%，强制降仓至减仓防守"
                )
            else:
                _log(
                    f"[止损] 组合净值 {stop_loss_info['portfolio_nav']:.2f}"
                    f"（高水位 {stop_loss_info['high_water_mark']:.2f}）"
                    f"，回撤 {stop_loss_info['drawdown_pct']:.1f}%，未触发"
                )
        except Exception as e:
            _log(f"[止损] 浮亏追踪失败（不影响主流程）: {e}")
    signal["stop_loss"] = stop_loss_info

    return signal, scores_df, portfolio
