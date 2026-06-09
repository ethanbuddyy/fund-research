"""基金数据仓储：最新净值读取入口（窄接口）。

把「按基金取最新净值」这条原始 SQL 收敛到这一处，供组合快照构建（portfolio.py）
与止损浮亏追踪（portfolio_tracker.py）共用，避免两处各写一遍 get_connection。
基础数据/评分/持仓等仍经 read_table 读取（本阶段不扩大改动面）。
"""
from collections.abc import Iterable


def get_latest_navs(fund_codes: Iterable[str]) -> dict[str, float]:
    """查各基金 fund_nav_history 中最新一条净值，返回 {code: nav}。

    无净值/查询失败的基金不出现在结果中；整体异常返回空 dict（不阻断主流程）。
    """
    try:
        from .database import get_connection
        conn = get_connection()
        try:
            nav_map: dict[str, float] = {}
            for code in fund_codes:
                row = conn.execute(
                    "SELECT nav FROM fund_nav_history WHERE fund_code=? ORDER BY date DESC LIMIT 1",
                    (code,),
                ).fetchone()
                if row and row[0] is not None:
                    nav_map[code] = float(row[0])
        finally:
            conn.close()  # 异常路径也要关连接
        return nav_map
    except Exception:
        return {}
