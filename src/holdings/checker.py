"""持仓健康诊断引擎

接受用户自定义持仓（任意基金代码 + 权重），结合当前市场信号，
输出组合级健康报告。与推荐引擎完全独立，不修改现有推荐流程。

主入口：check_holdings(holdings, market_signal) -> dict
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional, cast

import pandas as pd

from ..utils.database import read_table
from ..utils.config import load_config
from ..utils.fund_universe import (
    classify_asset_class,
    holdings_adjusted_strategy_score,
    REGION_BY_CODE,
    EXPENSE_RATIO_BY_CODE,
    infer_region,
)


# ─────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────

@dataclass
class HoldingItem:
    fund_code: str
    weight: float                        # 用户输入的百分比，如 40.0
    fund_name: str = ""
    asset_class: str = "unknown"
    region: str = "未知"
    in_db: bool = False
    score: Optional[dict] = field(default=None, repr=False)
    expense_ratio: Optional[float] = None
    signal: Optional[str] = None        # 买入/持有/观望/回避
    strategy_score: float = 5.0
    issue: Optional[str] = None         # 单条基金级警告


# ─────────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────────

def check_holdings(
    holdings: list[dict],
    market_signal: dict,
    top_n: int = 5,
) -> dict:
    """持仓健康诊断。

    Args:
        holdings:      [{"fund_code": "519915", "weight": 40, "fund_name": "..."}, ...]
                       fund_code 可以是 "cash" 表示现金仓位。
        market_signal: generate_market_signal() 或 DB 最新信号的 dict。
        top_n:         与系统推荐 top-N 做 gap 对比。

    Returns:
        结构化诊断报告 dict，包含 holdings / analytics / gap / verdict。
    """
    items = _parse_holdings(holdings)
    items = _enrich_from_db(items, market_signal)
    analytics = _compute_analytics(items, market_signal)
    gap = _compute_gap(items, market_signal, top_n=top_n)
    verdict = _verdict(items, analytics, gap, market_signal)

    return {
        "composite_signal": market_signal.get("composite_signal", "未知"),
        "signal_date": market_signal.get("date", ""),
        "holdings": [asdict(i) for i in items],
        "analytics": analytics,
        "gap": gap,
        "verdict": verdict,
    }


# ─────────────────────────────────────────────────────────────
# Step 1 — 解析持仓输入
# ─────────────────────────────────────────────────────────────

def _parse_holdings(raw: list[dict]) -> list[HoldingItem]:
    items = []
    total_weight = sum(float(r.get("weight", 0)) for r in raw)
    if total_weight <= 0:
        raise ValueError("持仓权重之和必须大于 0")

    for r in raw:
        code = str(r.get("fund_code", "")).strip()
        if not code:
            continue
        weight = float(r.get("weight", 0))
        # 归一化到 100
        norm_weight = round(weight / total_weight * 100, 2)
        name = str(r.get("fund_name", "")).strip()

        if code.lower() == "cash":
            items.append(HoldingItem(
                fund_code="cash",
                weight=norm_weight,
                fund_name="现金",
                asset_class="cash",
                region="现金",
                in_db=False,
            ))
        else:
            items.append(HoldingItem(
                fund_code=code,
                weight=norm_weight,
                fund_name=name,
            ))
    return items


# ─────────────────────────────────────────────────────────────
# Step 2 — 从数据库富化
# ─────────────────────────────────────────────────────────────

def _enrich_from_db(items: list[HoldingItem], market_signal: dict) -> list[HoldingItem]:
    composite = market_signal.get("composite_signal", "标配稳健")

    fund_list_df = read_table("fund_list")
    scores_df = read_table("fund_scores")
    holdings_df = read_table("fund_holdings")

    fl_map: dict[str, dict] = {}
    if not fund_list_df.empty:
        for _, row in fund_list_df.iterrows():
            fl_map[str(row["fund_code"])] = row.to_dict()

    sc_map: dict[str, dict] = {}
    if not scores_df.empty:
        for _, row in scores_df.iterrows():
            sc_map[str(row["fund_code"])] = row.to_dict()

    # fund_holdings: 取每只基金的最新一条
    fh_map: dict[str, dict] = {}
    if not holdings_df.empty:
        try:
            latest = (
                holdings_df.sort_values("date")
                .groupby("fund_code")
                .last()
                .reset_index()
            )
            for _, row in latest.iterrows():
                fh_map[str(row["fund_code"])] = row.to_dict()
        except Exception:
            pass

    for item in items:
        if item.fund_code == "cash":
            continue

        code = item.fund_code

        # ── 来自 fund_list ─────────────────────────────
        fl = fl_map.get(code)
        if fl:
            item.in_db = True
            if not item.fund_name:
                item.fund_name = str(fl.get("fund_name", code))
            raw_er = fl.get("expense_ratio")
            if raw_er is not None:
                try:
                    item.expense_ratio = float(raw_er)
                except (TypeError, ValueError):
                    pass
            fund_type = str(fl.get("fund_type", ""))
            benchmark = str(fl.get("benchmark", ""))
        else:
            fund_type = ""
            benchmark = ""

        # expense_ratio fallback: 从 universe 字典补
        if item.expense_ratio is None and code in EXPENSE_RATIO_BY_CODE:
            item.expense_ratio = cast(float, EXPENSE_RATIO_BY_CODE[code])

        # ── 资产类别分类 ───────────────────────────────
        item.asset_class = classify_asset_class(
            fund_code=code,
            fund_type=fund_type,
            fund_name=item.fund_name,
            benchmark=benchmark,
        )

        # ── 地区推断 ───────────────────────────────────
        if code in REGION_BY_CODE:
            item.region = str(REGION_BY_CODE[code])
        else:
            item.region = infer_region(item.fund_name, benchmark)

        # ── 来自 fund_scores ───────────────────────────
        sc = sc_map.get(code)
        if sc:
            item.signal = str(sc.get("signal", ""))
            item.score = {
                "total_score":       _safe_float(sc.get("total_score")),
                "performance_score": _safe_float(sc.get("performance_score")),
                "risk_score":        _safe_float(sc.get("risk_score")),
                "strategy_score":    _safe_float(sc.get("strategy_score")),
                "consistency_score": _safe_float(sc.get("consistency_score")),
                "cost_score":        _safe_float(sc.get("cost_score")),
            }

        # ── 策略匹配分（复用 holdings_adjusted_strategy_score）──
        fh = fh_map.get(code, {})
        item.strategy_score = holdings_adjusted_strategy_score(
            asset_class=item.asset_class,
            composite_signal=composite,
            stock_ratio=_safe_float(fh.get("stock_ratio")),
            bond_ratio=_safe_float(fh.get("bond_ratio")),
            cash_ratio=_safe_float(fh.get("cash_ratio")),
        )

        # ── 基金级警告 ─────────────────────────────────
        cfg = load_config()
        max_er = cfg.get("strategy_params", {}).get("cost_filter", {}).get("max_expense_ratio", 0.015)
        if item.signal == "回避":
            item.issue = f"系统信号为「回避」，评分过低"
        elif item.expense_ratio is not None and item.expense_ratio > max_er:
            item.issue = f"费率 {item.expense_ratio*100:.2f}% 超过上限 {max_er*100:.1f}%"

    return items


# ─────────────────────────────────────────────────────────────
# Step 3 — 组合级分析
# ─────────────────────────────────────────────────────────────

def _compute_analytics(items: list[HoldingItem], market_signal: dict) -> dict:
    non_cash = [i for i in items if i.fund_code != "cash"]
    cash_item = next((i for i in items if i.fund_code == "cash"), None)
    cash_pct = cash_item.weight if cash_item else 0.0

    # 资产类别分布
    class_dist: dict[str, float] = {}
    for i in items:
        class_dist[i.asset_class] = class_dist.get(i.asset_class, 0.0) + i.weight

    # 地区分布
    region_dist: dict[str, float] = {}
    for i in items:
        region_dist[i.region] = region_dist.get(i.region, 0.0) + i.weight

    # HHI（不含现金；现金单独列示）
    non_cash_total = sum(i.weight for i in non_cash) or 1.0
    hhi_classes = {k: v for k, v in class_dist.items() if k != "cash"}
    hhi = sum((w / non_cash_total) ** 2 for w in hhi_classes.values()) if hhi_classes else 1.0

    # 加权综合评分（仅有评分数据的基金）
    scored = [i for i in non_cash if i.score and i.score.get("total_score") is not None]
    scored_weight = sum(i.weight for i in scored) or None
    weighted_score = (
        sum(i.score["total_score"] * i.weight for i in scored if i.score) / scored_weight
        if scored_weight else None
    )

    # 加权策略匹配分
    weighted_strategy = sum(i.strategy_score * i.weight for i in non_cash) / non_cash_total

    # 加权费率（仅有费率数据的基金）
    rated = [i for i in non_cash if i.expense_ratio is not None]
    rated_weight = sum(i.weight for i in rated) or None
    weighted_er = (
        sum((i.expense_ratio or 0.0) * i.weight for i in rated) / rated_weight
        if rated_weight else None
    )

    # DB 覆盖率
    in_db_weight = sum(i.weight for i in non_cash if i.in_db)
    in_db_coverage = in_db_weight / non_cash_total * 100 if non_cash_total > 0 else 0.0

    return {
        "asset_class_distribution": class_dist,
        "region_distribution": region_dist,
        "cash_pct": round(cash_pct, 1),
        "recommended_cash_pct": round(market_signal.get("cash_allocation", 0.10) * 100, 1),
        "weighted_score": round(weighted_score, 1) if weighted_score is not None else None,
        "weighted_strategy_score": round(weighted_strategy, 2),
        "weighted_expense_ratio": round(weighted_er * 100, 3) if weighted_er is not None else None,
        "hhi": round(hhi, 3),
        "in_db_coverage_pct": round(in_db_coverage, 1),
    }


# ─────────────────────────────────────────────────────────────
# Step 4 — Gap 分析
# ─────────────────────────────────────────────────────────────

def _compute_gap(items: list[HoldingItem], market_signal: dict, top_n: int = 5) -> dict:
    scores_df = read_table("fund_scores")
    if scores_df.empty:
        return {
            "overlap_count": 0,
            "overlap_codes": [],
            "in_recommendation": [],
            "missing_recommended": [],
            "not_in_recommendation": [],
        }

    top_codes = (
        scores_df.nlargest(top_n, "total_score")["fund_code"]
        .astype(str)
        .tolist()
    )
    top_names = {}
    for _, row in scores_df.nlargest(top_n, "total_score").iterrows():
        top_names[str(row["fund_code"])] = str(row.get("fund_name", row["fund_code"]))

    user_codes = {i.fund_code for i in items if i.fund_code != "cash"}
    top_set = set(top_codes)

    overlap = sorted(user_codes & top_set)
    missing = [c for c in top_codes if c not in user_codes]
    not_in = sorted(user_codes - top_set)

    return {
        "overlap_count": len(overlap),
        "overlap_codes": overlap,
        "in_recommendation": [{"code": c, "name": top_names.get(c, c)} for c in overlap],
        "missing_recommended": [{"code": c, "name": top_names.get(c, c)} for c in missing],
        "not_in_recommendation": list(not_in),
    }


# ─────────────────────────────────────────────────────────────
# Step 5 — 健康裁决
# ─────────────────────────────────────────────────────────────

def _verdict(
    items: list[HoldingItem],
    analytics: dict,
    gap: dict,
    market_signal: dict,
) -> dict:
    issues: list[str] = []
    strengths: list[str] = []
    actions: list[str] = []
    severity: list[str] = []   # "red" / "yellow"

    hhi = analytics["hhi"]
    w_strategy = analytics["weighted_strategy_score"]
    w_score = analytics["weighted_score"]
    w_er = analytics["weighted_expense_ratio"]
    cash_pct = analytics["cash_pct"]
    rec_cash = analytics["recommended_cash_pct"]
    in_db_cov = analytics["in_db_coverage_pct"]
    composite = market_signal.get("composite_signal", "标配稳健")

    cfg = load_config()
    max_er_pct = cfg.get("strategy_params", {}).get("cost_filter", {}).get("max_expense_ratio", 0.015) * 100

    # ── RED 条件 ──────────────────────────────────────────────
    for item in items:
        if item.fund_code != "cash" and item.signal == "回避" and item.weight >= 15:
            issues.append(f"「{item.fund_name or item.fund_code}」系统信号为回避，权重 {item.weight:.1f}% 偏高")
            severity.append("red")

    if hhi > 0.7:
        top_class = max(
            {k: v for k, v in analytics["asset_class_distribution"].items() if k != "cash"},
            key=lambda k: analytics["asset_class_distribution"][k],
            default="",
        )
        issues.append(f"资产集中度过高（HHI={hhi:.2f}），{top_class} 占比超 80%，分散不足")
        severity.append("red")

    if w_strategy < 5.0:
        issues.append(f"持仓策略匹配分 {w_strategy:.1f}/10，与当前「{composite}」信号严重不匹配")
        severity.append("red")

    # ── YELLOW 条件 ───────────────────────────────────────────
    if w_er is not None and w_er > max_er_pct:
        issues.append(f"加权平均费率 {w_er:.2f}% 超过推荐上限 {max_er_pct:.1f}%，长期将侵蚀收益")
        severity.append("yellow")

    cash_gap = cash_pct - rec_cash
    if abs(cash_gap) > 20:
        direction = "高于" if cash_gap > 0 else "低于"
        issues.append(
            f"现金 {cash_pct:.1f}% {direction}市场建议 {rec_cash:.1f}%（差值 {abs(cash_gap):.1f}%），存在{'机会成本' if cash_gap > 0 else '仓位过重风险'}"
        )
        severity.append("yellow")

    if gap["overlap_count"] == 0 and len([i for i in items if i.fund_code != "cash"]) > 0:
        issues.append("持仓与系统推荐 Top-5 无重叠，建议参考系统推荐调整方向")
        severity.append("yellow")

    if in_db_cov < 50:
        issues.append(f"仅 {in_db_cov:.0f}% 的持仓权重有评分数据，诊断结果受限（可先运行 python run.py 更新数据）")
        severity.append("yellow")

    # ── 亮点 ─────────────────────────────────────────────────
    if w_score is not None and w_score >= 65:
        strengths.append(f"持仓基金加权评分 {w_score:.1f} 分，整体质量良好")

    if hhi < 0.4:
        strengths.append(f"资产集中度低（HHI={hhi:.2f}），分散效果较好")

    if gap["overlap_count"] >= 2:
        names = "、".join(r["name"] for r in gap["in_recommendation"][:2])
        strengths.append(f"{gap['overlap_count']} 只持仓与系统推荐重叠（{names}），核心方向一致")

    if w_strategy >= 7.5:
        strengths.append(f"持仓策略与当前「{composite}」信号高度匹配（{w_strategy:.1f}/10）")

    # ── 建议操作 ──────────────────────────────────────────────
    if "red" in severity:
        if hhi > 0.7:
            actions.append("降低单一资产类别集中度，增加其他地区/类别的 QDII 以改善分散")
        if any("回避" in iss for iss in issues):
            actions.append("评分低的回避信号基金，建议分批减仓或替换为系统推荐基金")
        if w_strategy < 5.0:
            actions.append(f"当前市场信号为「{composite}」，持仓策略方向偏差较大，建议按推荐组合方向调整仓位")

    if gap["missing_recommended"]:
        names = "、".join(r["name"] for r in gap["missing_recommended"][:3])
        actions.append(f"系统推荐 Top-5 中你尚未持有：{names}，可作为参考调仓候选")

    if cash_gap > 20:
        actions.append(f"现金偏多，可视市场变化将约 {min(cash_gap, 30):.0f}% 现金分批换回宽基 QDII")
    elif cash_gap < -20:
        actions.append("权益仓位偏高，建议适当提高现金或债券类资产比例以控制回撤风险")

    # ── 综合裁决 ─────────────────────────────────────────────
    if "red" in severity:
        overall = "red"
    elif "yellow" in severity:
        overall = "yellow"
    else:
        overall = "green"

    return {
        "overall": overall,
        "issues": issues,
        "strengths": strengths,
        "actions": actions,
    }


# ─────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────

def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        import math
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def load_signal_from_db() -> dict:
    """从数据库读取最新一条市场信号（经 SignalRepository，不触发网络采集）。"""
    from ..utils.signal_repository import load_latest_signal
    row = load_latest_signal()
    if not row:
        return {}
    # 补全 allocation 字段（DB 存的是小数）
    for k in ("core_allocation", "satellite_allocation", "cash_allocation"):
        v = row.get(k)
        if v is not None:
            try:
                row[k] = float(v)
            except (TypeError, ValueError):
                pass
    return row


def parse_holdings_str(s: str) -> list[dict]:
    """解析内联持仓字符串，格式：'code1:weight1,code2:weight2,...'。"""
    result = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            code, w = part.split(":", 1)
            result.append({"fund_code": code.strip(), "weight": float(w.strip())})
        else:
            raise ValueError(f"无法解析持仓格式：{part!r}，应为 code:weight")
    return result
