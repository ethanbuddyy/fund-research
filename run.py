"""一键启动入口：数据采集 → 信号生成 → 基金评分 → 组合推荐 → 投研报告"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    print("=" * 60)
    print("  基金投资私人幕僚系统 — 启动中")
    print("=" * 60)

    from src.application.update_pipeline import run_update
    signal, scores_df, portfolio = run_update()

    from src.utils import provenance
    print()
    print(provenance.banner())

    print(f"\n[信号] 综合市场信号：{signal.get('composite_signal', '—')}")
    print(f"  经济周期：{signal.get('macro_cycle', '—')}")
    print(f"  估值水位：{signal.get('valuation_level', '—')}")
    print(
        f"  建议仓位：核心{signal.get('core_allocation', 0)*100:.0f}%"
        f" | 卫星{signal.get('satellite_allocation', 0)*100:.0f}%"
        f" | 现金{signal.get('cash_allocation', 0)*100:.0f}%"
    )

    # 生成 Markdown 投研报告
    try:
        from src.reports.report_builder import build_report
        report_path = build_report(signal, portfolio, scores_df=scores_df)
        print(f"\n[报告] 投研报告已生成：{report_path}")
    except Exception as e:
        print(f"\n[警告] 报告生成失败（数据采集流程不受影响）：{e}")


if __name__ == "__main__":
    main()
