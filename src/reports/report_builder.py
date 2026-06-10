"""单基金研判 & 持仓诊断报告生成器（Markdown）。

本模块只负责两类**单一实现、无 HTML 孪生**的诊断报告：
    - build_fund_report：`--analyze <基金>` 的综合研判报告
    - build_holdings_report：`--check-holdings` 的持仓健康诊断报告

主投研报告（run.py / scheduler.py 的每期产物）已收敛为**仅 HTML**
（src/reports/html_report_builder.py），不再有 Markdown 孪生——彻底消除
「MD/HTML 双实现需人工同步」的维护负担。跨渲染器共享的业务函数/常量仍来自
report_model（单一真相源），本模块只引用其纯格式化函数 `_score`。

调用方式：
    from src.reports.report_builder import build_fund_report, build_holdings_report
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .report_model import _score


# ─────────────────────────────────────────────────────────────
# 单基金综合研判报告
# ─────────────────────────────────────────────────────────────

def build_fund_report(
    analysis_result: dict,
    output_dir: str | Path = "reports",
) -> Path:
    """将 analyze_fund() 的结构化结果生成 Markdown 研判报告，返回文件路径。"""
    fund_code = analysis_result.get("fund_code", "unknown")
    date_str  = datetime.now().strftime("%Y-%m-%d")
    content   = _fund_report_content(analysis_result, date_str)
    out_dir   = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path  = out_dir / f"{date_str}_{fund_code}_analysis.md"
    out_path.write_text(content, encoding="utf-8")
    return out_path


def _load_fund_extra(fund_code: str) -> dict:
    """加载单基金的扩展数据：逐年收益、经理详情、申购/赎回费、换手率、费率分拆。"""
    from ..utils.database import read_table
    extra: dict = {
        "year_returns": {},
        "managers": [],
        "purchase_fees": [],
        "redemption_fees": [],
        "turnover": {},
        "mgmt_fee": None,
        "custody_fee": None,
    }

    # 逐年收益
    try:
        yr_df = read_table("fund_year_returns", "fund_code = ? ORDER BY year", (fund_code,))
        if not yr_df.empty:
            extra["year_returns"] = {
                str(int(row["year"])): float(row["return_pct"])
                for _, row in yr_df.iterrows()
                if row["return_pct"] is not None
            }
    except Exception as e:
        print(f"[WARN] 报告富集：逐年收益跳过（{fund_code}）: {e}")

    # 基金经理详情
    try:
        mgr_df = read_table("fund_manager", "fund_code = ?", (fund_code,))
        if not mgr_df.empty:
            extra["managers"] = mgr_df.to_dict("records")
    except Exception as e:
        print(f"[WARN] 报告富集：基金经理跳过（{fund_code}）: {e}")

    # 申购/赎回费
    try:
        fee_df = read_table("fund_fees", "fund_code = ?", (fund_code,))
        if not fee_df.empty:
            extra["purchase_fees"]   = fee_df[fee_df["fee_type"] == "purchase"].to_dict("records")
            extra["redemption_fees"] = fee_df[fee_df["fee_type"] == "redemption"].to_dict("records")
    except Exception as e:
        print(f"[WARN] 报告富集：申购/赎回费跳过（{fund_code}）: {e}")

    # 换手率
    try:
        turn_df = read_table("fund_turnover",
                             "fund_code = ? AND turnover_rate IS NOT NULL ORDER BY year",
                             (fund_code,))
        if not turn_df.empty:
            extra["turnover"] = {
                str(int(row["year"])): float(row["turnover_rate"])
                for _, row in turn_df.iterrows()
            }
    except Exception as e:
        print(f"[WARN] 报告富集：换手率跳过（{fund_code}）: {e}")

    # 管理费/托管费分拆
    try:
        fl_df = read_table("fund_list", "fund_code = ?", (fund_code,))
        if not fl_df.empty:
            r = fl_df.iloc[0]
            mf = r.get("mgmt_fee")
            cf = r.get("custody_fee")
            extra["mgmt_fee"]    = float(mf)    if mf    is not None and mf    == mf    else None
            extra["custody_fee"] = float(cf)    if cf    is not None and cf    == cf    else None
    except Exception as e:
        print(f"[WARN] 报告富集：管理费/托管费跳过（{fund_code}）: {e}")

    return extra


# 子项数据覆盖度图标（COMPUTED=实算 / PROXY=代理 / UNAVAILABLE=缺失）
_COV = {"COMPUTED": "✅", "PROXY": "~", "UNAVAILABLE": "⚠️"}

_GRADE_LABEL = {
    "优质候选": "🟢 优质候选", "合格候选": "🔵 合格候选",
    "有明显短板": "🟡 有明显短板", "不建议配置": "🟠 不建议配置", "剔除": "🔴 剔除",
}

_REGION_LABEL_EMOJI = {"强势": "🟢", "偏强": "🔵", "中性": "🟡", "偏弱": "🟠", "弱势": "🔴"}

# 七维评分（key, 中文标签, 满分）——评分表/各维度详解共用，单一真相源
_DIM_NAMES = [
    ("performance", "业绩质量",  20),
    ("risk",        "风险控制",  20),
    ("manager",     "基金经理",  15),
    ("strategy",    "策略稳定",  15),
    ("attribution", "收益归因",  10),
    ("structure",   "规模流动",  10),
    ("cost",        "费用成本",  10),
]


def _fmt_num(v, d: int = 2) -> str:
    """NaN 安全的定点格式化：None/NaN → 「—」，否则保留 d 位小数。"""
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v:.{d}f}"


def _dimension_detail_table(scores: dict, key: str) -> str:
    """单个评分维度的子项明细表。"""
    s = scores.get(key, {})
    rows = ["| 子项 | 得分 | 满分 | 覆盖 | 备注 |", "|---|---:|---:|---|---|"]
    for sub, d in (s.get("details") or {}).items():
        sc  = d.get("score", "—")
        mx  = d.get("max", "—")
        cov = _COV.get(d.get("coverage", "?"), "?")
        note_parts = []
        for k, v in d.items():
            if k in ("score", "max", "coverage", "note"):
                continue
            note_parts.append(f"{k}={_fmt_num(v) if isinstance(v, float) else v}")
        note = d.get("note") or ("; ".join(note_parts[:3]))
        rows.append(f"| {sub} | {sc:.1f} | {mx} | {cov} | {note[:60]} |")
    return "\n".join(rows)


def _fee_table(fee_rows: list, title: str) -> str:
    """申购/赎回费率表。"""
    if not fee_rows:
        return f"**{title}**：暂无数据\n"
    lines = [f"**{title}**", "", "| 条件 | 费率 |", "|---|---:|"]
    for r in fee_rows:
        desc = r.get("rate_desc") or "—"
        rate = r.get("rate")
        rate_str = f"{rate*100:.2f}%" if rate is not None else "—"
        lines.append(f"| {desc} | {rate_str} |")
    return "\n".join(lines)


# ── _fund_report_content 的分块构造器（从单个巨函数提取，便于阅读/测试，issue #3）──

def _score_summary_table(scores: dict, total: float) -> str:
    """七维评分汇总表。"""
    rows = ["| 维度 | 得分 | 满分 | 数据覆盖 |", "|---|---:|---:|---|"]
    for key, label, max_s in _DIM_NAMES:
        s = scores.get(key, {})
        raw = s.get("score", 0)
        covers = {d.get("coverage", "?") for d in (s.get("details") or {}).values()}
        cov_str = "✅ 全量计算" if covers <= {"COMPUTED"} else "⚠️ 部分代理" if "UNAVAILABLE" in covers else "~ 代理"
        rows.append(f"| {label} | **{raw:.1f}** | {max_s} | {cov_str} |")
    rows.append(f"| **合计** | **{total:.1f}** | **100** | |")
    return "\n".join(rows)


def _peer_table(peer: dict, perf: dict) -> str:
    """同类对比表（本基金 vs 同类中位数/均值）。"""
    rows = ["| 指标 | 本基金 | 同类中位数 | 同类均值 |", "|---|---:|---:|---:|"]
    ps = peer.get("stats", {})
    metrics_cn = {
        "return_3y": ("近3年累计收益%", perf.get("return_3y")),
        "annualized_return": ("年化收益%", perf.get("annualized_return")),
        "sharpe_ratio": ("夏普比率", perf.get("sharpe_ratio")),
        "max_drawdown": ("最大回撤%", perf.get("max_drawdown")),
        "volatility": ("波动率%", perf.get("volatility")),
    }
    for col, (label, val) in metrics_cn.items():
        p = ps.get(col, {})
        rows.append(f"| {label} | {_fmt_num(val)} | {_fmt_num(p.get('median'))} | {_fmt_num(p.get('mean'))} |")
    return "\n".join(rows)


def _holding_block(hold: dict | None) -> str:
    """持仓穿透文本块。"""
    if not hold:
        return "_持仓数据暂缺，建议运行 `python run.py` 更新_"
    md = (
        f"- 数据日期：{hold.get('date', '—')}\n"
        f"- 股票比例：{_fmt_num(hold.get('stock_ratio'))}%　债券：{_fmt_num(hold.get('bond_ratio'))}%　现金：{_fmt_num(hold.get('cash_ratio'))}%\n"
    )
    codes = hold.get("stock_codes", "")
    if codes:
        code_list = [c.strip() for c in str(codes).split(",") if c.strip()]
        md += f"- 持仓股票：{', '.join(code_list[:8])}（共 {len(code_list)} 只）\n"
    return md


def _region_outlook_section(result: dict) -> str:
    """地区宏观机会评估表 + 本基金地区聚焦。"""
    ro = result.get("region_outlook")
    if not (ro and ro.get("covered_regions")):
        return "_地区宏观数据不足，请运行 `python run.py` 更新后重新分析。_"
    cov = ro["covered_regions"]
    ranking = ro.get("ranking", list(cov.keys()))
    rows = ["| 地区 | 综合 | 宏观 | 动量 | 相对 | 标签 | GDP% | 通胀% | 近1年 | vs美国3年 |",
            "|---|---:|---:|---:|---:|---|---:|---:|---:|---:|"]
    for rk in ranking:
        d = cov.get(rk)
        if not d:
            continue
        emoji   = _REGION_LABEL_EMOJI.get(d["label"], "⚪")
        gdp_c   = f"{d['gdp_growth']:+.1f}"  if d.get("gdp_growth") is not None else "—"
        infl_c  = f"{d['inflation']:+.1f}"   if d.get("inflation")  is not None else "—"
        r1_c    = f"{d['return_1y']:+.1f}%"  if d.get("return_1y") is not None else "—"
        vs3_c   = f"{d['vs_us_3y']:+.1f}%"   if d.get("vs_us_3y") is not None else "—"
        rows.append(
            f"| {rk} | {d['total']:.1f} | {d['macro_score']:.1f} | {d['momentum_score']:.1f} "
            f"| {d['relative_score']:.1f} | {emoji}{d['label']} "
            f"| {gdp_c} | {infl_c} | {r1_c} | {vs3_c} |"
        )
    table = "\n".join(rows)
    focus = ro.get("focus_region", {})
    focus_md = ""
    if focus.get("summary"):
        focus_md = (
            f"\n**本基金地区（{focus.get('name','—')}）**：{focus.get('label','—')}（{focus.get('score','—')}/10）\n\n"
            f"> {focus['summary']}"
        )
    notes_md = "\n".join(f"> ⚠️ {n}" for n in ro.get("data_notes", [])[:3])
    return table + focus_md + ("\n\n" + notes_md if notes_md else "")


def _year_returns_section(extra: dict) -> str:
    """逐年收益表。"""
    yr_map = extra.get("year_returns") or {}
    if not yr_map:
        return "\n### 逐年收益\n\n_暂无逐年收益数据（需更新数据后重新分析）_"
    rows = ["| 年份 | 收益率 |", "|---:|---:|"]
    for yr in sorted(yr_map.keys()):
        ret = yr_map[yr]
        sign = "▲" if ret >= 0 else "▼"
        rows.append(f"| {yr} | {sign} {abs(ret):.2f}% |")
    return "\n### 逐年收益\n\n" + "\n".join(rows)


def _manager_section(extra: dict) -> str:
    """基金经理详情块。"""
    mgr_list = extra.get("managers") or []
    if not mgr_list:
        return "\n### 基金经理\n\n_暂无详细经理数据（需更新数据）_"
    parts = []
    for m in mgr_list:
        name_str = m.get("name", "—")
        start = m.get("work_start_date") or "—"
        aum_m = m.get("total_assets_managed") or "—"
        composite_score = m.get("avg_annual_return")  # 东财综合评分 0-100
        tenure_ret = m.get("return_3y")               # 任期累计收益%（复用 return_3y 字段）
        desc = (m.get("description") or "")[:120]
        mgr_funds = m.get("managed_funds") or ""
        score_str = f"{composite_score:.1f}/100" if composite_score is not None else "—"
        tenure_ret_str = f"{tenure_ret:+.2f}%" if tenure_ret is not None else "—"
        parts.append(
            f"**{name_str}**　任职时长：{start}　在管规模：{aum_m}\n\n"
            f"| 东财综合评分 | 任期累计收益 |\n"
            f"|---:|---:|\n"
            f"| {score_str} | {tenure_ret_str} |\n\n"
            + (f"> {desc}\n" if desc else "")
            + (f"_在管基金：{mgr_funds[:150]}_\n" if mgr_funds else "")
        )
    return "\n### 基金经理\n\n" + "\n---\n".join(parts)


def _fee_section(extra: dict, info: dict) -> str:
    """费率详情块（管理/托管/综合 + 申购/赎回表）。"""
    mgmt_fee    = extra.get("mgmt_fee")    or info.get("mgmt_fee")
    custody_fee = extra.get("custody_fee") or info.get("custody_fee")
    er = info.get("expense_ratio")
    mgmt_str    = f"{mgmt_fee*100:.3f}%"    if mgmt_fee    is not None else "—"
    custody_str = f"{custody_fee*100:.3f}%" if custody_fee is not None else "—"
    er_str_full = f"{er*100:.3f}%" if er else "—"
    purchase_section   = _fee_table(extra.get("purchase_fees") or [],  "申购费率")
    redemption_section = _fee_table(extra.get("redemption_fees") or [], "赎回费率")
    return f"""### 费率详情

