"""第6因子（全球宏观）失败路径回归（issue #2）。

过去 signals._global_macro_factor 的前身把任何异常静默回落中性 5.0，使一个本应
参与综合分的因子悄悄失效却无告警。此处钉死：意外异常必须 ① 回落中性 5.0 不崩，
② 记入 provenance(partial) 让 banner / 数据可信度板块显形（不再静默劣化）。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from src.utils import provenance, database
from src.recommender import signals


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setattr("src.utils.database.get_db_path", lambda: str(db))
    monkeypatch.setattr("src.utils.config.get_db_path", lambda: str(db))
    database.init_database()
    return tmp_path


def test_factor_exception_recorded_not_silently_neutral(tmp_db, monkeypatch):
    # 让分析器抛意外异常（模拟 schema/逻辑 bug）
    def _boom():
        raise RuntimeError("模拟 schema 漂移")
    monkeypatch.setattr(
        "src.analyzers.global_macro_analyzer.analyze_global_macro", _boom
    )

    gm, score = signals._global_macro_factor()

    # ① 回落中性，不崩
    assert score == 5.0
    assert gm == {"available": False, "regions": {}}

    # ② 故障显形：provenance 记了 partial，且 overall_mode 不再是纯 real
    meta = provenance.read_all()
    assert "global_macro_score" in meta
    assert meta["global_macro_score"]["mode"] == "partial"
    assert "模拟 schema 漂移" in (meta["global_macro_score"]["detail"] or "")


def test_no_data_returns_neutral_without_partial_mark(tmp_db):
    # 全新库无 global_macro 数据 → 分析器返回 available=False（不抛异常）；
    # 这是「无数据」而非「计算故障」，不应被标 partial（避免假阳性告警）。
    gm, score = signals._global_macro_factor()
    assert score == 5.0
    assert gm.get("available") is False
    assert "global_macro_score" not in provenance.read_all()
