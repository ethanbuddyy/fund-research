"""组合浮亏追踪与止损检测

从上次快照的持仓权重+净值出发，计算本期加权回报；累计到运行中的组合净值（初始 100），
与历史高水位比较得出回撤幅度。回撤超过配置阈值时返回 triggered=True，
供 update_pipeline 强制将信号调至"减仓防守"。

数据文件：
  data/portfolio_snapshot.json  — 上次推荐持仓（portfolio.py 写入）
  data/portfolio_nav.json       — 累计净值 + 高水位（本模块维护）
"""
import json
from datetime import datetime
from pathlib import Path

_SNAPSHOT_PATH = Path(__file__).parent.parent.parent / "data" / "portfolio_snapshot.json"
_NAV_PATH = Path(__file__).parent.parent.parent / "data" / "portfolio_nav.json"


def update_and_check(stop_loss_pct: float = 0.15) -> dict:
    """计算组合浮亏状态，返回止损检测结果。

    Args:
        stop_loss_pct: 止损阈值（小数），如 0.15 表示回撤超 15% 触发

    Returns:
        {triggered, portfolio_nav, high_water_mark, drawdown_pct,
         threshold_pct, period_return_pct, funds_tracked, note}
    """
    threshold = -abs(stop_loss_pct * 100)
    base = {
        "triggered": False,
        "portfolio_nav": 100.0,
        "high_water_mark": 100.0,
        "drawdown_pct": 0.0,
        "threshold_pct": threshold,
        "period_return_pct": 0.0,
        "funds_tracked": 0,
        "note": "",
    }

    snapshot = _load_snapshot()
    if snapshot is None:
        return {**base, "note": "首次运行，无历史快照，止损追踪将从下次运行开始"}

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
        return {**base, "note": "快照中无权重/净值信息（旧格式），止损追踪将从下次运行开始"}

    # 查询各基金最新净值，计算本期收益
    fund_returns = _query_period_returns(all_funds)

    if not fund_returns:
        return {**base, "note": "无法从数据库获取基金最新净值，跳过本次止损检测"}

    # 加权本期收益（仅统计有净值数据的基金，按权重归一化）
    total_weight = sum(all_funds[c]["weight"] for c in fund_returns)
    weighted_return = (
        sum(all_funds[c]["weight"] * fund_returns[c] for c in fund_returns) / total_weight
        if total_weight > 0 else 0.0
    )

    # 更新累计净值和高水位
    nav_data = _load_nav_data()
    new_nav = nav_data["nav"] * (1.0 + weighted_return)
    new_hwm = max(nav_data["hwm"], new_nav)
    drawdown = (new_nav / new_hwm - 1.0) * 100.0 if new_hwm > 0 else 0.0

    _save_nav_data(new_nav, new_hwm)

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
    """查询各基金最新净值，计算相对上次快照净值的变化率。"""
    try:
        from .database import get_connection
        conn = get_connection()
        returns = {}
        for code, info in all_funds.items():
            prev_nav = info["prev_nav"]
            row = conn.execute(
                "SELECT nav FROM fund_nav_history WHERE fund_code=? ORDER BY date DESC LIMIT 1",
                (code,),
            ).fetchone()
            if row and row[0] is not None and float(row[0]) > 0 and prev_nav > 0:
                returns[code] = float(row[0]) / prev_nav - 1.0
        conn.close()
        return returns
    except Exception:
        return {}


def _load_snapshot() -> dict | None:
    try:
        if _SNAPSHOT_PATH.exists():
            return json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def _load_nav_data() -> dict:
    try:
        if _NAV_PATH.exists():
            data = json.loads(_NAV_PATH.read_text(encoding="utf-8"))
            return {"nav": float(data.get("nav", 100.0)), "hwm": float(data.get("hwm", 100.0))}
    except Exception:
        pass
    return {"nav": 100.0, "hwm": 100.0}


def _save_nav_data(nav: float, hwm: float):
    try:
        _NAV_PATH.parent.mkdir(parents=True, exist_ok=True)
        _NAV_PATH.write_text(
            json.dumps(
                {"nav": round(nav, 6), "hwm": round(hwm, 6),
                 "updated": datetime.now().strftime("%Y-%m-%d")},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception as e:
        # 净值/高水位持久化失败会让下次回撤计算从错误基准重新开始，必须可见。
        print(f"[WARN] 止损净值数据保存失败（将影响下次回撤计算基准）: {e}")
