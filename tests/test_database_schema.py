"""数据库基线 schema 与增量迁移必须在全新库和旧库上得到同一结构。"""

from src.utils import database


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
