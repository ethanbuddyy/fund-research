"""每日自动数据更新调度器"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import schedule
import time
import logging
import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def _beijing_schedule_time(hour: int, minute: int) -> str:
    """将北京时间 HH:MM 转换为系统本地时间字符串，供 schedule 库使用。"""
    beijing = ZoneInfo("Asia/Shanghai")
    today = datetime.date.today()
    t = datetime.datetime(today.year, today.month, today.day, hour, minute, tzinfo=beijing)
    local = t.astimezone()
    return f"{local.hour:02d}:{local.minute:02d}"

_LOG_PATH = Path(__file__).parent / "data" / "scheduler.log"
_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(_LOG_PATH), encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


def run_daily_update():
    logger.info("=" * 50)
    logger.info("开始每日数据更新...")

    try:
        from src.utils.database import init_database
        init_database()

        from src.collectors.macro_collector import collect_macro_data
        collect_macro_data()
        logger.info("[1/5] 宏观数据更新完成")

        from src.collectors.market_collector import collect_market_data
        collect_market_data()
        logger.info("[2/5] 市场数据更新完成")

        from src.collectors.fund_collector import collect_fund_data
        collect_fund_data()
        logger.info("[3/5] 基金数据更新完成")

        from src.collectors.valuation_collector import collect_valuation_data
        collect_valuation_data()
        logger.info("[3.5/5] 真实估值数据更新完成")

        from src.analyzers.fund_analyzer import analyze_all_funds
        analyze_all_funds()
        logger.info("[4/5] 基金绩效分析完成")

        from src.recommender.signals import generate_market_signal
        from src.recommender.scorer import score_all_funds
        signal = generate_market_signal()
        score_all_funds(signal)
        logger.info(f"[5/5] 投资信号生成完成 → {signal.get('composite_signal', '—')}")

        logger.info("每日数据更新完成！")
    except Exception as e:
        logger.error(f"数据更新失败: {e}", exc_info=True)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="立即执行一次更新")
    args = parser.parse_args()

    if args.once:
        logger.info("执行单次数据更新...")
        run_daily_update()
    else:
        # 每天早上 8:30 北京时间更新（自动适配系统时区）
        schedule_time = _beijing_schedule_time(8, 30)
        schedule.every().day.at(schedule_time).do(run_daily_update)
        tz_name = datetime.datetime.now().astimezone().tzname()
        logger.info(f"调度器启动，每天 08:30 北京时间（本地时区 {tz_name} = {schedule_time}）自动更新数据")
        logger.info("按 Ctrl+C 停止调度器")
        while True:
            schedule.run_pending()
            time.sleep(60)
