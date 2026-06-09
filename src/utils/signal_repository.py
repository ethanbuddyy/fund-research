"""市场信号仓储：最终市场信号的唯一保存/读取入口（窄接口）。

把 market_signals 表的「写回」与「读最新」收敛到这一处，应用层（scheduler /
holdings 诊断 / recommender）不再各自拼 SQL 或散落 read_table 口径。
采集器等历史代码暂仍可用 database.py，本阶段不扩大改动面。
"""
from collections.abc import Mapping
from typing import Any

import pandas as pd

from .database import read_table, upsert_dataframe


def save_signal(signal: Mapping[str, Any]) -> None:
    """写回最终市场信号（按 date upsert，同一日期只存一份）。"""
    row = {
        "date": signal["date"],
        "macro_cycle": signal["macro_cycle"],
        "valuation_level": signal["valuation_level"],
        "sentiment": signal.get("sentiment_label", ""),
        "composite_signal": signal["composite_signal"],
        "cape": signal.get("cape"),
        "sp500_pe": signal.get("sp500_pe"),
        "vix": signal.get("vix"),
        "buffett_indicator": signal.get("buffett_indicator"),
        "equity_risk_premium": signal.get("equity_risk_premium"),
        "core_allocation": signal["core_allocation"],
        "satellite_allocation": signal["satellite_allocation"],
        "cash_allocation": signal["cash_allocation"],
        "notes": f"data_source={signal.get('data_source', 'unknown')}",
    }
    upsert_dataframe(pd.DataFrame([row]), "market_signals", ["date"])


def load_latest_signal() -> dict | None:
    """读取最新一条市场信号原始行（dict）；无记录返回 None。不触发网络采集。"""
    df = read_table("market_signals", "1=1 ORDER BY date DESC LIMIT 1")
    if df.empty:
        return None
    return df.iloc[0].to_dict()
