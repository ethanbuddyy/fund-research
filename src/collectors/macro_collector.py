"""采集美国宏观经济数据（FRED API）"""
import pandas as pd
from datetime import datetime, timedelta
from ..utils.config import load_config
from ..utils.database import upsert_dataframe


SERIES_NAMES = {
    "GDP": "美国GDP（十亿美元）",
    "CPIAUCSL": "CPI（城市消费者）",
    "CPILFESL": "核心CPI（剔除食品能源）",
    "FEDFUNDS": "联邦基金利率",
    "GS10": "10年期国债收益率",
    "GS2": "2年期国债收益率",
    "UNRATE": "失业率",
    "MANEMP": "制造业就业",
    "RSXFS": "零售销售（剔除食品）",
    "HOUST": "新屋开工数",
    "M2SL": "M2货币供应量",
}


def collect_macro_data() -> dict[str, pd.DataFrame]:
    cfg = load_config()
    api_key = cfg.get("fred_api_key", "")
    results = {}

    if not api_key or api_key == "YOUR_FRED_API_KEY_HERE":
        print("[WARN] FRED API Key未配置，使用模拟宏观数据")
        results = _generate_mock_macro()
        _save_macro(results)
        return results

    try:
        from fredapi import Fred
        fred = Fred(api_key=api_key)
        series_cfg = cfg.get("fred_series", {})
        start_date = (datetime.now() - timedelta(days=365 * 5)).strftime("%Y-%m-%d")

        for key, series_id in series_cfg.items():
            try:
                data = fred.get_series(series_id, observation_start=start_date)
                df = pd.DataFrame({"date": data.index.strftime("%Y-%m-%d"), "value": data.values})
                df["series_id"] = series_id
                df["series_name"] = SERIES_NAMES.get(series_id, key)
                df = df.dropna()
                results[series_id] = df
                print(f"[OK] 宏观数据 {series_id}: {len(df)} 条")
            except Exception as e:
                print(f"[WARN] {series_id} 获取失败: {e}")

    except ImportError:
        print("[WARN] fredapi未安装，使用模拟数据")
        results = _generate_mock_macro()

    _save_macro(results)
    return results


def _save_macro(results: dict):
    rows = []
    for series_id, df in results.items():
        for _, row in df.iterrows():
            rows.append({
                "series_id": row.get("series_id", series_id),
                "series_name": row.get("series_name", SERIES_NAMES.get(series_id, series_id)),
                "date": row["date"],
                "value": row["value"],
            })
    if rows:
        save_df = pd.DataFrame(rows)
        upsert_dataframe(save_df, "macro_data", ["series_id", "date"])
        print(f"[DB] 宏观数据已保存 {len(rows)} 条")


def _generate_mock_macro() -> dict:
    """FRED key未配置时返回模拟数据，保证系统可运行"""
    import numpy as np
    np.random.seed(int(datetime.now().strftime("%Y%m%d")))
    dates = pd.date_range(end=datetime.now(), periods=60, freq="ME")
    date_strs = dates.strftime("%Y-%m-%d").tolist()
    mock = {}

    for series_id, name in SERIES_NAMES.items():
        if series_id == "FEDFUNDS":
            values = [5.33] * 60
        elif series_id == "GS10":
            values = (4.2 + np.random.randn(60) * 0.3).tolist()
        elif series_id == "GS2":
            values = (4.8 + np.random.randn(60) * 0.3).tolist()
        elif series_id == "UNRATE":
            values = (4.1 + np.random.randn(60) * 0.2).clip(3, 6).tolist()
        elif series_id == "CPIAUCSL":
            values = (310 + np.cumsum(np.random.randn(60) * 0.3)).tolist()
        elif series_id == "CPILFESL":
            values = (320 + np.cumsum(np.random.randn(60) * 0.2)).tolist()
        else:
            values = (100 + np.random.randn(60) * 5).tolist()

        mock[series_id] = pd.DataFrame({
            "date": date_strs,
            "value": values,
            "series_id": series_id,
            "series_name": name,
        })
    return mock
