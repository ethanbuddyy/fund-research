"""组合状态集中存储（快照与净值的唯一真相源）。

把两份运行时状态文件的「路径拼接 + JSON 读写」全部收敛到这一处，消除
portfolio.py / portfolio_tracker.py / report_builder.py 各自直接读盘所带来的
隐式时序耦合（谁先读、谁先写决定正确性）。上层只通过下列四个函数访问状态，
读取时点与提交时点由编排层（update_pipeline）显式控制。

数据文件：
  data/portfolio_runtime_state.json — 组合快照 + 止损净值，一次原子替换提交。
  data/portfolio_snapshot.json / portfolio_nav.json — 旧版只读兼容。

失效原则：
  - 文件缺失 = 首次运行，属正常，不告警。
  - 文件存在却解析失败 = 损坏，打印 [WARN] 后回退默认值，**绝不静默重置**。
  - 写入失败会让下次换仓门槛 / 止损追踪从错误基准重来，同样必须可见。
"""
import json
import os
from datetime import datetime
from pathlib import Path

from ..domain.types import PortfolioState

_DATA_DIR = Path(__file__).parent.parent.parent / "data"
_SNAPSHOT_PATH = _DATA_DIR / "portfolio_snapshot.json"
_NAV_PATH = _DATA_DIR / "portfolio_nav.json"
_RUNTIME_PATH = _DATA_DIR / "portfolio_runtime_state.json"


def _read_runtime_state() -> dict:
    if not _RUNTIME_PATH.exists():
        return {}
    try:
        data = json.loads(_RUNTIME_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"[WARN] 组合运行状态损坏，将尝试旧版状态文件: {e}")
        return {}


def _atomic_write_json(path: Path, payload: dict) -> None:
    """同目录临时文件 + os.replace，避免进程中断留下半截 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def load_previous_portfolio() -> dict | None:
    """读取上期推荐组合快照原文（核心/卫星桶的 {code: {score, weight_pct, nav}}）。

    首次运行（文件缺失）返回 None（正常，不告警）；文件损坏同样返回 None，
    但会打印告警——换仓门槛与止损追踪本期将从空基准重来，必须让用户可见。
    """
    runtime = _read_runtime_state()
    snapshot = runtime.get("portfolio")
    if isinstance(snapshot, dict):
        return snapshot
    if not _SNAPSHOT_PATH.exists():
        return None
    try:
        return json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[WARN] 组合快照损坏，换仓门槛/止损追踪本期将从空基准重来: {e}")
        return None


def save_current_portfolio(snapshot: PortfolioState) -> bool:
    """提交本期推荐组合快照（供下次运行的换仓门槛与止损追踪）。

    必须在本期所有「与上期对比」的展示数据计算完成后再调用，
    否则会退化为「本期与本期比较」。写失败必须可见（不可降级）。
    """
    try:
        runtime = _read_runtime_state()
        runtime["portfolio"] = snapshot
        runtime.setdefault("nav", load_nav_state())
        _atomic_write_json(_RUNTIME_PATH, runtime)
        return True
    except Exception as e:
        print(f"[WARN] 组合快照保存失败（将影响下次换仓门槛/止损追踪）: {e}")
        return False


def load_nav_state() -> dict:
    """读取累计净值/高水位 {nav, hwm}；缺失/损坏均回退到初始基准 100。"""
    runtime = _read_runtime_state()
    nav = runtime.get("nav")
    if isinstance(nav, dict):
        try:
            return {
                "nav": float(nav.get("nav", 100.0)),
                "hwm": float(nav.get("hwm", 100.0)),
            }
        except (TypeError, ValueError):
            print("[WARN] 组合运行状态中的止损净值无效，将尝试旧版状态文件")
    if not _NAV_PATH.exists():
        return {"nav": 100.0, "hwm": 100.0}  # 首次运行属正常
    try:
        data = json.loads(_NAV_PATH.read_text(encoding="utf-8"))
        return {"nav": float(data.get("nav", 100.0)), "hwm": float(data.get("hwm", 100.0))}
    except Exception as e:
        print(f"[WARN] 止损净值数据损坏，回撤基准重置为 100: {e}")
        return {"nav": 100.0, "hwm": 100.0}


def save_nav_state(nav: float, high_water_mark: float) -> bool:
    """保存累计净值与高水位。写失败会让下次回撤从错误基准重算，必须可见。"""
    try:
        runtime = _read_runtime_state()
        runtime["nav"] = {
            "nav": round(nav, 6),
            "hwm": round(high_water_mark, 6),
            "updated": datetime.now().strftime("%Y-%m-%d"),
        }
        _atomic_write_json(_RUNTIME_PATH, runtime)
        return True
    except Exception as e:
        print(f"[WARN] 止损净值数据保存失败（将影响下次回撤计算基准）: {e}")
        return False


def commit_runtime_state(
    snapshot: PortfolioState,
    nav_state: dict[str, float] | None = None,
) -> bool:
    """一次原子替换同时提交组合快照与止损净值，避免两份状态跨期。"""
    try:
        runtime = _read_runtime_state()
        runtime["portfolio"] = snapshot
        if nav_state is not None:
            runtime["nav"] = {
                "nav": round(float(nav_state["nav"]), 6),
                "hwm": round(float(nav_state["hwm"]), 6),
                "updated": datetime.now().strftime("%Y-%m-%d"),
            }
        else:
            runtime.setdefault("nav", load_nav_state())
        _atomic_write_json(_RUNTIME_PATH, runtime)
        return True
    except Exception as e:
        print(f"[WARN] 组合运行状态提交失败，本期快照和止损净值均未推进: {e}")
        return False
