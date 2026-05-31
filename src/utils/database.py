import sqlite3
import pandas as pd
from pathlib import Path
from .config import get_db_path


def get_connection() -> sqlite3.Connection:
    db_path = get_db_path()
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_database():
    conn = get_connection()
    cur = conn.cursor()

    cur.executescript("""
    CREATE TABLE IF NOT EXISTS macro_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        series_id TEXT NOT NULL,
        series_name TEXT,
        date TEXT NOT NULL,
        value REAL,
        updated_at TEXT DEFAULT (datetime('now')),
        UNIQUE(series_id, date)
    );

    CREATE TABLE IF NOT EXISTS market_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        name TEXT,
        date TEXT NOT NULL,
        open REAL,
        high REAL,
        low REAL,
        close REAL,
        volume REAL,
        updated_at TEXT DEFAULT (datetime('now')),
        UNIQUE(symbol, date)
    );

    CREATE TABLE IF NOT EXISTS fund_list (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fund_code TEXT UNIQUE NOT NULL,
        fund_name TEXT,
        fund_type TEXT,
        manager TEXT,
        company TEXT,
        inception_date TEXT,
        expense_ratio REAL,
        nav REAL,
        nav_date TEXT,
        total_assets REAL,
        benchmark TEXT,
        updated_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS fund_nav_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fund_code TEXT NOT NULL,
        date TEXT NOT NULL,
        nav REAL,
        acc_nav REAL,
        daily_return REAL,
        UNIQUE(fund_code, date)
    );

    CREATE TABLE IF NOT EXISTS fund_performance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fund_code TEXT UNIQUE NOT NULL,
        return_1m REAL,
        return_3m REAL,
        return_6m REAL,
        return_1y REAL,
        return_3y REAL,
        return_5y REAL,
        annualized_return REAL,
        sharpe_ratio REAL,
        max_drawdown REAL,
        volatility REAL,
        updated_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS fund_scores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fund_code TEXT UNIQUE NOT NULL,
        fund_name TEXT,
        total_score REAL,
        performance_score REAL,
        risk_score REAL,
        strategy_score REAL,
        timing_score REAL,
        cost_score REAL,
        signal TEXT,
        recommendation TEXT,
        updated_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS collection_meta (
        source TEXT PRIMARY KEY,
        mode TEXT,
        rows INTEGER DEFAULT 0,
        detail TEXT,
        updated_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS fund_holdings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fund_code TEXT NOT NULL,
        date TEXT NOT NULL,
        stock_ratio REAL,
        bond_ratio REAL,
        cash_ratio REAL,
        stock_codes TEXT,
        managers TEXT,
        source TEXT,
        updated_at TEXT DEFAULT (datetime('now')),
        UNIQUE(fund_code, date)
    );

    CREATE TABLE IF NOT EXISTS global_macro (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        region TEXT NOT NULL,
        indicator TEXT NOT NULL,
        date TEXT NOT NULL,
        value REAL,
        source TEXT,
        updated_at TEXT DEFAULT (datetime('now')),
        UNIQUE(region, indicator, date)
    );

    CREATE TABLE IF NOT EXISTS valuation_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        metric TEXT NOT NULL,
        date TEXT NOT NULL,
        value REAL,
        source TEXT,
        updated_at TEXT DEFAULT (datetime('now')),
        UNIQUE(metric, date)
    );

    CREATE TABLE IF NOT EXISTS market_signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL UNIQUE,
        macro_cycle TEXT,
        valuation_level TEXT,
        sentiment TEXT,
        composite_signal TEXT,
        cape REAL,
        sp500_pe REAL,
        vix REAL,
        buffett_indicator REAL,
        equity_risk_premium REAL,
        core_allocation REAL,
        satellite_allocation REAL,
        cash_allocation REAL,
        notes TEXT,
        updated_at TEXT DEFAULT (datetime('now'))
    );
    """)
    conn.commit()
    conn.close()


def upsert_dataframe(df: pd.DataFrame, table: str, unique_cols: list[str]):
    if df.empty:
        return
    conn = get_connection()
    try:
        cols = df.columns.tolist()
        placeholders = ", ".join(["?" for _ in cols])
        col_names = ", ".join(cols)
        update_set = ", ".join([f"{c} = excluded.{c}" for c in cols if c not in unique_cols])
        sql = f"""
        INSERT INTO {table} ({col_names}) VALUES ({placeholders})
        ON CONFLICT({", ".join(unique_cols)}) DO UPDATE SET {update_set}
        """
        conn.executemany(sql, df.values.tolist())
        conn.commit()
    finally:
        conn.close()


def read_table(table: str, where: str = "", params: tuple = ()) -> pd.DataFrame:
    conn = get_connection()
    try:
        query = f"SELECT * FROM {table}"
        if where:
            query += f" WHERE {where}"
        return pd.read_sql_query(query, conn, params=params)
    finally:
        conn.close()
