"""检索/语义检索层（src/retrieval/）单元测试。

覆盖：中英混合分词、BM25 相关性排序、store 内容去重幂等、recall 端到端、
以及 RAG 注入开关 on/off 的行为差异（关闭时 prompt 逐字不变）。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import importlib

from src.utils import database
from src.retrieval.tokenize import tokenize
from src.retrieval.bm25 import BM25Index

# 注意：src.retrieval.__init__ 把 recall 函数导出为包属性，遮蔽了同名子模块；
# 用 importlib 取回真正的模块对象，才能 monkeypatch 其 load_config。
store_mod = importlib.import_module("src.retrieval.store")
recall_mod = importlib.import_module("src.retrieval.recall")


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """DB 指向临时目录，隔离真实 data/（仿 test_provenance_cache）。"""
    db = tmp_path / "test.db"
    monkeypatch.setattr("src.utils.database.get_db_path", lambda: str(db))
    monkeypatch.setattr("src.utils.config.get_db_path", lambda: str(db))
    database.init_database()
    return tmp_path


# ── 分词 ──────────────────────────────────────────────────────
class TestTokenize:
    def test_latin_lowercased(self):
        assert tokenize("SP500 Up 5%") == ["sp500", "up", "5"]

    def test_cjk_bigram(self):
        # "美联储降息" → 相邻字符二元组
        assert tokenize("美联储降息") == ["美联", "联储", "储降", "降息"]

    def test_mixed(self):
        toks = tokenize("美联储降息 SP500")
        assert "降息" in toks and "sp500" in toks

    def test_single_cjk_char(self):
        assert tokenize("涨") == ["涨"]

    def test_empty(self):
        assert tokenize("") == []


# ── BM25 ──────────────────────────────────────────────────────
class TestBM25:
    def test_relevant_doc_ranks_first(self):
        docs = [
            {"doc_type": "news", "title": "A", "text": "美联储宣布降息，市场情绪明显回暖"},
            {"doc_type": "news", "title": "B", "text": "某科技公司发布季度财报超预期"},
        ]
        idx = BM25Index(docs)
        hits = idx.search("美联储降息", k=2)
        assert hits, "应有命中"
        assert hits[0].title == "A"

    def test_no_match_returns_empty(self):
        idx = BM25Index([{"doc_type": "news", "title": "A", "text": "财报超预期"}])
        assert idx.search("完全无关的查询词xyz", k=5) == []

    def test_empty_index(self):
        assert BM25Index([]).search("任意", k=5) == []


# ── store 去重 ────────────────────────────────────────────────
class TestStore:
    def test_dedup_idempotent(self, tmp_db):
        text = "美联储降息预期升温，权益资产吸引力上升"
        id1 = store_mod.upsert_document("narrative", "2026-06-05", "叙事", text)
        id2 = store_mod.upsert_document("narrative", "2026-06-05", "叙事", text)
        assert id1 == id2
        assert len(store_mod.iter_documents()) == 1

    def test_empty_text_skipped(self, tmp_db):
        assert store_mod.upsert_document("narrative", "x", "t", "   ") is None
        assert len(store_mod.iter_documents()) == 0

    def test_doc_type_filter(self, tmp_db):
        store_mod.upsert_document("news", "d", "n", "降息消息")
        store_mod.upsert_document("report", "f", "r", "报告正文内容")
        assert len(store_mod.iter_documents(["news"])) == 1
        assert len(store_mod.iter_documents()) == 2


# ── recall 端到端 ─────────────────────────────────────────────
class TestRecall:
    def test_end_to_end(self, tmp_db, monkeypatch):
        # 用稳定的 retrieval 配置，避免依赖真实 settings.yaml
        monkeypatch.setattr(
            recall_mod, "load_config",
            lambda: {"retrieval": {"enabled": True, "top_k": 5}},
        )
        store_mod.upsert_document("narrative", "2026-06-05", "市场叙事", "美联储降息预期升温")
        store_mod.upsert_document("news", "2026-06-05", "财报", "某公司财报超预期")
        hits = recall_mod.recall("降息")
        assert hits
        assert "narrative" == hits[0].doc_type

    def test_disabled_returns_empty(self, tmp_db, monkeypatch):
        monkeypatch.setattr(
            recall_mod, "load_config",
            lambda: {"retrieval": {"enabled": False}},
        )
        store_mod.upsert_document("narrative", "d", "t", "美联储降息")
        assert recall_mod.recall("降息") == []


# ── RAG 注入开关 ──────────────────────────────────────────────
class TestInjectionGate:
    def _signal(self):
        return {
            "macro": {"cycle": "扩张"}, "valuation": {"valuation_level": "高估"},
            "sentiment": {"label": "中性"}, "global_macro": {},
            "composite_signal": "标配稳健",
        }

    def test_evidence_block_off_is_empty(self, tmp_db, monkeypatch):
        monkeypatch.setattr(
            recall_mod, "load_config",
            lambda: {"retrieval": {"enabled": True, "inject_into_ai": False}},
        )
        store_mod.upsert_document("narrative", "d", "t", "美联储降息预期升温")
        assert recall_mod.evidence_block("降息") == ""

    def test_evidence_block_on_has_hits(self, tmp_db, monkeypatch):
        monkeypatch.setattr(
            recall_mod, "load_config",
            lambda: {"retrieval": {"enabled": True, "inject_into_ai": True,
                                   "top_k": 5, "max_evidence_chars": 1200}},
        )
        store_mod.upsert_document("narrative", "2026-06-05", "市场叙事", "美联储降息预期升温")
        block = recall_mod.evidence_block("降息")
        assert "检索到的相关证据" in block
        assert "降息" in block

    def test_phase1_format_identical_when_off(self, tmp_db, monkeypatch):
        """inject_into_ai=false 时 _format_signal_data 输出与无注入逐字一致。"""
        from src.ai import phase1_market_analyzer as p1
        store_mod.upsert_document("narrative", "d", "t", "美联储降息预期升温，估值高企")

        monkeypatch.setattr(
            recall_mod, "load_config",
            lambda: {"retrieval": {"enabled": True, "inject_into_ai": False}},
        )
        out_off = p1._format_signal_data(self._signal())

        # 基线：检索层完全禁用（亦应逐字一致）
        monkeypatch.setattr(
            recall_mod, "load_config",
            lambda: {"retrieval": {"enabled": False}},
        )
        out_disabled = p1._format_signal_data(self._signal())

        assert out_off == out_disabled
        assert "检索到的相关证据" not in out_off

    def test_phase1_format_differs_when_on(self, tmp_db, monkeypatch):
        from src.ai import phase1_market_analyzer as p1
        store_mod.upsert_document("narrative", "2026-06-05", "市场叙事", "扩张周期下美联储降息预期升温")

        monkeypatch.setattr(
            recall_mod, "load_config",
            lambda: {"retrieval": {"enabled": True, "inject_into_ai": True,
                                   "top_k": 5, "max_evidence_chars": 1200,
                                   "doc_types": ["news", "narrative", "region", "report"]}},
        )
        out_on = p1._format_signal_data(self._signal())
        assert "检索到的相关证据" in out_on


# ── 总开关：单一真相源 + 漏点封堵 ─────────────────────────────
class TestMasterSwitch:
    def test_news_persist_respects_switch(self, tmp_db, monkeypatch):
        """retrieval.enabled=false 时，新闻原文截留必须被总开关挡住（曾绕过总闸）。"""
        import importlib
        nc = importlib.import_module("src.collectors.news_collector")
        items = [{"title": "美联储降息", "summary": "市场情绪回暖", "url": "u", "source": "av"}]

        monkeypatch.setattr(recall_mod, "load_config",
                            lambda: {"retrieval": {"enabled": False}})
        nc._persist_news_corpus(items, "2026-06-05")
        assert store_mod.count_documents() == 0  # 总开关关 → 一条都不写

        monkeypatch.setattr(recall_mod, "load_config",
                            lambda: {"retrieval": {"enabled": True}})
        nc._persist_news_corpus(items, "2026-06-05")
        assert store_mod.count_documents() == 1  # 开 → 正常截留

    def test_status_line_reflects_state(self, tmp_db, monkeypatch):
        store_mod.upsert_document("narrative", "d", "t", "美联储降息预期升温")

        monkeypatch.setattr(recall_mod, "load_config",
                            lambda: {"retrieval": {"enabled": False}})
        assert "关闭" in recall_mod.status_line()
        assert recall_mod.status()["enabled"] is False

        monkeypatch.setattr(
            recall_mod, "load_config",
            lambda: {"retrieval": {"enabled": True, "inject_into_ai": True, "backend": "lexical"}},
        )
        line = recall_mod.status_line()
        assert "开启" in line and "语料 1 篇" in line and "RAG 注入 AI：开" in line
