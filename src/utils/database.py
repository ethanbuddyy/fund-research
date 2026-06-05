import sqlite3
import pandas as pd
from pathlib import Path
from .config import get_db_path

# 已知表白名单：read_table/upsert_dataframe 把 table 名以 f-string 拼进 SQL（无法参数化），
# 当前所有调用点都传内部常量、不存在注入面；但表名是字符串入参而非白名单，
# 一旦未来有人把用户输入当 table 传进来就会瞬间引入 SQL 注入。这里把边界钉死：
# 只允许 schema 中实际存在的表，挡住任何意外/恶意的表名。
_KNOWN_TABLES = frozenset({
    "collection_meta", "fund_fees", "fund_holdings", "fund_list", "fund_manager",
    "fund_nav_history", "fund_performance", "fund_scores", "fund_turnover",
    "fund_year_returns", "global_macro", "macro_data", "market_data",
    "market_signals", "news_sentiment", "valuation_data",
})


def _check_table(table: str) -> None:
    if table not in _KNOWN_TABLES:
        raise ValueError(
            f"未知数据表名 {table!r}（不在白名单内）。"
            "表名以字符串拼入 SQL，仅允许 schema 中已定义的表，禁止传入动态/外部值。"
        )


def get_connection() -> sqlite3.Connection:
    db_path = get_db_path()
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_database():
    conn = get_connection()
    cur = conn.cursor()

    # 增量迁移：为已有表添加新列（SQLite 不支持 IF NOT EXISTS on ALTER，用 try/except）
    _migrations = [
        "ALTER TABLE fund_list ADD COLUMN mgmt_fee REAL",
        "ALTER TABLE fund_list ADD COLUMN custody_fee REAL",
        "ALTER TABLE fund_holdings ADD COLUMN turnover_rates TEXT",
        "ALTER TABLE fund_holdings ADD COLUMN region_breakdown TEXT",
    ]
    for sql in _migrations:
        try:
            cur.execute(sql)
        except sqlite3.OperationalError as e:
            # 仅吞两类正常情况：①「列已存在」=重复迁移；②「表不存在」=全新库
            # （迁移在下方 CREATE TABLE 之前执行，新库此时尚无目标表，CREATE 时会带上新列）。
            # 其它结构性错误必须暴露，否则后续依赖该列的写入会以更隐蔽的方式失败。
            msg = str(e).lower()
            if "duplicate column name" not in msg and "no such table" not in msg:
                raise
    conn.commit()

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
        consistency_score REAL,
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

    CREATE TABLE IF NOT EXISTS news_sentiment (
        date TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT 'finnhub',
        bullish_pct REAL,
        bearish_pct REAL,
        news_score REAL,
        buzz REAL,
        articles_count INTEGER,
        updated_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (date, source)
    );

    CREATE TABLE IF NOT EXISTS fund_manager (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fund_code TEXT NOT NULL,
        manager_id TEXT,
        name TEXT NOT NULL,
        work_start_date TEXT,
        total_assets_managed TEXT,
        avg_annual_return REAL,
        return_1y REAL,
        return_3y REAL,
        return_5y REAL,
        managed_funds TEXT,
        description TEXT,
        source TEXT DEFAULT 'eastmoney',
        updated_at TEXT DEFAULT (datetime('now')),
        UNIQUE(fund_code, name)
    );

    CREATE TABLE IF NOT EXISTS fund_fees (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fund_code TEXT NOT NULL,
        fee_type TEXT NOT NULL,
        amount_min REAL,
        amount_max REAL,
        rate REAL,
        rate_desc TEXT,
        source TEXT DEFAULT 'akshare',
        updated_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS fund_turnover (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fund_code TEXT NOT NULL,
        year INTEGER NOT NULL,
        turnover_rate REAL,
        source TEXT DEFAULT 'eastmoney',
        updated_at TEXT DEFAULT (datetime('now')),
        UNIQUE(fund_code, year)
    );
    """)
    conn.commit()
    conn.close()


def upsert_dataframe(df: pd.DataFrame, table: str, unique_cols: list[str]):
    if df.empty:
        return
    _check_table(table)
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
    _check_table(table)
    conn = get_connection()
    try:
        query = f"SELECT * FROM {table}"
        if where:
            query += f" WHERE {where}"
        return pd.read_sql_query(query, conn, params=params)
    finally:
        conn.close()
