"""统一数据更新流水线。

run.py 和 scheduler.py 共用此处的编排逻辑，避免重复维护两条几乎相同的流程。
新增采集步骤、修改顺序、调整重试逻辑只需改这一处。
"""
from typing import Any

from src.domain.types import MarketSignal, PortfolioRecommendation


def run_update(logger=None) -> tuple[MarketSignal, Any, PortfolioRecommendation]:
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

    from src.recommender.signals import (
        generate_market_signal, apply_stop_loss, save_market_signal,
    )
    from src.recommender.scorer import score_all_funds
    from src.recommender.portfolio import build_portfolio_recommendation
    from src.utils.config import load_config
    from src.utils.portfolio_state_store import (
        load_previous_portfolio, commit_runtime_state,
    )

    # ── 状态所有权（阶段1）：本期决策开始前，只读取一次上期组合快照 ──
    # 之后显式传给「止损检查 / 组合选择 / 报告对比」，杜绝各模块各自读盘导致的
    # 隐式时序耦合（谁先读、谁先写决定正确性）。
    previous_portfolio = load_previous_portfolio()

    # 信号生成阶段：先不落库（save=False），待止损覆盖后再持久化最终版本，
    # 避免数据库存到止损前的旧信号（scheduler/持仓诊断从库里读的须是最终信号）。
    signal = generate_market_signal(save=False)
    scores_df = score_all_funds(signal)

    # ── 组合浮亏追踪 + 止损检测（阶段2）────────────────────────────
    # 止损检查只读「上期」快照（previous_portfolio），不读本次刚算出的数据；
    # 触发后用纯函数 apply_stop_loss 生成新信号（不原地改），仓位档位取自
    # POSITION_TIERS，再据此构建组合，保证「建议仓位」与「推荐组合权重」一致。
    cfg = load_config()
    stop_loss_pct = float((cfg.get("risk_management") or {}).get("stop_loss_pct") or 0)
    stop_loss_info = None
    if stop_loss_pct > 0:
        try:
            from src.utils.portfolio_tracker import update_and_check
            stop_loss_info = update_and_check(stop_loss_pct, previous_portfolio)
            if stop_loss_info.get("triggered"):
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
    signal = apply_stop_loss(signal, stop_loss_info)

    # 组合构建：使用（可能已被止损覆盖的）最终 signal + 上期快照（换仓门槛/对比）。
    # build 不再写盘，本期快照数据放在 portfolio["snapshot_payload"]，下面统一提交。
    portfolio = build_portfolio_recommendation(signal, previous_portfolio=previous_portfolio)
    _log(f"[5/5] 投资信号生成完成 → {signal.get('composite_signal', '—')}")

    # ── 持久化：先存最终信号（止损后唯一版本），再提交本期组合快照 ──
    # 报告层的「换仓变动」用的是 portfolio 里携带的内存版 previous_portfolio，
    # 与此处落盘的本期快照无关，故现在提交不会污染本期对比。
    save_market_signal(signal)
    snapshot_payload = portfolio.get("snapshot_payload")
    if snapshot_payload:
        nav_state = stop_loss_info.get("next_nav_state") if stop_loss_info else None
        commit_runtime_state(snapshot_payload, nav_state)

    # ── 语料沉淀：把本次「用完即弃」叙事 + 历史报告收编进检索库（fail-soft）──
    try:
        from src.retrieval.ingest import ingest_run
        added = ingest_run(signal, scores_df, portfolio)
        if added:
            _log(f"[检索] 语料沉淀完成，新增 {added} 条文档")
    except Exception as e:
        _log(f"[检索] 语料沉淀跳过（不影响主流程）: {e}")

    return signal, scores_df, portfolio
