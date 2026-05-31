"""一键启动入口：初始化数据库 → 拉取数据 → 启动仪表盘"""
import sys
import os
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def init():
    print("=" * 60)
    print("  基金投资私人幕僚系统 — 启动中")
    print("=" * 60)

    from src.utils.database import init_database
    init_database()
    print("[OK] 数据库初始化完成")


def fetch_data():
    print("\n[数据采集] 开始获取最新数据...")

    from src.collectors.macro_collector import collect_macro_data
    collect_macro_data()

    from src.collectors.market_collector import collect_market_data
    collect_market_data()

    from src.collectors.fund_collector import collect_fund_data
    collect_fund_data()

    from src.analyzers.fund_analyzer import analyze_all_funds
    analyze_all_funds()

    from src.recommender.signals import generate_market_signal
    from src.recommender.scorer import score_all_funds
    signal = generate_market_signal()
    score_all_funds(signal)

    print(f"\n[信号] 综合市场信号：{signal.get('composite_signal', '—')}")
    print(f"  经济周期：{signal.get('macro_cycle', '—')}")
    print(f"  估值水位：{signal.get('valuation_level', '—')}")
    print(f"  建议仓位：核心{signal.get('core_allocation', 0)*100:.0f}% | 卫星{signal.get('satellite_allocation', 0)*100:.0f}% | 现金{signal.get('cash_allocation', 0)*100:.0f}%")
    return signal


def launch_dashboard():
    dashboard_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard", "app.py")
    print(f"\n[仪表盘] 启动 Streamlit → http://localhost:8501")
    print("按 Ctrl+C 停止服务\n")
    subprocess.run([
        sys.executable, "-m", "streamlit", "run", dashboard_path,
        "--server.port", "8501",
        "--server.headless", "false",
        "--browser.gatherUsageStats", "false",
    ])


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="基金投资私人幕僚系统")
    parser.add_argument("--skip-fetch", action="store_true", help="跳过数据采集，直接启动仪表盘")
    parser.add_argument("--fetch-only", action="store_true", help="仅更新数据，不启动仪表盘")
    args = parser.parse_args()

    init()

    if not args.skip_fetch:
        fetch_data()

    if not args.fetch_only:
        launch_dashboard()
