"""数据库基线 schema 与增量迁移必须在全新库和旧库上得到同一结构。"""

import pytest

from src.utils import database


def test_unknown_table_rejected():
    with pytest.raises(ValueError, match="白名单"):
        database._check_table("evil_table")


def test_where_clause_blocks_injection():
    # where 只接受代码内常量；语句堆叠/注释应被拦下（与表名白名单对称的护栏）
    for bad in ("1=1; DROP TABLE fund_list", "x = 1 --", "a /* c */ = 1"):
        with pytest.raises(ValueError, match="非法 where"):
            database._check_where(bad)


def test_where_clause_allows_constant_conditions():
    # 现网真实用法（列名 + ? 占位 + ORDER/LIMIT）不应被误伤
    database._check_where("series_id = ? ORDER BY date DESC LIMIT 1")
    database._check_where("fund_code = ? AND turnover_rate IS NOT NULL ORDER BY year")


def test_fresh_database_contains_post_migration_columns(tmp_path, monkeypatch):
    db_path = tmp_path / "fresh.db"
    monkeypatch.setattr(database, "get_db_path", lambda: str(db_path))

    database.init_database()

    with database.get_connection() as conn:
        fund_list_cols = {
            row["name"] for row in conn.execute("PRAGMA table_info(fund_list)")
        }
        holdings_cols = {
            row["name"] for row in conn.execute("PRAGMA table_info(fund_holdings)")
        }

    assert {"mgmt_fee", "custody_fee"} <= fund_list_cols
    assert {"turnover_rates", "region_breakdown"} <= holdings_cols
