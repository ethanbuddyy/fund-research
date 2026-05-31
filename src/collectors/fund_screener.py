"""规则驱动的 QDII 基金池筛选器。

替代手工硬编码的核心基金清单，用可复核的客观规则从全市场 QDII 中筛选：
  1) 数据源：东方财富 QDII 排行接口（一次请求拿到全部 QDII 的代码/名称/各期收益/
     成立日期/费率）。
  2) 过滤：成立满 N 年、具备业绩记录、费率达标。
  3) 分类：按名称客观推断 基准/地区/资产类别。
  4) 去重：每个底层指数只保留最优的若干只（消除“5只标普500”这类冗余）。
  5) 排序+上限：按指定指标排序，控制池子规模与多样性。

设计：纯规则核心（解析/过滤/去重/排序）只依赖 stdlib + fund_universe，可离线单测；
网络抓取与落库为惰性导入，失败时返回空列表，由调用方回退到现有核心池。
"""
import re
from datetime import datetime
from ..utils.fund_universe import (
    infer_benchmark, infer_region, classify_asset_class, EXPENSE_RATIO_BY_CODE,
)

_RANK_URL = "https://fund.eastmoney.com/data/rankhandler.aspx"
_HEADERS = {
    "Referer": "https://fund.eastmoney.com/data/fundranking.html",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

# 排行 datas 字段索引（已对真实接口核验，25字段）
_IDX = {"code": 0, "name": 1, "nav_date": 3, "ret_1y": 11, "ret_3y": 13,
        "ret_since": 15, "inception": 16, "fee": 20}

_DEFAULTS = {
    "enabled": True,
    "min_inception_years": 2.0,
    "require_3y_record": False,
    "max_purchase_fee": 0.015,
    "per_benchmark_keep": 2,
    "max_pool_size": 30,
    "rank_by": "return_3y",      # return_1y / return_3y / return_since（3年更可比，不偏袒老基金）
    "min_aum_yi": 0,             # 规模下限(亿)，>0 时需 pingzhongdata 富集
}


# ── 对外主入口 ──────────────────────────────────────────

def screen_funds(cfg: dict = None) -> list:
    """返回筛选后的基金池 list[dict]；禁用/抓取失败/requests缺失时返回 []（调用方回退）。"""
    from ..utils.config import load_config
    full = cfg or load_config()
    sc = {**_DEFAULTS, **(full.get("fund_screener", {}) or {})}

    if not sc.get("enabled", True):
        return []

    try:
        import requests
    except ImportError:
        print("[WARN] requests 未安装，跳过规则筛选，回退核心池")
        return []

    text = _fetch_rank(requests)
    if not text:
        print("[WARN] QDII 排行获取失败，回退核心池")
        return []

    candidates = _parse_rank_rows(text)
    if not candidates:
        return []

    today = datetime.now().date()
    filtered = apply_filters(candidates, sc, today)
    pool = classify_and_dedup(filtered, sc)
    print(f"[OK] 规则筛选：候选 {len(candidates)} → 过滤后 {len(filtered)} → 去重定池 {len(pool)} 只")
    return pool


# ── 抓取与解析（纯解析部分可离线测）──────────────────────

def _fetch_rank(requests) -> str:
    params = {"op": "ph", "dt": "kf", "ft": "qdii", "rs": "", "gs": "0",
              "sc": "zzf", "st": "desc", "pi": "1", "pn": "300"}
    try:
        r = requests.get(_RANK_URL, params=params, headers=_HEADERS, timeout=20)
        return r.text if r.status_code == 200 else ""
    except Exception as e:
        print(f"[WARN] QDII 排行请求异常: {e}")
        return ""


def _parse_rank_rows(text: str) -> list:
    """解析 rankhandler 返回的 datas 数组 → 候选 dict 列表。"""
    m = re.search(r"datas:\s*\[(.*?)\]\s*,\s*allRecords", text, re.S)
    if not m:
        return []
    rows = re.findall(r'"(.*?)"', m.group(1))
    out = []
    for row in rows:
        f = row.split(",")
        if len(f) <= _IDX["fee"]:
            continue
        out.append({
            "fund_code": f[_IDX["code"]].strip(),
            "fund_name": f[_IDX["name"]].strip(),
            "inception_date": f[_IDX["inception"]].strip(),
            "return_1y": _to_float(f[_IDX["ret_1y"]]),
            "return_3y": _to_float(f[_IDX["ret_3y"]]),
            "return_since": _to_float(f[_IDX["ret_since"]]),
            "purchase_fee": _pct_to_float(f[_IDX["fee"]]),
        })
    return out


def _to_float(s):
    s = (s or "").strip()
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _pct_to_float(s):
    s = (s or "").strip().rstrip("%")
    try:
        return float(s) / 100.0
    except (TypeError, ValueError):
        return None


# ── 纯规则核心（离线可测）──────────────────────────────

def apply_filters(cands: list, sc: dict, today) -> list:
    """成立年限 + 业绩记录 + 费率 过滤。"""
    min_years = sc.get("min_inception_years", 2.0)
    require_3y = sc.get("require_3y_record", False)
    max_fee = sc.get("max_purchase_fee", 0.015)

    kept = []
    for c in cands:
        # 成立年限
        age = _age_years(c.get("inception_date"), today)
        if age is not None and age < min_years:
            continue
        # 若拿不到成立日期，用“有近1年业绩”兜底证明有最低记录
        if age is None and c.get("return_1y") is None:
            continue
        # 业绩记录
        if require_3y and c.get("return_3y") is None:
            continue
        # 费率（拿不到费率不一票否决，仅在已知且超限时剔除）
        fee = c.get("purchase_fee")
        if fee is not None and max_fee is not None and fee > max_fee:
            continue
        c = {**c, "age_years": round(age, 1) if age is not None else None}
        kept.append(c)
    return kept


def classify_and_dedup(cands: list, sc: dict) -> list:
    """分类 → 按基准去重保留最优 → 排序 → 池上限。"""
    rank_key = {"return_1y": "return_1y", "return_3y": "return_3y",
                "return_since": "return_since"}.get(sc.get("rank_by"), "return_since")
    keep_n = max(1, int(sc.get("per_benchmark_keep", 2)))
    cap = int(sc.get("max_pool_size", 30))

    # 分类
    enriched = []
    for c in cands:
        name = c["fund_name"]
        benchmark = infer_benchmark(name)
        enriched.append({
            **c,
            "fund_type": "QDII",
            "benchmark": benchmark,
            "region": infer_region(name),
            "asset_class": classify_asset_class(fund_code=c["fund_code"], fund_name=name, benchmark=benchmark),
            "expense_ratio": EXPENSE_RATIO_BY_CODE.get(c["fund_code"]),
        })

    # 排序键：指定收益降序；缺失值排最后；同分用费率低者优先
    def sort_key(c):
        r = c.get(rank_key)
        fee = c.get("purchase_fee")
        return (r if r is not None else -1e9, -(fee if fee is not None else 1e9))

    # ① 先按“基名”合并同一基金的不同份额类别(A/C、人民币/美元现汇)，每只基金只留最优份额
    by_fund = {}
    for c in enriched:
        by_fund.setdefault(_normalize_base(c["fund_name"]), []).append(c)
    one_per_fund = [sorted(items, key=sort_key, reverse=True)[0] for items in by_fund.values()]

    # ② 再按基准去重：标准指数每个基准保留最优 keep_n 只（不同提供商），其余每基金即一组
    groups = {}
    for c in one_per_fund:
        groups.setdefault(_dedup_key(c["benchmark"], c["fund_name"]), []).append(c)
    deduped = []
    for items in groups.values():
        items.sort(key=sort_key, reverse=True)
        deduped.extend(items[:keep_n])

    # ③ 全局按排序键排序，控制池上限
    deduped.sort(key=sort_key, reverse=True)
    return deduped[:cap]


# 可识别的标准指数基准（命中则按基准去重；否则按“去份额类别后的基金基名”去重）
_KNOWN_BENCHMARKS = {
    "标普500", "纳斯达克100", "标普科技", "标普油气", "日经225", "MSCI日本",
    "MSCI欧洲", "MSCI全球", "MSCI亚洲", "DAX", "恒生指数", "全球债券", "黄金",
    "印度市场", "越南市场",
}


def _normalize_base(name: str) -> str:
    """去掉份额类别/币种/结构后缀，得到基金“基名”，用于合并 A/C、人民币/美元现汇 等同一基金的不同份额。"""
    s = name or ""
    s = re.sub(r"[（(]\s*QDII\s*[）)]", "", s)
    s = re.sub(r"(人民币|美元现汇|美元现钞|美元|美钞|现汇|现钞|欧元|港币)", "", s)
    s = re.sub(r"(ETF联接|联接|ETF|LOF)", "", s)
    s = s.strip()
    # 反复剥离结尾份额字母（如 “…股票A”“…混合C”，含“A人民币”去币后再去A）
    for _ in range(2):
        s = re.sub(r"[ABCDE]$", "", s).strip()
    return s


def _dedup_key(benchmark: str, name: str) -> str:
    """标准指数→按基准去重；其余(多为主动)→按去份额后的基名去重，合并同一基金的多份额。"""
    if benchmark in _KNOWN_BENCHMARKS:
        return f"bench::{benchmark}"
    return f"base::{_normalize_base(name)}"


def _age_years(inception_date: str, today):
    if not inception_date:
        return None
    try:
        d = datetime.strptime(inception_date.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        return None
    return (today - d).days / 365.25


# ── 落库 ────────────────────────────────────────────────

def save_pool(pool: list):
    """把基金池写入 fund_list（upsert，不删除既有），并记 provenance + 导出CSV。"""
    if not pool:
        return
    from ..utils.database import get_connection
    from ..utils import provenance

    conn = get_connection()
    try:
        conn.executemany(
            """INSERT INTO fund_list (fund_code, fund_name, fund_type, expense_ratio, benchmark, updated_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(fund_code) DO UPDATE SET
                 fund_name=excluded.fund_name, fund_type=excluded.fund_type,
                 expense_ratio=excluded.expense_ratio, benchmark=excluded.benchmark,
                 updated_at=excluded.updated_at""",
            [(p["fund_code"], p["fund_name"], p.get("fund_type", "QDII"),
              p.get("expense_ratio"), p.get("benchmark", "")) for p in pool],
        )
        conn.commit()
    finally:
        conn.close()

    provenance.record("fund_pool", provenance.REAL, len(pool), "规则筛选(东财QDII排行)")
    _export_csv(pool)
    print(f"[DB] 规则筛选基金池已写入 fund_list：{len(pool)} 只")


def _export_csv(pool: list):
    import csv
    from pathlib import Path
    data_dir = Path(__file__).parent.parent.parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    cols = ["fund_code", "fund_name", "benchmark", "region", "asset_class",
            "expense_ratio", "purchase_fee", "age_years", "return_1y", "return_3y", "return_since"]
    try:
        with open(data_dir / "fund_pool.csv", "w", newline="", encoding="utf-8-sig") as fp:
            w = csv.DictWriter(fp, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(pool)
    except Exception:
        pass