| 费用项目 | 费率 |
|---|---:|
| 管理费率（年） | {mgmt_str} |
| 托管费率（年） | {custody_str} |
| 综合年费率（管理+托管） | {er_str_full} |

{purchase_section}

{redemption_section}

> 注：申购费为直销渠道标准费率，各平台优惠力度不同，实际以购买渠道为准。"""


def _turnover_section(extra: dict) -> str:
    """换手率表。"""
    turn_map = extra.get("turnover") or {}
    if not turn_map:
        return "### 换手率\n\n_暂无换手率数据（部分基金不披露）_"
    rows = ["| 年份 | 换手率 |", "|---:|---:|"]
    for yr in sorted(turn_map.keys()):
        rows.append(f"| {yr} | {turn_map[yr]*100:.1f}% |")
    return "### 换手率\n\n" + "\n".join(rows)


def _fund_report_content(result: dict, date_str: str) -> str:
    """组装单基金研判报告全文。各分块由上面的纯构造器负责，本函数只做拼装。"""
    info   = result["fund_info"]
    perf   = result["performance"]
    adv    = result["advanced_metrics"]
    scores = result["scores"]
    vetoes = result["vetoes"]
    concl  = result["conclusion"]
    hold   = result["holdings"]
    peer   = result["peer_context"]
    extra  = _load_fund_extra(result["fund_code"])

    name    = info.get("fund_name", result["fund_code"])
    grade   = _GRADE_LABEL.get(concl["grade"], concl["grade"])
    total   = scores["total"]
    sig_fit = concl.get("fit_signal") or {}
    _f = _fmt_num

    def _detail(key: str) -> str:
        return _dimension_detail_table(scores, key)

    score_table = _score_summary_table(scores, total)
    peer_table = _peer_table(peer, perf)
    holding_md = _holding_block(hold)

    # ── 一票否决 ───────────────────────────────────────────────
    veto_md = "**无一票否决触发** ✅" if not vetoes else "\n".join(
        f"- {'🚨' if v['severity']=='hard' else '⚠️'} **[{v['id']}] {v['condition']}**：{v['detail']}"
        for v in vetoes
    )

    # ── 信号适配 ───────────────────────────────────────────────
    signal_md = ""
    if sig_fit:
        signal_md = (
            f"\n### 当前市场信号适配\n\n"
            f"- 市场信号：{sig_fit.get('composite_signal', '—')}\n"
            f"- 策略匹配分：{sig_fit.get('strategy_match_score', 0):.1f} / 10\n"
            f"- 评估：**{sig_fit.get('assessment', '—')}**\n"
        )

    _inception_raw = info.get("inception_date")
    inception = (
        "—" if _inception_raw is None
        else "—" if str(_inception_raw).lower() in ("nan", "none", "")
        else str(_inception_raw)
    )
    tenure_raw = info.get("tenure_years")
    tenure = None if (tenure_raw is None or str(tenure_raw).lower() == "nan") else tenure_raw
    aum = info.get("total_assets")
    er = info.get("expense_ratio")
    mgmt_fee    = extra.get("mgmt_fee")    or info.get("mgmt_fee")
    custody_fee = extra.get("custody_fee") or info.get("custody_fee")
    mgmt_str    = f"{mgmt_fee*100:.3f}%"    if mgmt_fee    is not None else "—"
    custody_str = f"{custody_fee*100:.3f}%" if custody_fee is not None else "—"
    er_str_full = f"{er*100:.3f}%" if er else "—"

    region_outlook_section = _region_outlook_section(result)
    year_returns_section   = _year_returns_section(extra)
    manager_section        = _manager_section(extra)
    fee_section            = _fee_section(extra, info)
    turnover_section       = _turnover_section(extra)

    return f"""# 单基金综合研判报告

