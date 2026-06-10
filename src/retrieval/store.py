"""`documents` 语料表读写。

内容寻址去重：`doc_id = f"{doc_type}:{compute_data_hash(text)}"`，
同 doc_type + 同正文 → 同 doc_id → `INSERT OR IGNORE` 幂等不重复入库。
复用 provenance.compute_data_hash（与缓存层同一内容指纹口径）。
"""

from __future__ import annotations

import json
from typing import Iterable, Optional

from ..utils.database import get_connection
from ..utils.provenance import compute_data_hash


def upsert_document(
    doc_type: str,
    source_id: str,
    title: str,
    text: str,
    meta: Optional[dict] = None,
    mode: str = "real",
) -> Optional[str]:
    """入库一条文档，按内容去重。返回 doc_id；text 为空则跳过返回 None。"""
    if not text or not text.strip():
        return None
    data_hash = compute_data_hash(text)
    doc_id = f"{doc_type}:{data_hash}"
    conn = get_connection()
    try:
        conn.execute(
            """INSERT OR IGNORE INTO documents
               (doc_id, doc_type, source_id, title, text, meta, data_hash, mode)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                doc_id,
                doc_type,
                source_id or "",
                title or "",
                text,
                json.dumps(meta or {}, ensure_ascii=False),
                data_hash,
                mode,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return doc_id


def count_documents() -> int:
    """语料总条数（轻量 COUNT，供报告状态行用，不拉全表）。"""
    conn = get_connection()
    try:
        row = conn.execute("SELECT COUNT(*) FROM documents").fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0
    finally:
        conn.close()


def document_version(doc_types: Optional[Iterable[str]] = None) -> tuple[str, int, int]:
    """返回 `(数据库路径, 行数, 最大 rowid)`，供内存索引判断是否失效。"""
    conn = get_connection()
    try:
        db_row = conn.execute("PRAGMA database_list").fetchone()
        db_path = str(db_row[2]) if db_row else ""
        if doc_types:
            types = list(doc_types)
            placeholders = ",".join("?" for _ in types)
            row = conn.execute(
                f"SELECT COUNT(*), COALESCE(MAX(rowid), 0) FROM documents "
                f"WHERE doc_type IN ({placeholders})",
                types,
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*), COALESCE(MAX(rowid), 0) FROM documents"
            ).fetchone()
        count, max_rowid = (int(row[0]), int(row[1])) if row else (0, 0)
        return db_path, count, max_rowid
    finally:
        conn.close()


def iter_documents(doc_types: Optional[Iterable[str]] = None) -> list[dict]:
    """读取语料。doc_types=None 取全部；否则按类型过滤。meta 解析回 dict。"""
    conn = get_connection()
    try:
        if doc_types:
            types = list(doc_types)
            placeholders = ",".join("?" * len(types))
            rows = conn.execute(
                f"SELECT doc_id, doc_type, source_id, title, text, meta, mode, created_at "
                f"FROM documents WHERE doc_type IN ({placeholders})",
                types,
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT doc_id, doc_type, source_id, title, text, meta, mode, created_at "
                "FROM documents"
            ).fetchall()
    finally:
        conn.close()

    out: list[dict] = []
    for r in rows:
        d = dict(r)
        try:
            d["meta"] = json.loads(d.get("meta") or "{}")
        except (ValueError, TypeError):
            d["meta"] = {}
        out.append(d)
    return out
