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
        conn.execute(
            """INSERT INTO collection_meta (source, mode, rows, detail, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'))
               ON CONFLICT(source) DO UPDATE SET
                 mode=excluded.mode, rows=excluded.rows,
                 detail=excluded.detail, updated_at=excluded.updated_at""",
            (source, mode, int(rows), detail),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # provenance 记录失败不应影响主流程


def read_all() -> dict:
    """返回 {source: {mode, rows, detail, updated_at}}。"""
    try:
        conn = get_connection()
        cur = conn.execute("SELECT source, mode, rows, detail, updated_at FROM collection_meta")
        out = {r["source"]: {"mode": r["mode"], "rows": r["rows"],
                             "detail": r["detail"], "updated_at": r["updated_at"]}
               for r in cur.fetchall()}
        conn.close()
        return out
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


def banner() -> str:
    """给 CLI 用的一行数据真实性提示。"""
    mode = overall_mode()
    meta = read_all()
    parts = []
    for src in ["macro", "market", "fund", "valuation"]:
        if src in meta:
            parts.append(f"{src}={meta[src]['mode']}")
    detail = "  ".join(parts)
    if mode == REAL:
        return f"[数据来源] ✅ 全部真实数据  ({detail})"
    elif mode == PARTIAL:
        return f"[数据来源] ⚠️ 部分真实/近似数据，谨慎参考  ({detail})"
    else:
        return f"[数据来源] ❌ 含模拟数据，仅供界面演示、不可用于实际决策  ({detail})"
