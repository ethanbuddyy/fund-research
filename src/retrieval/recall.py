"""高层检索入口。CLI（--recall）与 RAG 注入共用。

从 store 载入语料 → 建 BM25 → 检索。读 `cfg["retrieval"]`（top_k/doc_types/enabled/backend）。
backend 目前仅 lexical；embedding 后端日后在此分流（实现同 Retriever 协议）。
"""

from __future__ import annotations

from typing import Optional

from ..utils.config import load_config
from .bm25 import BM25Index, Hit
from .store import iter_documents


def _retrieval_cfg() -> dict:
    try:
        return load_config().get("retrieval", {}) or {}
    except Exception:
        return {}


def recall(
    query: str,
    k: Optional[int] = None,
    doc_types: Optional[list[str]] = None,
) -> list[Hit]:
    """检索相关文档。enabled=false 或语料为空 → 返回空列表（调用方据此降级）。"""
    cfg = _retrieval_cfg()
    if not cfg.get("enabled", True):
        return []
    if not query or not query.strip():
        return []

    k = k if k is not None else int(cfg.get("top_k", 5))
    types = doc_types if doc_types is not None else cfg.get("doc_types")

    docs = iter_documents(types)
    if not docs:
        return []
    index = BM25Index(docs)
    return index.search(query, k=k)


def evidence_block(
    query: str,
    doc_types: Optional[list[str]] = None,
    header: str = "=== 检索到的相关证据 ===",
) -> str:
    """供 RAG 注入：把检索命中格式化成带来源标注的证据块。

    `retrieval.enabled=false` 或 `inject_into_ai=false` 或无命中 → 返回 ""，
    调用方据此保证「关闭注入时 prompt 与现状逐字一致」。截到 max_evidence_chars。
    """
    cfg = _retrieval_cfg()
    if not cfg.get("enabled", True) or not cfg.get("inject_into_ai", True):
        return ""
    try:
        hits = recall(query, doc_types=doc_types)
    except Exception:
        return ""
    if not hits:
        return ""

    max_chars = int(cfg.get("max_evidence_chars", 1200))
    lines = [header]
    used = 0
    for h in hits:
        date = h.meta.get("date", "") if isinstance(h.meta, dict) else ""
        src = f"[{h.doc_type}{(' ' + date) if date else ''}]"
        snippet = (h.snippet or "").replace("\n", " ").strip()
        entry = f"- {src} {h.title}：{snippet}" if h.title else f"- {src} {snippet}"
        if used + len(entry) > max_chars:
            remaining = max_chars - used
            if remaining > 40:
                lines.append(entry[:remaining] + "…")
            break
        lines.append(entry)
        used += len(entry)

    return "\n".join(lines) if len(lines) > 1 else ""
