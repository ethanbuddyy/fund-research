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

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

NAV_CSV = DATA_DIR / "fund_nav_seed.csv"
LIST_CSV = DATA_DIR / "fund_list_seed.csv"

# 真实费率（管理费+托管费，不含申购赎回）
CORE_QDII_FUNDS = [
    {"fund_code": "513100", "fund_name": "纳斯达克100ETF(华夏)",    "fund_type": "ETF",      "benchmark": "纳斯达克100", "region": "美国",   "expense_ratio": 0.006},
    {"fund_code": "513500", "fund_name": "标普500ETF(南方)",        "fund_type": "ETF",      "benchmark": "标普500",    "region": "美国",   "expense_ratio": 0.006},
    {"fund_code": "159941", "fund_name": "纳斯达克ETF(博时)",       "fund_type": "ETF",      "benchmark": "纳斯达克100", "region": "美国",   "expense_ratio": 0.006},
    {"fund_code": "040046", "fund_name": "华安标普500增强",         "fund_type": "增强指数",  "benchmark": "标普500",    "region": "美国",   "expense_ratio": 0.012},
    {"fund_code": "006479", "fund_name": "易方达标普科技",          "fund_type": "被动指数",  "benchmark": "标普科技",   "region": "美国",   "expense_ratio": 0.012},
    {"fund_code": "206005", "fund_name": "博时标普500ETF联接",      "fund_type": "ETF联接",  "benchmark": "标普500",    "region": "美国",   "expense_ratio": 0.0085},
    {"fund_code": "161130", "fund_name": "标普500指数LOF(富国)",    "fund_type": "LOF",      "benchmark": "标普500",    "region": "美国",   "expense_ratio": 0.006},
    {"fund_code": "002803", "fund_name": "摩根标普500指数",         "fund_type": "被动指数",  "benchmark": "标普500",    "region": "美国",   "expense_ratio": 0.007},
    {"fund_code": "513880", "fund_name": "华夏野村日经225ETF",      "fund_type": "ETF",      "benchmark": "日经225",    "region": "日本",   "expense_ratio": 0.006},
    {"fund_code": "513000", "fund_name": "华安日本股票ETF",         "fund_type": "ETF",      "benchmark": "MSCI日本",   "region": "日本",   "expense_ratio": 0.006},
    {"fund_code": "164403", "fund_name": "工银日本股票LOF",         "fund_type": "LOF",      "benchmark": "MSCI日本",   "region": "日本",   "expense_ratio": 0.018},
    {"fund_code": "015691", "fund_name": "华泰柏瑞日经225ETF",      "fund_type": "ETF",      "benchmark": "日经225",    "region": "日本",   "expense_ratio": 0.006},
    {"fund_code": "050026", "fund_name": "博时日本ETF联接",         "fund_type": "ETF联接",  "benchmark": "日经225",    "region": "日本",   "expense_ratio": 0.006},
    {"fund_code": "513030", "fund_name": "华安德国DAX ETF",        "fund_type": "ETF",      "benchmark": "DAX",        "region": "德国",   "expense_ratio": 0.006},
    {"fund_code": "160218", "fund_name": "博时德国DAX ETF联接",     "fund_type": "ETF联接",  "benchmark": "DAX",        "region": "德国",   "expense_ratio": 0.006},
    {"fund_code": "164701", "fund_name": "招商欧洲精选LOF",         "fund_type": "LOF",      "benchmark": "MSCI欧洲",   "region": "欧洲",   "expense_ratio": 0.018},
    {"fund_code": "001548", "fund_name": "汇添富欧洲市场",          "fund_type": "主动QDII", "benchmark": "MSCI欧洲",   "region": "欧洲",   "expense_ratio": 0.0175},
    {"fund_code": "003318", "fund_name": "易方达欧洲基金",          "fund_type": "被动指数",  "benchmark": "MSCI欧洲",   "region": "欧洲",   "expense_ratio": 0.012},
    {"fund_code": "270042", "fund_name": "广发全球精选",            "fund_type": "主动QDII", "benchmark": "MSCI全球",   "region": "全球",   "expense_ratio": 0.018},
    {"fund_code": "110022", "fund_name": "易方达亚洲精选",          "fund_type": "主动QDII", "benchmark": "MSCI亚洲",   "region": "亚洲",   "expense_ratio": 0.018},
    {"fund_code": "481010", "fund_name": "工银全球股票",            "fund_type": "主动QDII", "benchmark": "MSCI全球",   "region": "全球",   "expense_ratio": 0.018},
    {"fund_code": "485010", "fund_name": "工银全球精选",            "fund_type": "主动QDII", "benchmark": "MSCI全球",   "region": "全球",   "expense_ratio": 0.018},
    {"fund_code": "164906", "fund_name": "华宝标普油气LOF",         "fund_type": "LOF",      "benchmark": "标普油气",   "region": "全球",   "expense_ratio": 0.0072},
    {"fund_code": "000934", "fund_name": "汇添富全球互联网",        "fund_type": "主动QDII", "benchmark": "纳斯达克100", "region": "美国",   "expense_ratio": 0.018},
    {"fund_code": "519977", "fund_name": "长信全球债券",            "fund_type": "QDII债券", "benchmark": "全球债券",   "region": "全球",   "expense_ratio": 0.009},
]

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