**基金**：{name}（{result['fund_code']}）　　**研判日期**：{date_str}　　**综合等级**：{grade}

> **一句话结论**：{concl['summary']}

---

## 一、产品概况

| 项目 | 内容 |
|---|---|
| 基金代码 | {result['fund_code']} |
| 基金名称 | {name} |
| 基金类型 | {info.get('fund_type', '—')} |
| 资产类别 | {info.get('asset_class', '—')} |
| 投资地区 | {info.get('region', '—')} |
| 业绩基准 | {info.get('benchmark', '—')} |
| 成立日期 | {inception} |
| 成立年限（代理） | {f"{tenure:.1f} 年" if tenure else "—"} |
| 基金规模 | {f"{aum/1e8:.1f} 亿元" if aum and aum>0 else "—"} |
| 管理费率 | {mgmt_str} |
| 托管费率 | {custody_str} |
| 综合年费率 | {er_str_full} |
| QDII 特殊风险 | 汇率风险、QDII 额度限制、海外市场时差 |

---

## 二、综合评分（7 维 100 分制）

{score_table}

**评分等级说明**：85–100 优质候选 / 75–85 合格候选 / 65–75 有明显短板 / 50–65 不建议配置 / <50 剔除

---

## 三、一票否决检查

{veto_md}

