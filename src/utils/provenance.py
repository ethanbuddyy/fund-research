"""数据来源(provenance)记录与聚合。

每个采集器在运行后记录自己用的是真实数据还是模拟数据，
信号层据此向用户明确标注「本次建议基于真实/部分真实/模拟数据」，
避免随机模拟数据被当成真实行情输出投资建议。

刻意不依赖 pandas，直接用 sqlite3，保证最轻量、随处可调用。
"""
from .database import get_connection

# 模式取值
REAL = "real"        # 真实数据（API / 已下载的CSV种子）
PARTIAL = "partial"  # 部分真实（如估值用了价格近似）
MOCK = "mock"        # 随机模拟，仅供界面演示，不可用于决策

_PRIORITY = {REAL: 0, PARTIAL: 1, MOCK: 2}


def record(source: str, mode: str, rows: int = 0, detail: str = "") -> None:
    """记录某数据源本次采集的模式。source 如 macro/market/fund/valuation。"""
    try:
        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO collection_meta (source, mode, rows, detail, updated_at)
                   VALUES (?, ?, ?, ?, datetime('now'))
                   ON CONFLICT(source) DO UPDATE SET
                     mode=excluded.mode, rows=excluded.rows,
                     detail=excluded.detail, updated_at=excluded.updated_at""",
                (source, mode, int(rows), detail),
            )
            conn.commit()
        finally:
            conn.close()  # 异常路径也要关连接，避免句柄泄漏
    except Exception:
        pass  # provenance 记录失败不应影响主流程


def read_all() -> dict:
    """返回 {source: {mode, rows, detail, updated_at}}。"""
    try:
        conn = get_connection()
        try:
            cur = conn.execute("SELECT source, mode, rows, detail, updated_at FROM collection_meta")
            return {r["source"]: {"mode": r["mode"], "rows": r["rows"],
                                  "detail": r["detail"], "updated_at": r["updated_at"]}
                    for r in cur.fetchall()}
        finally:
            conn.close()  # 异常路径也要关连接，避免句柄泄漏
    except Exception:
        return {}


def overall_mode() -> str:
    """聚合所有数据源：任一 mock → 'mock'；任一 partial → 'partial'；全 real → 'real'。

    采集的关键源(macro/market/fund)只要有一个是 mock，就认为整体不可用于决策。
    """
    meta = read_all()
    if not meta:
        return MOCK  # 没有任何采集记录，保守视为模拟
    worst = REAL
    for info in meta.values():
        m = info.get("mode", MOCK)
        if _PRIORITY.get(m, 2) > _PRIORITY.get(worst, 0):
            worst = m
    return worst


def check_staleness(max_days: dict | None = None) -> list[str]:
    """检查各数据源的最后更新时间，返回超期警告列表。
    max_days: {source: 最大允许天数}，默认 macro=7, market=3, fund=7, valuation=7。
    """
    import datetime
    defaults = {"macro": 7, "market": 3, "fund": 7, "valuation": 7}
    thresholds = {**defaults, **(max_days or {})}
    meta = read_all()
    warnings = []
    now = datetime.datetime.utcnow()
    for src, max_d in thresholds.items():
        if src not in meta:
            continue
        updated_at = meta[src].get("updated_at")
        if not updated_at:
            continue
        try:
            last = datetime.datetime.fromisoformat(updated_at.replace("Z", ""))
            delta = (now - last).days
            if delta > max_d:
                warnings.append(f"{src} 数据已 {delta} 天未更新（阈值 {max_d} 天）")
        except Exception:
            pass
    return warnings


def banner() -> str:
    """给 CLI 用的一行数据真实性提示，包含过期警告。"""
    mode = overall_mode()
    meta = read_all()
    parts = []
    for src in ["macro", "market", "fund", "valuation"]:
        if src in meta:
            parts.append(f"{src}={meta[src]['mode']}")
    detail = "  ".join(parts)

    stale = check_staleness()
    stale_str = ("  ⚠️ 过期警告: " + "; ".join(stale)) if stale else ""

    if mode == REAL:
        return f"[数据来源] ✅ 全部真实数据  ({detail}){stale_str}"
    elif mode == PARTIAL:
        return f"[数据来源] ⚠️ 部分真实/近似数据，谨慎参考  ({detail}){stale_str}"
    else:
        return f"[数据来源] ❌ 含模拟数据，仅供界面演示、不可用于实际决策  ({detail}){stale_str}"
