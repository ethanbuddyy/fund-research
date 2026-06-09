"""基金名称 / 代码模糊搜索

支持：
  - 代码精确匹配（直接返回）
  - 名称关键词搜索（fund_list 表 + CORE_QDII_FUNDS 静态库）
"""
from __future__ import annotations

from ..utils.database import read_table
from ..utils.fund_universe import CORE_QDII_FUNDS


def search_funds(query: str) -> list[dict]:
    """搜索基金，返回 [{"fund_code", "fund_name", "fund_type", "benchmark", "region", "source"}]。

    优先级：DB fund_list > CORE_QDII_FUNDS 静态库。
    query 可以是：代码（精确）、名称关键词（模糊）、基准关键词。
    """
    query = query.strip()
    results: list[dict] = []
    seen: set[str] = set()

    # ── 1. 从 DB fund_list 搜索 ──────────────────────────────
    df = read_table("fund_list")
    if not df.empty:
        q_lower = query.lower()
        for _, row in df.iterrows():
            code = str(row.get("fund_code", ""))
            name = str(row.get("fund_name", ""))
            bench = str(row.get("benchmark", ""))
            if (query == code
                    or q_lower in name.lower()
                    or q_lower in bench.lower()):
                if code not in seen:
                    results.append({
                        "fund_code":  code,
                        "fund_name":  name,
                        "fund_type":  str(row.get("fund_type", "")),
                        "benchmark":  bench,
                        "region":     str(row.get("region", "")),
                        "source":     "db",
                    })
                    seen.add(code)

    # ── 2. 从 CORE_QDII_FUNDS 静态库补充 ─────────────────────
    q_lower = query.lower()
    for f in CORE_QDII_FUNDS:
        code  = str(f["fund_code"])
        name  = str(f["fund_name"])
        bench = str(f.get("benchmark", ""))
        if code in seen:
            continue
        if (query == code
                or q_lower in name.lower()
                or q_lower in bench.lower()
                or q_lower in str(f.get("region", "")).lower()):
            results.append({
                "fund_code":  code,
                "fund_name":  name,
                "fund_type":  f.get("fund_type", ""),
                "benchmark":  bench,
                "region":     f.get("region", ""),
                "source":     "universe",
            })
            seen.add(code)

    return results


def resolve_fund_code(query: str) -> str | None:
    """将用户输入（代码或名称）解析为唯一基金代码。

    - 若 query 本身像代码（全数字/字母，≤8位）且有唯一匹配 → 直接返回
    - 若只有一条搜索结果 → 返回该代码
    - 多条结果 → 返回 None（调用方应展示列表让用户选择）
    """
    hits = search_funds(query)
    if not hits:
        return None
    if len(hits) == 1:
        return hits[0]["fund_code"]
    # 精确代码匹配
    for h in hits:
        if h["fund_code"] == query:
            return h["fund_code"]
    return None  # 多条，调用方处理
