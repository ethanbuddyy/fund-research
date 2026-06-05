"""数据来源(provenance)记录与聚合。

每个采集器在运行后记录自己用的是真实数据还是模拟数据，
信号层据此向用户明确标注「本次建议基于真实/部分真实/模拟数据」，
避免随机模拟数据被当成真实行情输出投资建议。

本模块两层：
  1) 模式溯源（原有）：record / overall_mode / banner — 标注 real/partial/mock。
  2) 内容哈希缓存（扩展）：DataResult + cache_get/cache_put + cached_fetch —
     按「主键 + data_hash + config_hash」判定缓存有效性，配置变更或元数据缺失
     自动失效；原始 payload 内容寻址落 data/raw/（不可变快照，供复现/审计）。

刻意不依赖 pandas（仅 DataFrame 缓存的反序列化分支按需懒加载），直接用 sqlite3。
"""
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

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


# ══════════════════════════════════════════════════════════════════════════
# 内容哈希溯源与缓存失效（data_hash / config_hash）
#
# 文档纪律#4：缓存 key = 主键 + data_hash + config_hash，元数据缺失/配置变更自动失效。
# 痛点：旧缓存只按主键命中——参数（阈值/权重）改了仍命中旧结果，结果不可复现。
# 这里把「依赖的配置版本」编码进 config_hash：配置一变，哈希变，旧缓存立即失效。
# 同时 data_hash 既是缓存内容指纹，又作为不可变原始快照(data/raw/)的内容寻址文件名，
# 为「保留 API 原始返回、可复盘当时输入」(#3) 打地基。
# ══════════════════════════════════════════════════════════════════════════

_HASH_LEN = 16  # sha256 取前 16 hex（64 bit）；本系统规模下碰撞概率可忽略


def _canonical_bytes(payload: Any) -> bytes:
    """把任意 payload 规范化为确定性字节串，供哈希。

    - DataFrame 等（鸭子类型有 to_csv）：用无索引 CSV，避免在本模块引入 pandas 依赖。
    - dict/list/标量：排序键的紧凑 JSON（与键顺序无关）；非 JSON 类型用 str() 兜底。
    """
    if hasattr(payload, "to_csv"):
        try:
            return payload.to_csv(index=False).encode("utf-8")
        except Exception:
            pass
    try:
        return json.dumps(payload, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), default=str).encode("utf-8")
    except Exception:
        return repr(payload).encode("utf-8")


def compute_data_hash(payload: Any) -> str:
    """payload 的内容指纹。相同内容→相同哈希（与 dict 键顺序无关）。"""
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()[:_HASH_LEN]


def compute_config_hash(config_subset: Any) -> str:
    """影响本次取数/计算的配置子集的指纹。配置变→哈希变→缓存失效。

    只把真正影响结果的配置切片传进来（如某采集器相关的 series 列表、阈值），
    不要传整个 settings——否则任何无关配置改动都会无谓地冲掉缓存。
    """
    return hashlib.sha256(_canonical_bytes(config_subset)).hexdigest()[:_HASH_LEN]


@dataclass
class DataResult:
    """带溯源元数据的取数结果。

    把「数据本身」与「它从哪来、是否真实、内容指纹、依赖哪版配置」绑在一起，
    使缓存可按 (主键 + data_hash + config_hash) 判定有效性，使原始返回可留痕复现。
    """
    source: str                  # 数据源/逻辑域，如 macro/market/fund_fee
    payload: Any                 # 实际数据（JSON 可序列化 或 DataFrame）
    mode: str = REAL             # real/partial/mock，沿用 provenance 模式语义
    source_id: str = ""          # 主键内的细分标识，如基金代码 / series id
    config_hash: str = ""        # 依赖的配置子集指纹（空 = 不依赖配置）
    data_hash: str = ""          # 内容指纹，留空则按 payload 自动计算
    rows: int = 0
    detail: str = ""
    fetched_at: str = ""         # 落库时由 cache_put 填入
    from_cache: bool = False     # True = 本结果来自缓存命中

    def __post_init__(self):
        if not self.data_hash:
            self.data_hash = compute_data_hash(self.payload)

    @property
    def cache_key(self) -> str:
        return f"{self.source}:{self.source_id}" if self.source_id else self.source

    def record(self) -> None:
        """兼容旧接口：把本结果的模式写入 collection_meta。"""
        record(self.source, self.mode, self.rows, self.detail)


def _snapshot_root() -> Path:
    """原始快照根目录：与 DB 同级的 data/raw/（data/ 已 gitignore，只留本地）。"""
    from .config import get_db_path
    return Path(get_db_path()).parent / "raw"


def _snapshot_path(source: str, data_hash: str) -> Path:
    # 内容寻址：文件名=内容哈希 → 同内容必同名 → 天然不可变（#3 地基）。
    safe = "".join(c for c in source if c.isalnum() or c in ("_", "-")) or "misc"
    return _snapshot_root() / safe / f"{data_hash}.json"


