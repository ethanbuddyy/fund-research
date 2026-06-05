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


def is_enabled() -> bool:
    """检索层总开关（settings.yaml: retrieval.enabled）——**单一真相源**。

    关闭则整层静默：不写语料(ingest/新闻截留)、不检索(recall)、不注入(RAG)。
    所有入口（store 写入除外，store 是被动原语）都应据此短路。
    """
    return bool(_retrieval_cfg().get("enabled", True))


def is_injection_enabled() -> bool:
    """RAG 注入子开关：须 总开关开 且 inject_into_ai 开，缺一不注入。"""
    cfg = _retrieval_cfg()
    return bool(cfg.get("enabled", True)) and bool(cfg.get("inject_into_ai", True))


def status() -> dict:
    """检索层运行状态——供报告「数据可信度」板块呈现，提醒用户该层开关与语料量。

    返回 {enabled, injection, backend, doc_count}；关闭时 doc_count 不查库（置 0）。
    """
    cfg = _retrieval_cfg()
    enabled = bool(cfg.get("enabled", True))
    doc_count = 0
    if enabled:
        try:
            from .store import count_documents
            doc_count = count_documents()
        except Exception:
            doc_count = 0
    return {
        "enabled": enabled,
        "injection": enabled and bool(cfg.get("inject_into_ai", True)),
        "backend": cfg.get("backend", "lexical"),
        "doc_count": doc_count,
    }


def status_line() -> str:
    """单行中文状态串（MD/HTML/CLI 通用文本，不含标记）。"""
    st = status()
    if not st["enabled"]:
        return "检索增强层：关闭（settings.yaml: retrieval.enabled=false）"
    inject = "开" if st["injection"] else "关"
    return (
        f"检索增强层：开启（RAG 注入 AI：{inject} · 语料 {st['doc_count']} 篇 · "
        f"后端 {st['backend']}）"
    )


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
    if not is_injection_enabled():
        return ""
    cfg = _retrieval_cfg()
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
