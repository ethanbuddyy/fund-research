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


def _read_last_signal() -> str | None:
    """从数据库读取上次保存的综合信号（经 SignalRepository，不直接拼 SQL）。"""
    try:
        from src.utils.signal_repository import load_latest_signal
        row = load_latest_signal()
        return row.get("composite_signal") if row else None
    except Exception:
        return None


def run_daily_update():
    logger.info("=" * 50)
    logger.info("开始每日数据更新...")
    prev_signal = _read_last_signal()

    try:
        from src.application.update_pipeline import run_update
        signal, scores_df, portfolio = run_update(logger=logger)
        new_signal = signal.get("composite_signal", "—")

        if prev_signal and prev_signal != new_signal:
            logger.warning(
                f"【信号变化】{prev_signal} → {new_signal}  "
                f"CAPE={signal.get('cape', '—')}  VIX={signal.get('vix', '—')}  "
                f"建议仓位：核心{signal.get('core_allocation', 0)*100:.0f}%"
                f"/卫星{signal.get('satellite_allocation', 0)*100:.0f}%"
                f"/现金{signal.get('cash_allocation', 0)*100:.0f}%"
            )
        elif not prev_signal:
            logger.info(f"首次运行，基准信号设为：{new_signal}")

        from src.utils.provenance import check_staleness
        stale = check_staleness()
        for w in stale:
            logger.warning(f"[数据过期] {w}")

        try:
            from src.reports.html_report_builder import build_html_report
            report_path = build_html_report(signal, portfolio, scores_df=scores_df)
            logger.info(f"[报告] 投研报告已生成（HTML）：{report_path}")
        except Exception as e:
            logger.warning(f"[报告] 生成失败（不影响数据采集）：{e}")

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