def _serialize(payload: Any) -> tuple[str, str]:
    """(text, kind)。DataFrame 用 orient=split 无损往返；其余走 JSON。"""
    if hasattr(payload, "to_json"):
        return payload.to_json(orient="split", force_ascii=False), "dataframe"
    return json.dumps(payload, ensure_ascii=False, default=str), "json"


def _deserialize(text: str, kind: str) -> Any:
    if kind == "dataframe":
        import pandas as pd  # 仅此分支需要，懒加载
        from io import StringIO
        return pd.read_json(StringIO(text), orient="split")
    return json.loads(text)


def cache_put(result: DataResult) -> DataResult:
    """写不可变快照(data/raw) + 缓存行(data_cache) + 模式(collection_meta)。

    返回填好 fetched_at、from_cache=False 的同一结果。任一步失败都不抛出，
    最坏退化为「下次未命中重新取数」，绝不阻断主流程。
    """
    text, kind = _serialize(result.payload)

    # ① 不可变原始快照（内容寻址；已存在即同内容，跳过）
    try:
        path = _snapshot_path(result.source, result.data_hash)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.rename(path)  # 原子落地，避免半截快照
    except Exception:
        pass

    # ② 缓存索引行
    try:
        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO data_cache
                     (cache_key, source, source_id, data_hash, config_hash,
                      payload_kind, mode, rows, detail, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                   ON CONFLICT(cache_key) DO UPDATE SET
                     source=excluded.source, source_id=excluded.source_id,
                     data_hash=excluded.data_hash, config_hash=excluded.config_hash,
                     payload_kind=excluded.payload_kind, mode=excluded.mode,
                     rows=excluded.rows, detail=excluded.detail,
                     fetched_at=excluded.fetched_at""",
                (result.cache_key, result.source, result.source_id,
                 result.data_hash, result.config_hash, kind,
                 result.mode, int(result.rows), result.detail),
            )
            conn.commit()
            row = conn.execute(
                "SELECT fetched_at FROM data_cache WHERE cache_key = ?",
                (result.cache_key,)).fetchone()
            if row:
                result.fetched_at = row["fetched_at"]
        finally:
            conn.close()
    except Exception:
        pass

    result.record()        # 兼容旧 provenance 模式溯源
    result.from_cache = False
    return result


def cache_get(source: str, source_id: str = "", *, config_hash: str = "",
              max_age_days: Optional[float] = None) -> Optional[DataResult]:
    """按 (主键 + config_hash) 取缓存。任一不满足即返回 None（失效）：

      ① 无该缓存行；② data_hash 元数据缺失；③ config_hash 不匹配（配置已变）；
      ④ 超过 max_age_days；⑤ 快照文件丢失或损坏。
    """
    cache_key = f"{source}:{source_id}" if source_id else source
    try:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM data_cache WHERE cache_key = ?", (cache_key,)).fetchone()
        finally:
            conn.close()
    except Exception:
        return None

    if row is None:
        return None
    stored_data_hash = row["data_hash"]
    if not stored_data_hash:                       # ② 元数据缺失自动失效
        return None
    if (row["config_hash"] or "") != (config_hash or ""):  # ③ 配置变更失效
        return None
    if max_age_days is not None and row["fetched_at"]:     # ④ 时效
        import datetime
        try:
            last = datetime.datetime.fromisoformat(row["fetched_at"].replace("Z", ""))
            if (datetime.datetime.utcnow() - last).total_seconds() > max_age_days * 86400:
                return None
        except Exception:
            pass

    path = _snapshot_path(source, stored_data_hash)        # ⑤ 读回快照
    if not path.exists():
        return None
    try:
        payload = _deserialize(path.read_text(encoding="utf-8"),
                               row["payload_kind"] or "json")
    except Exception:
        return None

    return DataResult(
        source=source, payload=payload, mode=row["mode"] or REAL,
        source_id=source_id, config_hash=row["config_hash"] or "",
        data_hash=stored_data_hash, rows=row["rows"] or 0,
        detail=row["detail"] or "", fetched_at=row["fetched_at"] or "",
        from_cache=True,
    )


def cached_fetch(source: str, fetch_fn: Callable[[], Any], *, source_id: str = "",
                 config_subset: Any = None, mode: str = REAL,
                 max_age_days: Optional[float] = None,
                 detail: str = "") -> DataResult:
    """取数装配器：命中缓存即返回（跳过 fetch_fn）；未命中则取数→落快照+缓存。

    config_subset: 影响本次取数的配置切片；它一变 config_hash 变，旧缓存自动失效。
    fetch_fn 返回 None 视为取数失败，不写缓存（避免把失败结果固化）。
    """
    config_hash = compute_config_hash(config_subset) if config_subset is not None else ""
    hit = cache_get(source, source_id, config_hash=config_hash, max_age_days=max_age_days)
    if hit is not None:
        return hit

    payload = fetch_fn()
    if payload is None:
        return DataResult(source=source, payload=None, mode=mode,
                          source_id=source_id, config_hash=config_hash, detail=detail)
    result = DataResult(source=source, payload=payload, mode=mode,
                        source_id=source_id, config_hash=config_hash, detail=detail)
    return cache_put(result)