> 说明：一票否决触发时，总分无效，结论强制为"剔除"。

---

## 四、关键量化指标对比

{peer_table}

| 高级指标 | 数值 | 说明 |
|---|---|---|
| 年化 Alpha | {_f(adv.get('alpha_annual'))}% | 相对 SP500 代理基准 |
| Beta | {_f(adv.get('beta'))} | 相对 SP500 |
| 信息比率 IR | {_f(adv.get('information_ratio'))} | 年化超额 / 年化跟踪误差 |
| 下行捕获率 | {_f(adv.get('downside_capture'))} | <1 表示跌幅小于市场 |
| 滚动3年胜率 | {f"{adv['rolling_win_rate']*100:.1f}%" if adv.get('rolling_win_rate') and adv['rolling_win_rate']==adv['rolling_win_rate'] else "—"} | vs SP500，月度滚动36期 |
| 卡玛比率 | {_f(adv.get('calmar_ratio'))} | 年化收益 / 最大回撤 |
| R² | {_f(adv.get('r_squared'))} | 市场 beta 对收益的解释度 |
| 样本月数 | {adv.get('data_months', 0)} | OLS 回归有效月数 |

---

## 五、风险特征详解

### 维度二：风险控制（{scores.get('risk', {}).get('score', 0):.1f} / 20）

