"""内容哈希缓存失效（provenance 扩展）单元测试。

覆盖文档纪律#4 的核心语义：缓存 = 主键 + data_hash + config_hash，
配置变更 / 元数据缺失 / 超时 / 快照丢失 任一即失效；以及不可变原始快照。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from src.utils import provenance, database


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """把 DB 与快照根都指到临时目录，隔离真实 data/。"""
    db = tmp_path / "test.db"
    # database.get_connection 与 provenance._snapshot_root 各自取 get_db_path，
    # 两个绑定都要打补丁。
    monkeypatch.setattr("src.utils.database.get_db_path", lambda: str(db))
    monkeypatch.setattr("src.utils.config.get_db_path", lambda: str(db))
    database.init_database()
    return tmp_path


# ─────────────────────────────────────────────────────────────
# 哈希
# ─────────────────────────────────────────────────────────────
class TestHashing:
    def test_data_hash_independent_of_key_order(self):
        assert provenance.compute_data_hash({"a": 1, "b": 2}) == \
               provenance.compute_data_hash({"b": 2, "a": 1})

    def test_data_hash_changes_with_content(self):
        assert provenance.compute_data_hash({"a": 1}) != \
               provenance.compute_data_hash({"a": 2})

    def test_config_hash_changes_with_config(self):
        assert provenance.compute_config_hash({"w": 0.27}) != \
               provenance.compute_config_hash({"w": 0.30})

    def test_hash_is_short_hex(self):
        h = provenance.compute_data_hash([1, 2, 3])
        assert len(h) == 16 and all(c in "0123456789abcdef" for c in h)


# ─────────────────────────────────────────────────────────────
# put / get 往返与失效
# ─────────────────────────────────────────────────────────────
class TestCacheRoundtrip:
    def test_put_then_get_roundtrip(self, tmp_db):
        payload = {"cape": 32.1, "pe": 25.0}
        provenance.cache_put(provenance.DataResult(
            source="valuation", payload=payload, source_id="us"))
        got = provenance.cache_get("valuation", "us")
        assert got is not None
        assert got.payload == payload
        assert got.from_cache is True

    def test_miss_returns_none(self, tmp_db):
        assert provenance.cache_get("nope", "x") is None

    def test_snapshot_file_is_content_addressed(self, tmp_db):
        res = provenance.cache_put(provenance.DataResult(
            source="market", payload=[1, 2, 3], source_id="spx"))
        snap = tmp_db / "raw" / "market" / f"{res.data_hash}.json"
        assert snap.exists()

    def test_config_change_invalidates(self, tmp_db):
        h_old = provenance.compute_config_hash({"threshold": 15})
        provenance.cache_put(provenance.DataResult(
            source="macro", payload={"x": 1}, source_id="cycle", config_hash=h_old))
        # 同配置 → 命中
        assert provenance.cache_get("macro", "cycle", config_hash=h_old) is not None
        # 配置变了 → 失效
        h_new = provenance.compute_config_hash({"threshold": 20})
        assert provenance.cache_get("macro", "cycle", config_hash=h_new) is None

    def test_missing_metadata_invalidates(self, tmp_db):
        # 手工塞一行 data_hash 为空（模拟旧缓存/元数据缺失）
        conn = database.get_connection()
        conn.execute(
            "INSERT INTO data_cache (cache_key, source, data_hash, fetched_at) "
            "VALUES (?, ?, ?, datetime('now'))", ("legacy:x", "legacy", ""))
        conn.commit()
        conn.close()
        assert provenance.cache_get("legacy", "x") is None

    def test_max_age_invalidates(self, tmp_db):
        provenance.cache_put(provenance.DataResult(
            source="news", payload={"s": 0.5}, source_id="spx"))
        # 把 fetched_at 改到很久以前
        conn = database.get_connection()
        conn.execute("UPDATE data_cache SET fetched_at = '2000-01-01 00:00:00' "
                     "WHERE cache_key = 'news:spx'")
        conn.commit()
        conn.close()
        assert provenance.cache_get("news", "spx", max_age_days=1) is None
        # 不设时效 → 仍命中
        assert provenance.cache_get("news", "spx") is not None

    def test_lost_snapshot_invalidates(self, tmp_db):
        res = provenance.cache_put(provenance.DataResult(
            source="macro", payload={"x": 1}, source_id="cycle"))
        (tmp_db / "raw" / "macro" / f"{res.data_hash}.json").unlink()
        assert provenance.cache_get("macro", "cycle") is None


# ─────────────────────────────────────────────────────────────
# cached_fetch 装配器
# ─────────────────────────────────────────────────────────────
class TestCachedFetch:
    def test_second_call_skips_fetch(self, tmp_db):
        calls = {"n": 0}

        def fetch():
            calls["n"] += 1
            return {"v": 42}

        r1 = provenance.cached_fetch("fee", fetch, source_id="A")
        r2 = provenance.cached_fetch("fee", fetch, source_id="A")
        assert calls["n"] == 1            # 第二次走缓存，未再调 fetch
        assert r1.from_cache is False
        assert r2.from_cache is True
        assert r2.payload == {"v": 42}

    def test_config_change_triggers_refetch(self, tmp_db):
        calls = {"n": 0}

        def fetch():
            calls["n"] += 1
            return {"v": calls["n"]}

        provenance.cached_fetch("sig", fetch, source_id="A", config_subset={"w": 1})
        provenance.cached_fetch("sig", fetch, source_id="A", config_subset={"w": 2})
        assert calls["n"] == 2            # 配置变 → 重新取数

    def test_failed_fetch_not_cached(self, tmp_db):
        provenance.cached_fetch("fee", lambda: None, source_id="bad")
        assert provenance.cache_get("fee", "bad") is None


# ─────────────────────────────────────────────────────────────
# DataFrame 往返
# ─────────────────────────────────────────────────────────────
class TestDataFrameRoundtrip:
    def test_dataframe_cache_roundtrip(self, tmp_db):
        import pandas as pd
        df = pd.DataFrame({"code": ["A", "B"], "score": [80.0, 90.0]})
        provenance.cache_put(provenance.DataResult(
            source="fund_scores", payload=df, source_id="pool"))
        got = provenance.cache_get("fund_scores", "pool")
        assert got is not None
        pd.testing.assert_frame_equal(
            got.payload.reset_index(drop=True), df, check_dtype=False)
