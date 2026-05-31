"""
一次性脚本：从天天基金API抓取3年历史净值，保存为本地CSV种子数据。
运行方式：python tools/download_seed_data.py
成功后会生成 data/fund_nav_seed.csv 和 data/fund_list_seed.csv
"""
import sys
import os
import time
import requests
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.fund_universe import CORE_QDII_FUNDS  # 单一事实来源（含真实费率）

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

NAV_CSV = DATA_DIR / "fund_nav_seed.csv"
LIST_CSV = DATA_DIR / "fund_list_seed.csv"

HEADERS = {
    "Referer": "https://fundf10.eastmoney.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


def fetch_nav(fund_code: str, years: int = 3) -> pd.DataFrame:
    """从天天基金API获取指定基金的历史净值"""
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=365 * years)).strftime("%Y-%m-%d")
    url = "https://api.fund.eastmoney.com/f10/lsjz"

    all_rows = []
    page = 1
    page_size = 20   # 服务器每页固定返回20条，设大了也无效
    max_pages = 60   # 安全上限，3年约750交易日 / 20 = 38页

    while page <= max_pages:
        params = {
            "fundCode": fund_code,
            "pageIndex": page,
            "pageSize": page_size,
            "startDate": start_date,
            "endDate": end_date,
            "_": int(time.time() * 1000),
        }
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"  [ERROR] {fund_code} page {page}: {e}")
            break

        if data.get("ErrCode") != 0:
            print(f"  [ERROR] {fund_code}: ErrCode={data.get('ErrCode')}")
            break

        rows = data.get("Data", {}).get("LSJZList", [])
        if not rows:
            break  # 空页表示已到末尾

        all_rows.extend(rows)
        page += 1
        time.sleep(0.3)  # 礼貌延迟，避免触发限速

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    df = df.rename(columns={"FSRQ": "date", "DWJZ": "nav", "LJJZ": "acc_nav", "JZZZL": "daily_return"})
    df["fund_code"] = fund_code
    df["nav"] = pd.to_numeric(df["nav"], errors="coerce")
    df["acc_nav"] = pd.to_numeric(df["acc_nav"], errors="coerce")
    # JZZZL 格式为 "0.50" 或 "--"（无%号），转为浮点
    df["daily_return"] = df["daily_return"].replace(["--", ""], None)
    df["daily_return"] = pd.to_numeric(df["daily_return"], errors="coerce")
    df = df[["fund_code", "date", "nav", "acc_nav", "daily_return"]].dropna(subset=["nav"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


def main():
    print(f"开始下载 {len(CORE_QDII_FUNDS)} 只QDII基金历史净值（近3年）")
    print(f"输出目录: {DATA_DIR}\n")

    all_nav = []
    success, failed = [], []

    for i, fund in enumerate(CORE_QDII_FUNDS, 1):
        code = fund["fund_code"]
        name = fund["fund_name"]
        print(f"[{i:02d}/{len(CORE_QDII_FUNDS)}] {code} {name} ...", end=" ", flush=True)

        df = fetch_nav(code, years=3)
        if df.empty:
            print(f"失败（0条）")
            failed.append(code)
        else:
            all_nav.append(df)
            print(f"OK ({len(df)} 条, {df['date'].min()} ~ {df['date'].max()})")
            success.append(code)

        time.sleep(0.5)

    # 保存净值历史
    if all_nav:
        nav_df = pd.concat(all_nav, ignore_index=True)
        nav_df.to_csv(NAV_CSV, index=False, encoding="utf-8-sig")
        print(f"\n[OK] 净值历史已保存: {NAV_CSV} ({len(nav_df):,} 条)")
    else:
        print("\n[FAIL] 无数据可保存，请检查网络连接")

    # 保存基金列表（含真实费率）
    list_df = pd.DataFrame(CORE_QDII_FUNDS)
    list_df["manager"] = ""
    list_df["company"] = ""
    list_df["nav"] = 1.0
    list_df["nav_date"] = datetime.now().strftime("%Y-%m-%d")
    list_df.to_csv(LIST_CSV, index=False, encoding="utf-8-sig")
    print(f"[OK] 基金列表已保存: {LIST_CSV} ({len(list_df)} 只)")

    print(f"\n汇总: 成功 {len(success)} 只，失败 {len(failed)} 只")
    if failed:
        print(f"失败列表: {failed}")
        print("提示：失败的基金可能成立时间较短或基金代码已变更")


if __name__ == "__main__":
    main()
