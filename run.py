"""一键启动入口：初始化数据库 → 拉取数据"""
import sys
import os

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

    from src.collectors.global_macro_collector import collect_global_macro
    collect_global_macro()

    from src.collectors.market_collector import collect_market_data
    collect_market_data()

    from src.collectors.fund_collector import collect_fund_data
    collect_fund_data()

    # 用天天基金 pingzhongdata 富集真实净值与持仓（覆盖 akshare/模拟净值）
    from src.collectors.eastmoney_collector import collect_eastmoney
    collect_eastmoney()

    from src.collectors.valuation_collector import collect_valuation_data
    collect_valuation_data()

    from src.analyzers.fund_analyzer import analyze_all_funds
    analyze_all_funds()

    from src.recommender.signals import generate_market_signal
    from src.recommender.scorer import score_all_funds
    signal = generate_market_signal()
    score_all_funds(signal)

    from src.utils import provenance
    print()
    print(provenance.banner())

    print(f"\n[信号] 综合市场信号：{signal.get('composite_signal', '—')}")
    print(f"  经济周期：{signal.get('macro_cycle', '—')}")
    print(f"  估值水位：{signal.get('valuation_level', '—')}")
    print(f"  建议仓位：核心{signal.get('core_allocation', 0)*100:.0f}% | 卫星{signal.get('satellite_allocation', 0)*100:.0f}% | 现金{signal.get('cash_allocation', 0)*100:.0f}%")
    return signal


if __name__ == "__main__":
    init()
    fetch_data()