{_detail("risk")}

---

## 六、持仓穿透

{holding_md}

---

## 七、收益归因

### 维度五：收益来源（{scores.get('attribution', {}).get('score', 0):.1f} / 10）

{_detail("attribution")}

> **注**：本系统以 SP500 为代理基准计算 alpha/beta/IR。QDII 应以实际跟踪基准（如纳斯达克100、日经225）为准，建议结合第三方 factor model 验证。Active Share 因缺少完整指数成分持仓数据，当前无法计算。

---

## 八、配置结论

**综合等级**：{grade}　　**总分**：{total:.1f} / 100

**赚钱逻辑**：{concl.get('earn_logic', '—')}

**主要风险**：{'、'.join(concl.get('main_risks', ['—']))}

**建议仓位角色**：{concl.get('role', '—')}
{signal_md}
### 各维度评分详解

#### 业绩质量（{scores.get('performance', {}).get('score', 0):.1f} / 20）
{_detail("performance")}

#### 基金经理（{scores.get('manager', {}).get('score', 0):.1f} / 15）
{_detail("manager")}

#### 策略稳定性（{scores.get('strategy', {}).get('score', 0):.1f} / 15）
{_detail("strategy")}

#### 规模流动性（{scores.get('structure', {}).get('score', 0):.1f} / 10）
{_detail("structure")}

