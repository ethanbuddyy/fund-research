"""持仓健康诊断子命令：逻辑调度 + 终端输出。"""
import os


def run_check_holdings(source: str) -> None:
    from ..holdings.checker import check_holdings, load_signal_from_db, parse_holdings_str
    from ..reports.report_builder import build_holdings_report

    holdings: list[dict] = []
    source = source.strip()

    if ":" in source and not source.endswith(".yaml") and not source.endswith(".yml"):
        try:
            holdings = parse_holdings_str(source)
        except ValueError as e:
            print(f"[错误] 持仓格式解析失败：{e}")
            return
    else:
        import yaml
        yaml_path = source if os.path.isabs(source) else os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "..", source
        )
        if not os.path.exists(yaml_path):
            print(f"[错误] 找不到持仓文件：{yaml_path}")
            print("  请先编辑 config/my_holdings.yaml 填入你的实际持仓，")
            print("  或使用内联格式：python3 run.py --check-holdings 'code1:40,code2:60'")
            return
        try:
            with open(yaml_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            holdings = data.get("holdings", [])
        except Exception as e:
            print(f"[错误] YAML 读取失败：{e}")
            return

    if not holdings:
        print("[错误] 持仓列表为空，请检查配置文件或输入格式")
        return

    print("[持仓诊断] 读取最新市场信号...")
    market_signal = load_signal_from_db()
    if not market_signal:
        print("[持仓诊断] 数据库无市场信号，正在采集数据（首次运行）...")
        from ..application.update_pipeline import run_update
        _signal, _, _ = run_update()
        market_signal = dict(_signal)

    try:
        result = check_holdings(holdings, market_signal)
    except ValueError as e:
        print(f"[错误] {e}")
        return

    print_check_result(result)

    try:
        report_path = build_holdings_report(result)
        print(f"\n[报告] 持仓诊断报告已生成：{report_path}")
    except Exception as e:
        print(f"\n[警告] Markdown 报告生成失败：{e}")


def print_check_result(result: dict) -> None:
    """在终端打印持仓诊断摘要。"""
    _EMOJI = {"重仓进取": "🟢", "标配稳健": "🔵", "谨慎防守": "🟠", "减仓防守": "🔴"}
    _VERDICT_EMOJI = {"green": "🟢 GREEN", "yellow": "🟡 YELLOW", "red": "🔴 RED"}
    _SIGNAL_LABEL = {"买入": "买入↑", "增持": "增持↑", "持有": "持有·", "观望": "观望△", "回避": "回避✗"}

    composite = result.get("composite_signal", "未知")
    sig_emoji = _EMOJI.get(composite, "⚪")
    sig_date = result.get("signal_date", "")

    print()
    print("═" * 56)
    print(f"  持仓健康诊断  [{sig_emoji} {composite}  {sig_date}]")
    print("═" * 56)

    print("\n  持仓明细：")
    print(f"  {'代码':<12}{'名称':<16}{'权重':>6}  {'评分':>6}  {'信号':<6}  {'策略匹配':>8}")
    print(f"  {'─'*12}{'─'*16}{'─'*6}  {'─'*6}  {'─'*6}  {'─'*8}")
    for h in result["holdings"]:
        code = h["fund_code"]
        name = (h.get("fund_name") or code)[:14]
        w = f"{h['weight']:.1f}%"
        sc = h.get("score")
        score_str = f"{sc['total_score']:.1f}" if sc and sc.get("total_score") is not None else "—"
        sig = _SIGNAL_LABEL.get(h.get("signal") or "", "—")
        strat = f"{h.get('strategy_score', 0):.1f}/10" if code != "cash" else "—"
        print(f"  {code:<12}{name:<16}{w:>6}  {score_str:>6}  {sig:<6}  {strat:>8}")

    ana = result["analytics"]
    print("\n  组合分析：")
    ac_str = "  ".join(f"{k} {v:.1f}%" for k, v in ana["asset_class_distribution"].items())
    rg_str = "  ".join(f"{k} {v:.1f}%" for k, v in ana["region_distribution"].items())
    print(f"  资产类别：{ac_str}")
    print(f"  地区分布：{rg_str}")

    hhi = ana["hhi"]
    hhi_label = "分散" if hhi < 0.4 else "中等" if hhi < 0.65 else "集中"
    print(f"  集中度（HHI）：{hhi:.2f}（{hhi_label}）")

    ws = ana.get("weighted_score")
    print(f"  加权综合评分：{ws:.1f} / 100" if ws is not None else "  加权综合评分：— （评分数据不足）")
    print(f"  加权策略匹配：{ana['weighted_strategy_score']:.1f} / 10")
    wer = ana.get("weighted_expense_ratio")
    print(f"  加权费率：{wer:.2f}%" if wer is not None else "  加权费率：—")
    print(f"  现金仓位：{ana['cash_pct']:.1f}%（市场建议 {ana['recommended_cash_pct']:.1f}%）")

    gap = result["gap"]
    print("\n  vs 系统推荐 Top-5：")
    if gap["in_recommendation"]:
        for r in gap["in_recommendation"]:
            print(f"  ✓ {r['code']} {r['name']} — 与系统推荐重叠")
    if gap["not_in_recommendation"]:
        codes = "、".join(gap["not_in_recommendation"][:4])
        print(f"  △ {codes} — 不在当前推荐池")
    if gap["missing_recommended"]:
        names = "、".join(r["name"] for r in gap["missing_recommended"][:3])
        print(f"  + 推荐池中你尚未持有：{names}")
    if gap["overlap_count"] == 0 and not gap["not_in_recommendation"]:
        print("  （持仓基金均不在数据库，无法对比）")

    verdict = result["verdict"]
    overall = _VERDICT_EMOJI.get(verdict["overall"], verdict["overall"])
    print(f"\n  健康裁决：{overall}")

    if verdict["issues"]:
        print("  问题：")
        for iss in verdict["issues"]:
            print(f"    · {iss}")
    if verdict["strengths"]:
        print("  亮点：")
        for s in verdict["strengths"]:
            print(f"    · {s}")
    if verdict["actions"]:
        print("  建议操作：")
        for a in verdict["actions"]:
            print(f"    · {a}")

    print()
    print("  注：诊断基于系统数据库中的基金评分，结果仅供参考，不构成投资建议。")
    print("═" * 56)
