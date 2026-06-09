"""组合浮亏追踪与止损检测

从上次快照的持仓权重+净值出发，计算本期加权回报；累计到运行中的组合净值（初始 100），
与历史高水位比较得出回撤幅度。回撤超过配置阈值时返回 triggered=True，
供 update_pipeline 强制将信号调至"减仓防守"。

状态读写统一走 portfolio_state_store（唯一真相源），本模块不再自行拼接路径：
  上期快照由编排层显式传入（previous_portfolio），净值/高水位经 store 读写。
"""
from .portfolio_state_store import load_nav_state, save_nav_state, load_previous_portfolio
from ..domain.types import StopLossResult


def update_and_check(stop_loss_pct: float = 0.15,
                     previous_portfolio: dict | None = None) -> StopLossResult:
    """计算组合浮亏状态，返回止损检测结果。

    Args:
        stop_loss_pct: 止损阈值（小数），如 0.15 表示回撤超 15% 触发
        previous_portfolio: 上期推荐组合快照原文（编排层一次性读入并显式传入）。
            为 None 时退回经 store 自行读取一次，兼容独立调用。

    Returns:
        StopLossResult（triggered/portfolio_nav/high_water_mark/drawdown_pct/
        threshold_pct/period_return_pct/funds_tracked/note）。
    """
    threshold = -abs(stop_loss_pct * 100)
    base: StopLossResult = {
        "triggered": False,
        "portfolio_nav": 100.0,
        "high_water_mark": 100.0,
        "drawdown_pct": 0.0,
        "threshold_pct": threshold,
        "period_return_pct": 0.0,
        "funds_tracked": 0,
        "note": "",
    }

    snapshot = previous_portfolio if previous_portfolio is not None else load_previous_portfolio()
    if snapshot is None:
        base["note"] = "首次运行，无历史快照，止损追踪将从下次运行开始"
        return base

    # 提取上次持仓的权重（pct）和基准净值
    all_funds: dict[str, dict] = {}
    for bucket in ("core", "satellite"):
        for code, info in (snapshot.get(bucket) or {}).items():
            if not isinstance(info, dict):
                continue
            weight_pct = float(info.get("weight_pct") or 0.0)
            prev_nav = info.get("nav")
            if weight_pct > 0 and prev_nav is not None:
                all_funds[code] = {"weight": weight_pct / 100.0, "prev_nav": float(prev_nav)}

    if not all_funds:
        base["note"] = "快照中无权重/净值信息（旧格式），止损追踪将从下次运行开始"
        return base

    # 查询各基金最新净值，计算本期收益
    fund_returns = _query_period_returns(all_funds)

    if not fund_returns:
        base["note"] = "无法从数据库获取基金最新净值，跳过本次止损检测"
        return base

    # 加权本期收益（仅统计有净值数据的基金，按权重归一化）
    total_weight = sum(all_funds[c]["weight"] for c in fund_returns)
    weighted_return = (
        sum(all_funds[c]["weight"] * fund_returns[c] for c in fund_returns) / total_weight
        if total_weight > 0 else 0.0
    )

    # 更新累计净值和高水位
    nav_data = load_nav_state()
    new_nav = nav_data["nav"] * (1.0 + weighted_return)
    new_hwm = max(nav_data["hwm"], new_nav)
    drawdown = (new_nav / new_hwm - 1.0) * 100.0 if new_hwm > 0 else 0.0

    save_nav_state(new_nav, new_hwm)

    triggered = drawdown < threshold
    note = (
        f"组合净值 {new_nav:.2f}（高水位 {new_hwm:.2f}），"
        f"回撤 {drawdown:.1f}%，"
        f"本期加权收益 {weighted_return*100:.1f}%，"
        f"追踪 {len(fund_returns)}/{len(all_funds)} 只基金"
    )
    if triggered:
        note += f"  【⚠️ 止损触发！回撤超过阈值 {threshold:.0f}%，强制降仓】"

    return {
        "triggered": triggered,
        "portfolio_nav": round(new_nav, 4),
        "high_water_mark": round(new_hwm, 4),
        "drawdown_pct": round(drawdown, 2),
        "threshold_pct": threshold,
        "period_return_pct": round(weighted_return * 100, 2),
        "funds_tracked": len(fund_returns),
        "note": note,
    }


def _query_period_returns(all_funds: dict) -> dict[str, float]:
    """查询各基金最新净值（经 FundRepository），计算相对上次快照净值的变化率。"""
    from .fund_repository import get_latest_navs
    nav_map = get_latest_navs(all_funds.keys())
    returns = {}
    for code, info in all_funds.items():
        prev_nav = info["prev_nav"]
        latest = nav_map.get(code)
        if latest is not None and latest > 0 and prev_nav > 0:
            returns[code] = latest / prev_nav - 1.0
    return returns