#### 费用成本（{scores.get('cost', {}).get('score', 0):.1f} / 10）
{_detail("cost")}

---

---

## 九、逐年收益

{year_returns_section}

---

## 十、费率与成本

{fee_section}

---

## 十一、换手率

{turnover_section}

---

## 十二、基金经理

{manager_section}

---

## 十三、地区宏观机会评估

{region_outlook_section}

---

_本报告由单基金研判引擎自动生成，评分框架来源于《基金量化分析评分框架》（7维100分制）和《分析拆解基金方法论》（8模块）。_
_alpha/beta/IR 以 SP500 为代理基准，仅供参考，不构成投资建议。_"""


# ─────────────────────────────────────────────────────────────
# 持仓健康诊断报告（独立入口）
# ─────────────────────────────────────────────────────────────

def build_holdings_report(
    check_result: dict,
    output_dir: str | Path = "reports",
) -> Path:
    """将 check_holdings() 的结构化结果生成 Markdown 报告文件，返回文件路径。"""
    date_str = datetime.now().strftime("%Y-%m-%d")
    content = _holdings_report_content(check_result, date_str)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{date_str}_holdings_check.md"
    out_path.write_text(content, encoding="utf-8")
    return out_path


def _holdings_report_content(result: dict, date_str: str) -> str:
    _EMOJI = {"重仓进取": "🟢", "标配稳健": "🔵", "谨慎防守": "🟠", "减仓防守": "🔴"}
    _VERDICT_CN = {"green": "✅ 健康", "yellow": "⚠️ 关注", "red": "🚨 警示"}
    _SIGNAL_LABEL = {"买入": "买入↑", "增持": "增持↑", "持有": "持有·", "观望": "观望△", "回避": "回避✗"}

    composite = result.get("composite_signal", "未知")
    sig_emoji = _EMOJI.get(composite, "⚪")
    sig_date = result.get("signal_date", "")
    ana = result["analytics"]
    gap = result["gap"]
    verdict = result["verdict"]
    overall_cn = _VERDICT_CN.get(verdict["overall"], verdict["overall"])

    # ── 持仓明细表 ──────────────────────────────────────────
    rows = ["| 代码 | 名称 | 权重 | 综合评分 | 信号 | 策略匹配 | 费率 | 说明 |",
            "|---|---|---:|---:|---|---:|---:|---|"]
    for h in result["holdings"]:
        code = h["fund_code"]
        name = h.get("fund_name") or code
        w = f"{h['weight']:.1f}%"
        sc = h.get("score")
        score_str = _score(sc["total_score"]) if sc and sc.get("total_score") is not None else "—"
        sig = _SIGNAL_LABEL.get(h.get("signal") or "", "—") if code != "cash" else "—"
        strat = f"{h.get('strategy_score', 0):.1f}/10" if code != "cash" else "—"
        er = h.get("expense_ratio")
        er_str = f"{er*100:.2f}%" if er else "—"
        issue = h.get("issue") or ""
        rows.append(f"| {code} | {name} | {w} | {score_str} | {sig} | {strat} | {er_str} | {issue} |")
    fund_table = "\n".join(rows)

    # ── 资产分布表 ──────────────────────────────────────────
    ac_rows = ["| 资产类别 | 权重 |", "|---|---:|"]
    for k, v in sorted(ana["asset_class_distribution"].items(), key=lambda x: -x[1]):
        ac_rows.append(f"| {k} | {v:.1f}% |")
    ac_table = "\n".join(ac_rows)

    rg_rows = ["| 地区 | 权重 |", "|---|---:|"]
    for k, v in sorted(ana["region_distribution"].items(), key=lambda x: -x[1]):
        rg_rows.append(f"| {k} | {v:.1f}% |")
    rg_table = "\n".join(rg_rows)

    # ── Gap 表 ─────────────────────────────────────────────
    gap_lines = []
    if gap["in_recommendation"]:
        for r in gap["in_recommendation"]:
            gap_lines.append(f"- ✓ **{r['name']}**（{r['code']}）— 与系统推荐重叠")
    if gap["not_in_recommendation"]:
        for c in gap["not_in_recommendation"]:
            gap_lines.append(f"- △ {c} — 不在当前推荐池")
    if gap["missing_recommended"]:
        gap_lines.append("\n**推荐池中尚未持有：**")
        for r in gap["missing_recommended"]:
            gap_lines.append(f"- {r['name']}（{r['code']}）")
    gap_section = "\n".join(gap_lines) if gap_lines else "（无评分数据，无法对比）"

    # ── 裁决区 ─────────────────────────────────────────────
    issues_md = "\n".join(f"- 🔸 {i}" for i in verdict["issues"]) or "无"
    strengths_md = "\n".join(f"- 🔹 {s}" for s in verdict["strengths"]) or "无"
    actions_md = "\n".join(f"- {a}" for a in verdict["actions"]) or "无"

    hhi = ana["hhi"]
    hhi_label = "分散良好" if hhi < 0.4 else "中等集中" if hhi < 0.65 else "高度集中"
    ws = ana.get("weighted_score")
    wer = ana.get("weighted_expense_ratio")

    return f"""# 持仓健康诊断报告

