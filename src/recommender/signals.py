"""综合市场信号生成"""
from typing import Any
from collections.abc import Mapping
import numpy as np
from datetime import datetime
from ..utils.database import read_table
from ..utils import signal_repository
from ..analyzers.macro_analyzer import analyze_macro_cycle
from ..analyzers.valuation import calculate_valuation_metrics
from ..collectors.news_collector import get_market_sentiment
from ..utils.config import load_config
from ..analyzers.narrative import generate_narrative
from ..domain.scoring import (
    classify_signal, credit_score_from_spread, trend_score_from_deviation, apply_user_profile,
    POSITION_TIERS,
)
from ..domain.factor_config import FACTOR_WEIGHTS
from ..domain.types import MarketSignal, StopLossResult

_SIGNAL_COLORS = {"重仓进取": "green", "标配稳健": "blue", "谨慎防守": "orange", "减仓防守": "red"}


def _credit_score() -> float:
    df = read_table("macro_data", "series_id = ? ORDER BY date DESC LIMIT 1", ("BAMLH0A0HYM2",))
    if df.empty:
        return 5.0
    return credit_score_from_spread(float(df.iloc[0]["value"]))


def _trend_score() -> float:
    df = read_table("market_data", "symbol = ? ORDER BY date DESC LIMIT 300", ("^GSPC",))
    if len(df) < 60:
        return 5.0
    df = df.sort_values("date")
    prices = df["close"].astype(float)
    current = float(prices.iloc[-1])
    window = min(252, len(prices))
    if window < 252:
        print(f"[WARN] 趋势因子：SP500 数据仅 {window} 条（不足252），使用 {window} 日均线代替年线")
    ma = float(prices.tail(window).mean())
    return trend_score_from_deviation((current - ma) / ma)


def compute_market_signal(
    inputs: Mapping[str, Any], config: Mapping[str, Any]
) -> MarketSignal:
    """纯函数：由「已采集好的各维度数据 + 配置」算出市场信号。

    **不读库、不读配置文件、不调 AI、不落库、不打印**——所有外部数据由适配器
    generate_market_signal 先取好放进 inputs，本函数只做确定性计算，可脱离
    SQLite/网络/AI 用内存数据直接测试（回归 test #8：AI 关闭时与重构前逐字一致）。

    inputs 必含键：date, data_source, data_quality, macro, valuation, sentiment,
    trend_score, credit_score, global_macro, global_macro_score, narrative。
    AI 阶段一对 narrative / ai_analysis 的改写由适配器在本函数产出后追加。
    """
    macro = inputs["macro"]
    valuation = inputs["valuation"]
    sentiment = inputs["sentiment"]
    trend_score = inputs["trend_score"]
    credit_score = inputs["credit_score"]
    global_macro = inputs["global_macro"]
    global_macro_score = inputs["global_macro_score"]

    # 各维度得分
    macro_score      = macro.get("cycle_score", 5)
    fed_direction    = macro.get("fed_direction_score", 0.0)
    valuation_score  = valuation.get("valuation_score", 5)
    sentiment_score  = sentiment.get("score", 50) / 10         # 0-100 → 0-10
    contrarian       = 10 - sentiment_score                    # 逆向情绪

    # 宏观分叠加利率方向修正（上限10，下限1）
    macro_adj = float(np.clip(macro_score + fed_direction, 1, 10))

    composite_raw = (
        macro_adj            * FACTOR_WEIGHTS["macro"]
        + valuation_score    * FACTOR_WEIGHTS["valuation"]
        + contrarian         * FACTOR_WEIGHTS["sentiment"]
        + trend_score        * FACTOR_WEIGHTS["trend"]
        + credit_score       * FACTOR_WEIGHTS["credit"]
        + global_macro_score * FACTOR_WEIGHTS["global_macro"]
    )

    composite_signal, core_alloc, satellite_alloc, cash_alloc = classify_signal(composite_raw)

    # 用户个人化调整（risk_tolerance / investment_horizon_years / 上下界）
    user_profile = config.get("user_profile") or {}
    user_profile_applied = False
    if user_profile:
        core_alloc, satellite_alloc, cash_alloc = apply_user_profile(
            core_alloc, satellite_alloc, cash_alloc, user_profile
        )
        user_profile_applied = True

    signal: MarketSignal = {
        "date": inputs["date"],
        "data_source": inputs["data_source"],        # real / partial / mock
        "data_quality": inputs["data_quality"],
        "macro_cycle": macro.get("cycle"),
        "valuation_level": valuation.get("valuation_level"),
        "sentiment_label": sentiment.get("label"),
        "composite_signal": composite_signal,
        "signal_color": _SIGNAL_COLORS[composite_signal],
        "cape": valuation.get("cape"),
        "sp500_pe": valuation.get("sp500_pe"),
        "vix": sentiment.get("vix"),
        "buffett_indicator": valuation.get("buffett_indicator"),
        "equity_risk_premium": valuation.get("equity_risk_premium"),
        "core_allocation": core_alloc,
        "satellite_allocation": satellite_alloc,
        "cash_allocation": cash_alloc,
        "user_profile_applied": user_profile_applied,
        "user_profile": {
            k: user_profile.get(k)
            for k in ("risk_tolerance", "investment_horizon_years", "max_equity_pct", "min_cash_pct")
        } if user_profile_applied else None,
        "timing_score": composite_raw,
        "trend_score": round(trend_score, 2),
        "credit_score": round(credit_score, 2),
        "global_macro_score": round(global_macro_score, 2),
        "fed_direction": fed_direction,
        "macro_adj": round(macro_adj, 2),
        "macro": macro,
        "global_macro": global_macro,
        "valuation": valuation,
        "sentiment": sentiment,
        "narrative": inputs["narrative"],
    }
    return signal


