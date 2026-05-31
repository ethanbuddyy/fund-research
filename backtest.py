"""
回测分析入口：策略有效性验证
用法：
  python backtest.py                        # 默认参数
  python backtest.py --cape 38 --cash 10    # 调优参数（CAPE阈值38，现金上限10%）
  python backtest.py --top 8 --freq Q       # 前8只基金，按季度调仓
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 强制 UTF-8 输出，避免 Windows 终端乱码
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import argparse
import pandas as pd

from src.backtester.engine import run_backtest


def print_section(title: str):
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")


def print_metrics(m: dict):
    print(f"  {'累计总收益':12s}  {m['total_return']:+7.2f}%")
    print(f"  {'年化收益':12s}  {m['annualized_return']:+7.2f}%")
    print(f"  {'夏普比率':12s}  {m['sharpe_ratio']:+7.3f}")
    print(f"  {'最大回撤':12s}  {m['max_drawdown']:+7.2f}%")
    print(f"  {'年化波动率':12s}  {m['volatility']:+7.2f}%")
    print(f"  {'月度胜率':12s}  {m['win_rate']:+7.1f}%")
    print(f"  {'样本月数':12s}  {m['n_months']:7d} 月")


def run(args):
    print("=" * 60)
    print("  基金投资私人幕僚系统 — 策略回测分析")
    print("=" * 60)
    print(f"  持仓基金数：{args.top}  调仓频率：{'月度' if args.freq == 'M' else '季度'}")
    print(f"  现金上限：{args.cash}%")

    print("\n[回测] 正在执行走向前回测，请稍候...")
    freq_code = "MS" if args.freq == "M" else "QS"
    result = run_backtest(
        top_n=args.top,
        rebalance_freq=freq_code,
        min_cash_pct=args.cash / 100,
    )

    if "error" in result:
        print(f"[ERROR] {result['error']}")
        return

    df    = result["df"]
    sm    = result["strat_metrics"]
    spm   = result["sp500_metrics"]
    b6040 = result["b6040_metrics"]

    print(f"\n  回测区间：{result['start_date']} ～ {result['end_date']}  ({result['n_periods']} 个调仓周期)")

    # 数据真实性 + 幸存者偏差披露（解读结论前必须知晓）
    ds = result.get("data_source", "unknown")
    ds_label = {"real": "✅ 真实数据", "partial": "⚠️ 部分真实/近似", "mock": "❌ 含模拟数据(仅演示)"}.get(ds, ds)
    print(f"  数据来源：{ds_label}")
    if result.get("survivorship_note"):
        print(f"  ⚠️ 幸存者偏差：{result['survivorship_note']}")

    # ── 1. 绩效对比 ──────────────────────────────
    print_section("一、绩效对比")
    header = f"  {'指标':12s}  {'本策略':>10s}  {'标普500':>10s}  {'60/40':>10s}"
    print(header)
    print(f"  {'─'*12}  {'─'*10}  {'─'*10}  {'─'*10}")
    metrics_order = [
        ("累计收益%",      "total_return"),
        ("年化收益%",      "annualized_return"),
        ("夏普比率",       "sharpe_ratio"),
        ("最大回撤%",      "max_drawdown"),
        ("年化波动%",      "volatility"),
        ("月度胜率%",      "win_rate"),
    ]
    for label, key in metrics_order:
        sv = sm[key]; spv = spm[key]; bv = b6040[key]
        print(f"  {label:12s}  {sv:>+10.2f}  {spv:>+10.2f}  {bv:>+10.2f}")

    alpha = sm["annualized_return"] - spm["annualized_return"]
    print(f"\n  超额收益（vs SP500）：{alpha:+.2f}%/年")
    print(f"  累计净值末值        ：策略={df['strat_cum'].iloc[-1]:.3f}  SP500={df['sp500_cum'].iloc[-1]:.3f}  60/40={df['b6040_cum'].iloc[-1]:.3f}")

    # ── 2. 信号有效性 ─────────────────────────────
    print_section("二、信号有效性验证（信号 → 次月市场方向）")
    sig = result["signal_stats"]
    if not sig.empty:
        # 表头
        cols = sig.columns.tolist()
        widths = [10, 10, 16, 16, 14, 14]
        header_str = "  " + "  ".join(f"{c:<{w}}" for c, w in zip(cols, widths))
        print(header_str)
        print("  " + "─" * (sum(widths) + 2 * len(widths)))
        for _, row in sig.iterrows():
            vals = [str(row[c]) for c in cols]
            row_str = "  " + "  ".join(f"{v:<{w}}" for v, w in zip(vals, widths))
            print(row_str)

        print()
        # 有效性判断
        for _, row in sig.iterrows():
            s = row["信号"]; sp_r = row["SP500次月均收益%"]
            if s == "重仓进取":
                ok = "✓" if sp_r > 1.5 else "△" if sp_r > 0 else "✗"
                print(f"  {ok} {s}({row['出现次数']}次)：SP500次月均 {sp_r:+.2f}%  【预期：显著正收益】")
            elif s == "谨慎防守" or s == "减仓防守":
                ok = "✓" if sp_r < 0.5 else "△"
                print(f"  {ok} {s}({row['出现次数']}次)：SP500次月均 {sp_r:+.2f}%  【预期：低收益/负收益】")
            else:
                print(f"  · {s}({row['出现次数']}次)：SP500次月均 {sp_r:+.2f}%")
    else:
        print("  无信号数据")

    # ── 3. 策略诊断 ───────────────────────────────
    print_section("三、策略诊断")
    sig_dist = df["signal"].value_counts()
    total    = result["n_periods"]
    print("  信号分布：")
    for s in ["重仓进取", "标配稳健", "谨慎防守", "减仓防守"]:
        n = sig_dist.get(s, 0)
        bar = "█" * int(n / total * 30)
        print(f"    {s:8s}  {n:3d}次 ({n/total*100:4.1f}%)  {bar}")

    avg_inv = df["invested"].mean()
    n_def   = sig_dist.get("谨慎防守", 0) + sig_dist.get("减仓防守", 0)
    cash_drag = (1 - avg_inv) * spm["annualized_return"]

    print(f"\n  平均投资比例：{avg_inv*100:.1f}%（现金比例 {(1-avg_inv)*100:.1f}%）")
    print(f"  估算现金机会成本：约 {cash_drag:.1f}%/年（以SP500年化为基准）")
    print(f"  防守信号时间占比：{n_def/total*100:.1f}%")

    # 主要问题诊断
    print("\n  诊断结论：")
    if alpha < -5:
        print("  [!] 策略显著跑输SP500，主因：")
        if n_def / total > 0.3:
            print(f"      → 防守信号占比{n_def/total*100:.0f}%，持续踏空牛市")
        if avg_inv < 0.65:
            print(f"      → 平均仓位仅{avg_inv*100:.0f}%，现金拖累约{cash_drag:.1f}%/年")
    elif alpha < 0:
        print("  [△] 策略轻微跑输SP500，可通过调整CAPE阈值或降低现金上限改善")
    else:
        print("  [✓] 策略跑赢SP500基准")

    # 信号方向有效性评价
    if not sig.empty and len(sig) >= 2:
        sp500_by_signal = sig.set_index("信号")["SP500次月均收益%"]
        if "重仓进取" in sp500_by_signal and "谨慎防守" in sp500_by_signal:
            direction_ok = sp500_by_signal["重仓进取"] > sp500_by_signal["谨慎防守"]
            print(f"  {'[✓]' if direction_ok else '[✗]'} 信号方向有效性：进取信号后收益{'高于' if direction_ok else '低于'}防守信号（方向{'正确' if direction_ok else '相反'}）")

    # ── 4. 年度收益 ───────────────────────────────
    print_section("四、年度收益拆解")
    df_copy = df[["strat_return", "sp500_return", "signal"]].copy()
    df_copy.index = pd.to_datetime(df_copy.index)
    ret_cols = ["strat_return", "sp500_return"]
    annual = df_copy[ret_cols].resample("YE").apply(lambda x: (1 + x).prod() - 1) * 100
    print(f"  {'年份':6s}  {'策略':>8s}  {'SP500':>8s}  {'差值':>8s}  {'信号分布'}")
    for year, row in annual.iterrows():
        yr = year.year
        strat_r = float(row.get("strat_return", 0))
        sp500_r = float(row.get("sp500_return", 0))
        diff    = strat_r - sp500_r
        sign    = "▲" if diff >= 0 else "▼"
        yr_sigs = df_copy[df_copy.index.year == yr]["signal"].value_counts().to_dict()
        sig_str = " ".join(f"{k[:2]}x{v}" for k, v in yr_sigs.items())
        print(f"  {yr:<6d}  {strat_r:>+7.1f}%  {sp500_r:>+7.1f}%  {sign}{abs(diff):>6.1f}%  {sig_str}")

    print()
    print("=" * 60)
    print("  回测完成。结论仅供参考，不构成投资建议。")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="基金投资策略回测")
    parser.add_argument("--top",  type=int,   default=5,  help="top-N funds per period (default 5)")
    parser.add_argument("--freq", type=str,   default="M", choices=["M", "Q"], help="rebalance freq M=monthly Q=quarterly")
    parser.add_argument("--cash", type=float, default=50, help="max cash pct 0-100 (default 50)")
    args = parser.parse_args()
    run(args)