**诊断日期**：{date_str}　　**市场信号**：{sig_emoji} {composite}（{sig_date}）　　**健康裁决**：{overall_cn}

---

## 一、持仓明细

{fund_table}

---

## 二、组合分析

### 资产类别分布

{ac_table}

### 地区分布

{rg_table}

### 关键指标

| 指标 | 数值 | 说明 |
|---|---|---|
| 集中度（HHI） | {hhi:.3f} | {hhi_label}（0=完全分散，1=单一集中） |
| 加权综合评分 | {_score(ws) if ws is not None else '—'} / 100 | 有评分数据的基金加权均分 |
| 加权策略匹配 | {ana['weighted_strategy_score']:.2f} / 10 | 与当前市场信号的适配度 |
| 加权费率 | {f"{wer:.2f}%" if wer is not None else "—"} | 持仓平均年费率 |
| 现金仓位 | {ana['cash_pct']:.1f}% | 市场建议 {ana['recommended_cash_pct']:.1f}% |
| 数据库覆盖率 | {ana['in_db_coverage_pct']:.1f}% | 有评分数据的持仓权重占比 |

---

## 三、与系统推荐对比

{gap_section}

---

## 四、健康裁决：{overall_cn}

### 问题

{issues_md}

### 亮点

{strengths_md}

### 建议操作

{actions_md}

---

_本报告由持仓诊断引擎自动生成，基于系统数据库中的基金评分与当前市场信号。_
_所有结论仅供参考，不构成投资建议，投资者应结合自身风险承受能力独立判断。_"""