def generate_market_signal(save: bool = True) -> MarketSignal:
    """适配器：读取数据库/配置/采集器 → 调纯函数 compute_market_signal → 追加 AI。

    save 控制是否立刻落库：编排层（update_pipeline）须以 save=False 调用，待止损
    覆盖完成后再经 save_market_signal 持久化**最终**信号，避免数据库存到止损前的
    旧版本（详见 apply_stop_loss / save_market_signal）。默认 True 仅为兼容历史独立调用。
    """
    cfg = load_config()

    # ── 数据采集（IO 边界，全部隔离在适配器）────────────────────
    macro = analyze_macro_cycle()
    valuation = calculate_valuation_metrics()
    sentiment = get_market_sentiment()
    trend_score = _trend_score()        # 价格趋势（读 market_data）
    credit_score = _credit_score()      # 信用利差（读 macro_data）

    # 第6因子：全球宏观综合评分（QDII资产规模权重的跨区域加权）
    try:
        from ..analyzers.global_macro_analyzer import analyze_global_macro, compute_global_macro_score
        global_macro = analyze_global_macro()
        global_macro_score = compute_global_macro_score(global_macro)
    except Exception:
        global_macro = {"available": False, "regions": {}}
        global_macro_score = 5.0

    fund_list_data = _get_fund_list()
    narrative = generate_narrative(valuation, sentiment, fund_list_data, cfg)

    from ..utils import provenance
    data_source = provenance.overall_mode()

    inputs = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "data_source": data_source,
        "data_quality": provenance.read_all(),
        "macro": macro,
        "valuation": valuation,
        "sentiment": sentiment,
        "trend_score": trend_score,
        "credit_score": credit_score,
        "global_macro": global_macro,
        "global_macro_score": global_macro_score,
        "narrative": narrative,
    }

    # ── 纯计算 ──────────────────────────────────────────────
    signal = compute_market_signal(inputs, cfg)

    # 用户偏好调整的诊断打印（IO，留在适配器）：用 classify_signal 复算原始档位与
    # 最终档位对比——单一真相源，不复制映射逻辑，不会与 compute 漂移。
    if signal.get("user_profile_applied"):
        _, raw_core, raw_sat, raw_cash = classify_signal(signal["timing_score"])
        final = (signal["core_allocation"], signal["satellite_allocation"], signal["cash_allocation"])
        if final != (raw_core, raw_sat, raw_cash):
            up = cfg.get("user_profile") or {}
            print(
                f"[用户偏好] {up.get('risk_tolerance','moderate')} / "
                f"{up.get('investment_horizon_years',10)}年 → "
                f"核心 {raw_core*100:.0f}%→{final[0]*100:.0f}%  "
                f"卫星 {raw_sat*100:.0f}%→{final[1]*100:.0f}%  "
                f"现金 {raw_cash*100:.0f}%→{final[2]*100:.0f}%"
            )

    # ── AI 阶段一：市场上下文分析（配置开关控制，IO/AI 留在适配器）──────
    ai_analysis = None
    cfg_ai = cfg.get("ai_analysis", {})
    if cfg_ai.get("enabled", False):
        skip_mock = cfg_ai.get("skip_on_mock_data", True)
        if not (skip_mock and data_source == "mock"):
            try:
                from ..ai.phase1_market_analyzer import MarketContextAnalyzer
                ai_analysis = MarketContextAnalyzer().analyze(signal)
                if ai_analysis:
                    signal["narrative"] = {
                        "insights": [ai_analysis.get("market_narrative", "")],
                        "ai_enhanced": True,
                        "source": "claude_phase1",
                    }
            except Exception as e:
                print(f"[AI Phase1] 跳过: {e}")
    signal["ai_analysis"] = ai_analysis

    if save:
        save_market_signal(signal)
    return signal


def apply_stop_loss(
    signal: MarketSignal,
    stop_loss_info: StopLossResult | None,
) -> MarketSignal:
    """止损覆盖（纯函数，不原地修改传入对象，返回新 signal）。

    只覆盖**决策层**（composite_signal/仓位/signal_color/stop_loss），不动事实层
    （MarketFacts）——从类型结构上保证「止损不污染原始事实」。无论是否触发都把
    stop_loss_info 附到信号上（关闭止损时为 None）；一旦触发，降至「减仓防守」档——
    仓位绝对值取自 POSITION_TIERS（单一真相源），**禁止在编排层硬编码 0.35/0.15/0.50**。
    这样数据库、返回值、组合、报告四处看到的都是同一份「止损后最终信号」。
    """
    new_signal: MarketSignal = dict(signal)  # type: ignore[assignment]
    new_signal["stop_loss"] = stop_loss_info
    if stop_loss_info and stop_loss_info.get("triggered"):
        core, satellite, cash = POSITION_TIERS["减仓防守"]
        new_signal["composite_signal"] = "减仓防守"
        new_signal["core_allocation"] = core
        new_signal["satellite_allocation"] = satellite
        new_signal["cash_allocation"] = cash
        new_signal["signal_color"] = "red"
        new_signal["stop_loss_triggered"] = True
    return new_signal


def save_market_signal(signal: Mapping[str, Any]) -> None:
    """持久化最终市场信号（止损覆盖后唯一落库入口）。

    与 generate_market_signal(save=False) 配合：同一日期只保存一次最终版本。
    实际写库收敛到 SignalRepository，本层不再直接拼 SQL。
    """
    signal_repository.save_signal(signal)


def _get_fund_list() -> list:
    df = read_table("fund_list")
    if df.empty:
        return []
    return df.to_dict("records")
