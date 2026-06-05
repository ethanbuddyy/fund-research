"""Okapi BM25 词法检索（纯 numpy/collections，零新依赖）。

`Retriever` 协议把检索行为抽象成 `search(query, k) -> list[Hit]`；
本文件提供 `BM25Index` 词法实现。日后 embedding 后端只需实现同一 `.search()`，
`recall.py` 据 `retrieval.backend` 配置热插拔，store/recall/ingest/CLI/注入零改动。
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Protocol

from .tokenize import tokenize


@dataclass
class Hit:
    """一条检索命中。"""

    doc_type: str
    source_id: str
    title: str
    snippet: str
    score: float
    meta: dict = field(default_factory=dict)


class Retriever(Protocol):
    """检索后端协议——词法/embedding 后端均实现此接口。"""

    def search(self, query: str, k: int) -> list[Hit]: ...


class BM25Index:
    """Okapi BM25。

    docs: list[dict]，每条至少含 text；doc_type/source_id/title/meta 透传到 Hit。
    参数 k1/b 取业界常用默认（1.5 / 0.75）。
    """

    def __init__(self, docs: list[dict], k1: float = 1.5, b: float = 0.75):
        self.docs = docs
        self.k1 = k1
        self.b = b
        self._tokenized: list[list[str]] = [tokenize(d.get("text", "")) for d in docs]
        self._doc_len = [len(t) for t in self._tokenized]
        self._avgdl = (sum(self._doc_len) / len(self._doc_len)) if self._doc_len else 0.0
        self._tf: list[Counter] = [Counter(t) for t in self._tokenized]
        # 文档频率：含某 token 的文档数
        df: Counter = Counter()
        for tf in self._tf:
            df.update(tf.keys())
        n = len(docs)
        # BM25 idf（加 0.5 平滑，下限 0 防负）
        self._idf = {
            term: max(0.0, math.log((n - freq + 0.5) / (freq + 0.5) + 1.0))
            for term, freq in df.items()
        }

    def _score(self, q_tokens: list[str], i: int) -> float:
        if self._avgdl == 0:
            return 0.0
        tf = self._tf[i]
        dl = self._doc_len[i]
        score = 0.0
        for term in q_tokens:
            f = tf.get(term, 0)
            if f == 0:
                continue
            idf = self._idf.get(term, 0.0)
            denom = f + self.k1 * (1 - self.b + self.b * dl / self._avgdl)
            score += idf * (f * (self.k1 + 1)) / denom
        return score

    def search(self, query: str, k: int = 5, snippet_chars: int = 240) -> list[Hit]:
        q_tokens = tokenize(query)
        if not q_tokens or not self.docs:
            return []
        scored = [(self._score(q_tokens, i), i) for i in range(len(self.docs))]
        scored = [(s, i) for s, i in scored if s > 0]
        scored.sort(key=lambda x: x[0], reverse=True)
        hits: list[Hit] = []
        for s, i in scored[:k]:
            d = self.docs[i]
            text = d.get("text", "") or ""
            hits.append(
                Hit(
                    doc_type=d.get("doc_type", ""),
                    source_id=d.get("source_id", "") or "",
                    title=d.get("title", "") or "",
                    snippet=text[:snippet_chars].strip(),
                    score=round(float(s), 4),
                    meta=d.get("meta") or {},
                )
            )
        return hits
