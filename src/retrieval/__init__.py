"""检索/语义检索层（#1）。

BM25 词法检索起步，零新依赖；`Retriever` 协议封装，日后可平滑换 embedding 后端
（仅加 embedding_index.py 实现同一 .search()，其余零改动）。

两件事：先把「用完即弃」文本沉淀为语料（ingest），再在其上建检索（recall）。
双用途：独立语义搜索 CLI（run.py --recall）+ 注入 AI 三阶段 prompt 作证据（RAG）。
"""

from .bm25 import BM25Index, Hit, Retriever
from .ingest import ingest_fund_analysis, ingest_reports_dir, ingest_run
from .recall import recall
from .store import iter_documents, upsert_document
from .tokenize import tokenize

__all__ = [
    "BM25Index",
    "Hit",
    "Retriever",
    "recall",
    "upsert_document",
    "iter_documents",
    "tokenize",
    "ingest_run",
    "ingest_fund_analysis",
    "ingest_reports_dir",
]
