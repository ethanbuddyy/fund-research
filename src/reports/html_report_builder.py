"""HTML 格式投研报告生成器。

与 report_builder.py 接收相同的 signal/portfolio 数据，
输出自包含的单文件 HTML（无外部依赖），深色专业配色。
"""
from __future__ import annotations

import html
import math
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from ..domain.labels import vix_elevated, credit_tight
from ..domain.types import MarketSignal, PortfolioRecommendation

# ─────────────────────────────────────────────────────────────
# 公共入口
# ─────────────────────────────────────────────────────────────

def build_html_report(
    signal: MarketSignal,
    portfolio: PortfolioRecommendation,
    scores_df: Optional[pd.DataFrame] = None,
    backtest: Optional[dict] = None,
    output_dir: str | Path = "reports",
) -> Path:
    date_str = signal.get("date", datetime.now().strftime("%Y-%m-%d"))
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{date_str}_fund_research_report.html"
    out_path.write_text(_render(signal, portfolio, scores_df, backtest, date_str), encoding="utf-8")
    return out_path


# ─────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────

def _e(v) -> str:
    """转义插入 HTML 的动态文本（AI 叙事 / 基金名 / 新闻源等外部来源），
    防止 `<`、`&`、`<script>` 破坏排版或在浏览器打开报告时构成存储型 XSS。
    数值字段无需调用本函数。"""
    if v is None:
        return ""
    return html.escape(str(v))

def _f(v, decimals=2) -> str:
    if v is None: return "—"
    try:
        f = float(v)
        return "—" if math.isnan(f) else f"{f:.{decimals}f}"
    except (TypeError, ValueError):
        return "—"

def _pct(v, decimals=1, sign=False) -> str:
    if v is None: return "—"
    try:
        f = float(v)
        if math.isnan(f): return "—"
        s = f"{f:+.{decimals}f}%" if sign else f"{f:.{decimals}f}%"
        return s
    except (TypeError, ValueError):
        return "—"

def _score_bar(v, max_v=10, color="var(--accent)") -> str:
    if v is None: return ""
    try:
        pct = max(0, min(100, float(v) / max_v * 100))
    except (TypeError, ValueError):
        return ""
    return (f'<div class="bar-track"><div class="bar-fill" '
            f'style="width:{pct:.1f}%;background:{color}"></div></div>')

def _signal_class(composite: str) -> str:
    return {"重仓进取": "green", "标配稳健": "blue",
            "谨慎防守": "amber", "减仓防守": "red"}.get(composite, "blue")

def _ret_class(v) -> str:
    try:
        return "pos" if float(v) >= 0 else "neg"
    except (TypeError, ValueError):
        return ""

def _score_color(v, max_v=100) -> str:
    if v is None: return "var(--text-dim)"
    try:
        r = float(v) / max_v
        if r >= 0.8: return "var(--green)"
        if r >= 0.65: return "var(--accent)"
        if r >= 0.5: return "var(--amber)"
        return "var(--red)"
    except (TypeError, ValueError):
        return "var(--text-dim)"


# ─────────────────────────────────────────────────────────────
# CSS
# ─────────────────────────────────────────────────────────────

_CSS = """
:root {
  --bg:         #070c18;
  --surface:    #0d1526;
  --card:       #121e35;
  --card2:      #162440;
  --border:     #1d2e4e;
  --border2:    #253a60;
  --accent:     #4f8cf7;
  --accent2:    #7c63f5;
  --green:      #1fd9a0;
  --red:        #f96060;
  --amber:      #f5b731;
  --purple:     #a78bfa;
  --text:       #c5d3e8;
  --text-dim:   #637191;
  --text-bright:#edf3ff;
  --text-head:  #ffffff;
  --radius:     10px;
  --radius-lg:  16px;
  --shadow:     0 4px 24px rgba(0,0,0,.45);
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
               "Microsoft YaHei", sans-serif;
  font-size: 14px;
  line-height: 1.7;
  padding: 0 0 60px;
}

/* ── Header ── */
.report-header {
  background: linear-gradient(135deg, #0a1428 0%, #0f1e3d 50%, #0d1834 100%);
  border-bottom: 1px solid var(--border2);
  padding: 36px 48px 32px;
  position: relative;
  overflow: hidden;
}
.report-header::before {
  content: "";
  position: absolute; inset: 0;
  background: radial-gradient(ellipse 60% 80% at 80% 50%,
    rgba(79,140,247,.07) 0%, transparent 70%);
  pointer-events: none;
}
.header-top { display: flex; align-items: flex-start; justify-content: space-between; }
.header-title { font-size: 22px; font-weight: 700; color: var(--text-head); letter-spacing: .3px; }
.header-sub { font-size: 13px; color: var(--text-dim); margin-top: 4px; }
.signal-badge {
  padding: 7px 20px; border-radius: 24px; font-size: 15px; font-weight: 700;
  letter-spacing: .5px;
}
.signal-badge.green  { background: rgba(31,217,160,.15); color: var(--green);  border: 1px solid rgba(31,217,160,.3); }
.signal-badge.blue   { background: rgba(79,140,247,.15); color: var(--accent); border: 1px solid rgba(79,140,247,.3); }
.signal-badge.amber  { background: rgba(245,183,49,.15);  color: var(--amber);  border: 1px solid rgba(245,183,49,.3); }
.signal-badge.red    { background: rgba(249,96,96,.15);   color: var(--red);    border: 1px solid rgba(249,96,96,.3); }

.kpi-strip {
  display: grid; grid-template-columns: repeat(4, 1fr);
  gap: 12px; margin-top: 28px;
}
.kpi {
  background: rgba(255,255,255,.04); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 14px 18px;
}
.kpi-label { font-size: 11px; color: var(--text-dim); text-transform: uppercase; letter-spacing: .8px; }
.kpi-value { font-size: 26px; font-weight: 700; color: var(--text-bright); margin-top: 2px; }
.kpi-value.green { color: var(--green); }
.kpi-value.amber { color: var(--amber); }
.kpi-value.red   { color: var(--red); }
.kpi-sub { font-size: 12px; color: var(--text-dim); margin-top: 2px; }

/* ── Main layout ── */
.main { max-width: 1280px; margin: 0 auto; padding: 32px 48px; }
.section { margin-bottom: 36px; }
.section-title {
  font-size: 13px; font-weight: 700; color: var(--accent);
  text-transform: uppercase; letter-spacing: 1.2px;
  border-left: 3px solid var(--accent); padding-left: 12px;
  margin-bottom: 18px;
}

/* ── Cards ── */
.card {
  background: var(--card); border: 1px solid var(--border);
  border-radius: var(--radius-lg); padding: 22px 24px;
  box-shadow: var(--shadow);
}
.card + .card { margin-top: 14px; }
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.three-col { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 14px; }

/* ── Factor scores ── */
.factor-grid { display: flex; flex-direction: column; gap: 13px; }
.factor-row { display: grid; grid-template-columns: 130px 1fr 64px 64px; gap: 12px; align-items: center; }
.factor-name { font-size: 13px; color: var(--text); }
.bar-track {
  height: 6px; border-radius: 3px;
  background: rgba(255,255,255,.07);
  overflow: hidden;
}
.bar-fill { height: 100%; border-radius: 3px; transition: width .4s; }
.factor-score { font-size: 14px; font-weight: 700; color: var(--text-bright); text-align: right; }
.factor-weight { font-size: 11px; color: var(--text-dim); text-align: right; }

/* ── Allocation bar ── */
.alloc-bar { display: flex; height: 10px; border-radius: 5px; overflow: hidden; margin: 14px 0 8px; gap: 2px; }
.alloc-seg { height: 100%; border-radius: 3px; }
.alloc-legend { display: flex; gap: 20px; flex-wrap: wrap; }
.legend-item { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--text-dim); }
.legend-dot { width: 8px; height: 8px; border-radius: 50%; }

/* ── Tables ── */
.table-wrap { overflow-x: auto; border-radius: var(--radius); }
table {
  width: 100%; border-collapse: collapse;
  font-size: 13px;
}
thead th {
  background: var(--card2); color: var(--text-dim);
  padding: 10px 14px; text-align: left;
  font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .7px;
  border-bottom: 1px solid var(--border2);
  white-space: nowrap;
}
tbody tr {
  border-bottom: 1px solid var(--border);
  transition: background .15s;
}
tbody tr:hover { background: rgba(79,140,247,.05); }
tbody td {
  padding: 11px 14px; color: var(--text); vertical-align: top;
}
.td-right { text-align: right; font-variant-numeric: tabular-nums; }
.td-center { text-align: center; }

/* ── Tags / badges ── */
.role-tag {
  display: inline-block; padding: 2px 9px; border-radius: 4px;
  font-size: 11px; font-weight: 600; letter-spacing: .3px;
  white-space: nowrap;
}
.role-core { background: rgba(79,140,247,.18); color: var(--accent); }
.role-sat  { background: rgba(124,99,245,.18); color: var(--purple); }

.sig-tag {
  display: inline-block; padding: 2px 9px; border-radius: 4px;
  font-size: 11px; font-weight: 600;
}
.sig-buy  { background: rgba(31,217,160,.15); color: var(--green); }
.sig-hold { background: rgba(245,183,49,.12); color: var(--amber); }
.sig-sell { background: rgba(249,96,96,.15);  color: var(--red); }
.sig-watch{ background: rgba(79,140,247,.15); color: var(--accent); }

.pos { color: var(--green); }
.neg { color: var(--red); }

/* ── Score circle ── */
.score-circle {
  display: inline-flex; align-items: center; justify-content: center;
  width: 36px; height: 36px; border-radius: 50%;
  font-size: 13px; font-weight: 700;
  border: 2px solid currentColor;
}

/* ── Narrative / blockquote ── */
.narrative {
  border-left: 3px solid var(--border2); padding: 14px 18px;
  background: rgba(79,140,247,.04); border-radius: 0 var(--radius) var(--radius) 0;
  font-size: 13.5px; line-height: 1.8; color: var(--text);
  margin: 14px 0;
}

/* ── Trigger list ── */
.trigger-list { list-style: none; display: flex; flex-direction: column; gap: 10px; }
.trigger-item {
  display: flex; gap: 12px; align-items: flex-start;
  background: rgba(255,255,255,.03); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 12px 16px;
}
.trigger-icon {
  width: 20px; height: 20px; border-radius: 50%; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  font-size: 11px; font-weight: 700; margin-top: 1px;
}
.trigger-vix   { background: rgba(249,96,96,.2);   color: var(--red);    border: 1px solid rgba(249,96,96,.3); }
.trigger-credit{ background: rgba(245,183,49,.2);  color: var(--amber);  border: 1px solid rgba(245,183,49,.3); }
.trigger-signal{ background: rgba(79,140,247,.2);  color: var(--accent); border: 1px solid rgba(79,140,247,.3); }
.trigger-score { background: rgba(124,99,245,.2);  color: var(--purple); border: 1px solid rgba(124,99,245,.3); }

/* ── Global macro table ── */
.macro-label {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600;
}
.ml-strong { background: rgba(31,217,160,.15); color: var(--green); }
.ml-ok     { background: rgba(79,140,247,.15); color: var(--accent); }
.ml-neutral{ background: rgba(245,183,49,.12); color: var(--amber); }
.ml-weak   { background: rgba(249,96,96,.15);  color: var(--red); }

/* ── Scenario cards ── */
.scenario-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 14px; }
.scenario-card { border-radius: var(--radius); padding: 18px 20px; border: 1px solid; }
.sc-bull { background: rgba(31,217,160,.06); border-color: rgba(31,217,160,.2); }
.sc-base { background: rgba(79,140,247,.06); border-color: rgba(79,140,247,.2); }
.sc-bear { background: rgba(249,96,96,.06);  border-color: rgba(249,96,96,.2); }
.sc-label { font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: .8px; margin-bottom: 8px; }
.sc-bull .sc-label { color: var(--green); }
.sc-base .sc-label { color: var(--accent); }
.sc-bear .sc-label { color: var(--red); }
.sc-text { font-size: 12.5px; line-height: 1.75; color: var(--text); }

/* ── Data quality ── */
.dq-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.dq-item {
  border-radius: var(--radius); padding: 14px 16px;
  background: rgba(255,255,255,.03); border: 1px solid var(--border);
  display: flex; flex-direction: column; gap: 4px;
}
.dq-src { font-size: 12px; font-weight: 700; color: var(--text-bright); }
.dq-mode { font-size: 11px; }
.dq-rows { font-size: 20px; font-weight: 700; }
.dq-date { font-size: 11px; color: var(--text-dim); }
.dq-real  .dq-rows { color: var(--green); }
.dq-partial .dq-rows { color: var(--amber); }
.dq-mock  .dq-rows { color: var(--red); }
.dq-real  .dq-mode { color: var(--green); }
.dq-partial .dq-mode { color: var(--amber); }
.dq-mock  .dq-mode { color: var(--red); }

/* ── Footer ── */
.report-footer {
  text-align: center; padding: 24px 0;
  font-size: 11px; color: var(--text-dim);
  border-top: 1px solid var(--border);
  margin-top: 24px;
}

/* ── Disclaimer ── */
.disclaimer {
  background: rgba(249,96,96,.06); border: 1px solid rgba(249,96,96,.2);
  border-radius: var(--radius); padding: 12px 16px; margin-bottom: 24px;
  font-size: 12px; color: var(--red);
}
.disclaimer.partial {
  background: rgba(245,183,49,.06); border-color: rgba(245,183,49,.2);
  color: var(--amber);
}

@media print {
  body { background: #fff; color: #111; }
  .report-header { background: #f8faff; border-color: #dde; }
  .card { background: #f8faff; border-color: #dde; }
}
"""


# ─────────────────────────────────────────────────────────────
# 主渲染函数
# ─────────────────────────────────────────────────────────────

def _render(signal: dict, portfolio: dict,
            scores_df: Optional[pd.DataFrame],
            backtest: Optional[dict],
            date_str: str) -> str:

    composite = signal.get("composite_signal", "标配稳健")
    sig_cls   = _signal_class(composite)
    raw_score = signal.get("timing_score", 5.0) or 5.0
    cape      = signal.get("cape")
    vix       = signal.get("vix")
    core_pct  = portfolio.get("core_allocation_pct", 60)
    sat_pct   = portfolio.get("satellite_allocation_pct", 30)
    cash_pct  = portfolio.get("cash_allocation_pct", 10)

    from src.utils import provenance as prov_mod
    prov_data = signal.get("data_quality") or prov_mod.read_all()
    overall_mode = prov_mod.overall_mode()

    sections = [
        _header(signal, portfolio, composite, sig_cls, raw_score, cape, vix,
                core_pct, sat_pct, cash_pct, date_str, overall_mode),
        f'<div class="main">',
        _section_data_quality(prov_data, overall_mode),
        _section_market(signal, composite, raw_score),
        _section_allocation(portfolio, core_pct, sat_pct, cash_pct),
        _section_funds(portfolio),
        _section_alternates(portfolio),
        _section_risk(portfolio, signal),
        _section_action(portfolio),
        _section_scenario(portfolio),
        _section_global_macro(signal),
        '</div>',
        _footer(date_str),
    ]
    body = "\n".join(s for s in sections if s)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>QDII 基金投研报告 · {date_str}</title>
<style>{_CSS}</style>
</head>
<body>
{body}
</body>
</html>"""


# ─────────────────────────────────────────────────────────────
# 各 Section 构建函数
# ─────────────────────────────────────────────────────────────

def _header(signal, portfolio, composite, sig_cls, raw_score,
            cape, vix, core_pct, sat_pct, cash_pct,
            date_str, overall_mode) -> str:
    trend = signal.get("trend_score") or 0
    credit = signal.get("credit_score") or 0
    macro_adj = signal.get("macro_adj") or signal.get("macro", {}).get("cycle_score", 0) or 0

    disclaimer = ""
    if overall_mode == "mock":
        disclaimer = '<div class="disclaimer">⚠️ 本报告基于模拟数据，不可用于实际投资决策</div>'
    elif overall_mode == "partial":
        disclaimer = '<div class="disclaimer partial">⚠️ 部分数据为估算值，结论仅供参考</div>'

    return f"""
<div class="report-header">
  <div class="header-top">
    <div>
      <div class="header-title">QDII 基金投研报告</div>
      <div class="header-sub">报告日期：{date_str}&ensp;·&ensp;数据模式：{overall_mode}&ensp;·&ensp;置信度：{'高' if overall_mode=='real' and abs(raw_score-5)>=2 else '中'}</div>
    </div>
    <div class="signal-badge {sig_cls}">{composite}</div>
  </div>
  <div class="kpi-strip">
    <div class="kpi">
      <div class="kpi-label">综合评分</div>
      <div class="kpi-value {'green' if raw_score>=7 else 'amber' if raw_score>=5 else 'red'}">{raw_score:.2f}<span style="font-size:14px;font-weight:400;color:var(--text-dim)"> /10</span></div>
      <div class="kpi-sub">→ {composite}</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">建议仓位</div>
      <div class="kpi-value" style="font-size:18px;">{core_pct:.0f}% <span style="font-size:13px;color:var(--text-dim)">核心</span> + {sat_pct:.0f}% <span style="font-size:13px;color:var(--text-dim)">卫星</span></div>
      <div class="kpi-sub">现金 {cash_pct:.0f}%</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Shiller CAPE</div>
      <div class="kpi-value {'red' if cape and cape>30 else 'amber' if cape and cape>22 else 'green'}">{_f(cape, 1)}</div>
      <div class="kpi-sub">{'极度高估' if cape and cape>35 else '高估' if cape and cape>28 else '合理'}</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">VIX 恐慌指数</div>
      <div class="kpi-value {'red' if vix and vix>25 else 'amber' if vix and vix>20 else 'green'}">{_f(vix, 1)}</div>
      <div class="kpi-sub">{'高波动' if vix and vix>25 else '中性' if vix and vix>18 else '低波动·贪婪区'}</div>
    </div>
  </div>
</div>
<div class="main">{disclaimer}</div>"""


def _section_data_quality(prov_data: dict, overall_mode: str) -> str:
    items = []
    src_labels = {"macro": "宏观数据", "market": "市场数据",
                  "fund": "基金净值", "valuation": "估值数据"}
    for src, label in src_labels.items():
        if src not in prov_data:
            continue
        info = prov_data[src]
        mode = info.get("mode", "—")
        cls  = {"real": "dq-real", "partial": "dq-partial", "mock": "dq-mock"}.get(mode, "")
        mode_icon = {"real": "✅ 真实", "partial": "⚠️ 部分", "mock": "❌ 模拟"}.get(mode, mode)
        rows = info.get("rows", "—")
        updated = (info.get("updated_at") or "")[:16]
        items.append(f"""
      <div class="dq-item {cls}">
        <div class="dq-src">{label}</div>
        <div class="dq-mode">{mode_icon}</div>
        <div class="dq-rows">{rows:,}</div>
        <div class="dq-date">{updated}</div>
      </div>""")
    return f"""
<div class="section">
  <div class="section-title">数据质量</div>
  <div class="dq-grid">{''.join(items)}</div>
</div>"""


def _section_market(signal: dict, composite: str, raw_score: float) -> str:
    macro_adj    = signal.get("macro_adj") or signal.get("macro", {}).get("cycle_score", 5) or 5
    val_score    = (signal.get("valuation") or {}).get("valuation_score", 5) or 5
    trend_score  = signal.get("trend_score") or 5
    credit_score = signal.get("credit_score") or 5
    vix          = signal.get("vix") or 18
    contrarian   = 10 - (((signal.get("sentiment") or {}).get("score") or 50)) / 10

    factors = [
        ("宏观周期",   macro_adj,    "20%", 0.20),
        ("市场估值",   val_score,    "20%", 0.20),
        ("逆向情绪",   contrarian,   "15%", 0.15),
        ("价格趋势",   trend_score,  "30%", 0.30),
        ("信用利差",   credit_score, "15%", 0.15),
    ]

    rows = []
    for name, score, weight, w in factors:
        c = ("var(--green)" if score >= 6.5 else
             "var(--amber)" if score >= 4.5 else "var(--red)")
        bar = _score_bar(score, 10, c)
        contr = score * w
        rows.append(f"""
      <div class="factor-row">
        <div class="factor-name">{name}</div>
        {bar}
        <div class="factor-score" style="color:{c}">{_f(score,1)}/10</div>
        <div class="factor-weight">{weight} → {_f(contr,2)}</div>
      </div>""")

    # 主矛盾
    ai = signal.get("ai_analysis") or {}
    contradiction = (ai.get("primary_contradiction") or
                     signal.get("macro", {}).get("cycle", "") or "")
    narrative_text = (ai.get("market_narrative") or
                      "\n".join((signal.get("narrative") or {}).get("insights", [])[:2]))

    return f"""
<div class="section">
  <div class="section-title">市场主线</div>
  <div class="two-col">
    <div class="card">
      <div style="font-size:12px;color:var(--text-dim);margin-bottom:14px;text-transform:uppercase;letter-spacing:.8px;">五因子评分 · 综合 <span style="font-size:20px;font-weight:700;color:var(--text-bright)">{raw_score:.2f}</span>/10</div>
      <div class="factor-grid">{''.join(rows)}</div>
      <div style="margin-top:14px;padding-top:14px;border-top:1px solid var(--border);display:flex;justify-content:space-between;font-size:12px;color:var(--text-dim);">
        <span>CAPE <b style="color:var(--text)">{_f(signal.get('cape'),1)}</b></span>
        <span>VIX <b style="color:var(--text)">{_f(signal.get('vix'),1)}</b></span>
        <span>联邦利率 <b style="color:var(--text)">{_f((signal.get('macro') or {}).get('fed_rate'),2)}%</b></span>
        <span>期限利差 <b style="color:var(--text)">{_f((signal.get('macro') or {}).get('yield_curve'),2)}%</b></span>
      </div>
    </div>
    <div class="card" style="display:flex;flex-direction:column;gap:14px;">
      <div>
        <div style="font-size:11px;color:var(--text-dim);text-transform:uppercase;letter-spacing:.8px;margin-bottom:8px;">当前主要矛盾</div>
        <div class="narrative">{_e(contradiction) or '（暂无）'}</div>
      </div>
      <div style="flex:1;">
        <div style="font-size:11px;color:var(--text-dim);text-transform:uppercase;letter-spacing:.8px;margin-bottom:8px;">市场叙事</div>
        <div style="font-size:13px;line-height:1.8;color:var(--text);">{_e((narrative_text or '')[:600]).replace(chr(10), '<br>')}</div>
      </div>
    </div>
  </div>
</div>"""


def _section_allocation(portfolio: dict, core_pct, sat_pct, cash_pct) -> str:
    notes = portfolio.get("investment_notes") or []
    notes_html = "".join(f'<li style="margin-bottom:6px;">{_e(n)}</li>' for n in notes[:5])

    return f"""
<div class="section">
  <div class="section-title">资产配置建议</div>
  <div class="card">
    <div class="alloc-bar">
      <div class="alloc-seg" style="width:{core_pct}%;background:var(--accent);"></div>
      <div class="alloc-seg" style="width:{sat_pct}%;background:var(--accent2);"></div>
      <div class="alloc-seg" style="width:{cash_pct}%;background:var(--text-dim);"></div>
    </div>
    <div class="alloc-legend">
      <div class="legend-item"><div class="legend-dot" style="background:var(--accent)"></div>核心 {core_pct:.0f}%</div>
      <div class="legend-item"><div class="legend-dot" style="background:var(--accent2)"></div>卫星 {sat_pct:.0f}%</div>
      <div class="legend-item"><div class="legend-dot" style="background:var(--text-dim)"></div>现金 {cash_pct:.0f}%</div>
    </div>
    {'<ul style="margin-top:16px;padding-left:18px;font-size:13px;color:var(--text);line-height:1.8;">' + notes_html + '</ul>' if notes_html else ''}
  </div>
</div>"""


def _section_funds(portfolio: dict) -> str:
    core   = portfolio.get("core_funds", [])
    sat    = portfolio.get("satellite_funds", [])
    all_f  = core + sat
    if not all_f:
        return ""

    ai_dec = portfolio.get("ai_decision") or {}
    rationales = {r.get("fund_code", ""): r for r in (ai_dec.get("fund_rationales") or [])}

    rows = []
    for f in all_f:
        code   = str(f.get("fund_code", ""))
        name   = f.get("fund_name", code)
        role   = f.get("role", "")
        weight = f.get("weight", 0)
        total  = f.get("score") or f.get("total_score")
        er     = f.get("expense_ratio")
        sig    = f.get("signal", "—")

        role_tag = (f'<span class="role-tag role-core">核心</span>'
                    if role == "核心" else
                    f'<span class="role-tag role-sat">卫星</span>')

        sig_cls2 = ({"买入": "sig-buy", "增持": "sig-buy",
                     "持有": "sig-hold", "观望": "sig-watch",
                     "回避": "sig-sell"}.get(sig, "sig-hold"))
        sig_tag = f'<span class="sig-tag {sig_cls2}">{sig}</span>'

        score_c = _score_color(total)
        score_str = f'<span style="font-weight:700;color:{score_c}">{_f(total, 1)}</span>'

        perf_s = _f(f.get("performance_score"), 1)
        risk_s = _f(f.get("risk_score"), 1)
        er_str = f"{float(er)*100:.2f}%" if er is not None else "—"

        rat = rationales.get(code, {})
        reason_raw = rat.get("cycle_fit", "") or ""
        reason = reason_raw[:80] + ("…" if len(reason_raw) > 80 else "")

        rows.append(f"""
    <tr>
      <td><span style="font-family:monospace;font-size:12px;color:var(--text-dim)">{code}</span></td>
      <td style="max-width:200px;">
        <div style="font-weight:600;color:var(--text-bright);margin-bottom:2px;">{_e(name[:28])}</div>
        {role_tag}
      </td>
      <td class="td-right" style="font-weight:700;color:var(--accent)">{weight:.1f}%</td>
      <td class="td-center">{score_str}</td>
      <td class="td-right">{perf_s}</td>
      <td class="td-right">{risk_s}</td>
      <td class="td-right">{er_str}</td>
      <td class="td-center">{sig_tag}</td>
      <td style="max-width:220px;font-size:12px;color:var(--text-dim);line-height:1.5">{_e(reason)}</td>
    </tr>""")

    return f"""
<div class="section">
  <div class="section-title">推荐基金组合</div>
  <div class="card" style="padding:0;">
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>代码</th><th>基金名称</th><th class="td-right">权重</th>
          <th class="td-center">综合分</th><th class="td-right">绩效</th>
          <th class="td-right">风险</th><th class="td-right">管理费</th>
          <th class="td-center">信号</th><th>推荐逻辑</th>
        </tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
  </div>
</div>"""


def _section_alternates(portfolio: dict) -> str:
    top   = portfolio.get("top_picks") or []
    sel   = {str(f["fund_code"]) for f in
             portfolio.get("core_funds", []) + portfolio.get("satellite_funds", [])}
    alts  = [f for f in top if str(f.get("fund_code", "")) not in sel][:5]
    if not alts:
        return ""

    rows = []
    for f in alts:
        code = str(f.get("fund_code", ""))
        name = f.get("fund_name", code)
        sc   = f.get("total_score") or f.get("score")
        c    = _score_color(sc)
        rows.append(f"""
    <tr>
      <td><span style="font-family:monospace;font-size:12px;color:var(--text-dim)">{code}</span></td>
      <td style="color:var(--text)">{_e(name[:30])}</td>
      <td class="td-center"><span style="font-weight:700;color:{c}">{_f(sc,1)}</span></td>
      <td class="td-right">{_f(f.get('performance_score'),1)}</td>
      <td class="td-right">{_f(f.get('risk_score'),1)}</td>
    </tr>""")

    return f"""
<div class="section">
  <div class="section-title">备选基金</div>
  <div class="card" style="padding:0;">
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>代码</th><th>基金名称</th><th class="td-center">综合分</th>
          <th class="td-right">绩效</th><th class="td-right">风险</th>
        </tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
  </div>
</div>"""


def _section_risk(portfolio: dict, signal: dict) -> str:
    vix = signal.get("vix") or 18
    credit = signal.get("credit_score") or 5
    all_f = portfolio.get("core_funds", []) + portfolio.get("satellite_funds", [])
    ers = [float(f["expense_ratio"]) for f in all_f if f.get("expense_ratio") is not None]
    avg_er = sum(ers) / len(ers) if ers else None

    risks = [
        ("汇率风险", "QDII 资产以外币计价，人民币升值将直接冲击净值", "amber"),
        ("溢价/折价", "场内 ETF 换汇受限时可能出现大幅溢价，避免高溢价买入", "amber"),
        ("限购风险", "部分 QDII 在额度紧张时暂停大额申购，操作前确认可购状态", "blue"),
        ("申赎成本", "开放式 QDII 申购费 0.6–1.5%，频繁操作显著侵蚀收益", "blue"),
    ]
    if vix_elevated(vix):
        risks.insert(0, (f"VIX {_f(vix,1)} 偏高", "市场波动加剧，场内溢价可能快速扩大，谨慎操作", "red"))
    if credit_tight(credit):
        risks.insert(0, ("信用利差偏高", "全球信用环境趋紧，高收益债 QDII 需警惕流动性冲击", "red"))

    risk_items = "".join(f"""
    <tr>
      <td style="color:var(--{'red' if c=='red' else 'amber' if c=='amber' else 'accent'});font-weight:600;white-space:nowrap">{t}</td>
      <td style="color:var(--text)">{d}</td>
    </tr>""" for t, d, c in risks)

    return f"""
<div class="section">
  <div class="section-title">组合暴露与风险</div>
  <div class="two-col">
    <div class="card">
      <div style="font-size:12px;color:var(--text-dim);text-transform:uppercase;letter-spacing:.8px;margin-bottom:14px;">QDII 特有风险</div>
      <table><tbody>{risk_items}</tbody></table>
    </div>
    <div class="card">
      <div style="font-size:12px;color:var(--text-dim);text-transform:uppercase;letter-spacing:.8px;margin-bottom:14px;">组合特征</div>
      <table><tbody>
        <tr><td style="color:var(--text-dim)">持仓基金数</td><td style="color:var(--text-bright);font-weight:700">{len(all_f)} 只</td></tr>
        <tr><td style="color:var(--text-dim)">加权平均费率</td><td style="color:var(--text-bright);font-weight:700">{f"{avg_er*100:.2f}%" if avg_er else "—"}</td></tr>
        <tr><td style="color:var(--text-dim)">VIX</td><td style="color:var(--{'red' if float(vix)>25 else 'amber' if float(vix)>18 else 'green'});font-weight:700">{_f(vix,1)}</td></tr>
        <tr><td style="color:var(--text-dim)">信用利差评分</td><td style="color:var(--{'red' if float(credit)<=3.5 else 'amber' if float(credit)<=5.5 else 'green'});font-weight:700">{_f(credit,1)}/10</td></tr>
      </tbody></table>
    </div>
  </div>
</div>"""


def _section_action(portfolio: dict) -> str:
    ai_dec = portfolio.get("ai_decision") or {}
    notes  = ai_dec.get("position_sizing_notes") or []
    trigs  = ai_dec.get("rebalance_triggers") or []

    items = []
    icons = ["↕", "↓", "↑", "⟳", "⚡", "📌"]
    icon_classes = ["trigger-vix", "trigger-credit", "trigger-signal",
                    "trigger-score", "trigger-vix", "trigger-signal"]

    for i, note in enumerate(notes[:4]):
        cls = icon_classes[i % len(icon_classes)]
        items.append(f"""
    <li class="trigger-item">
      <div class="trigger-icon {cls}">{icons[i % len(icons)]}</div>
      <div style="font-size:13px;color:var(--text);line-height:1.7">{_e(note)}</div>
    </li>""")

    for i, trig in enumerate(trigs[:4]):
        cond   = trig.get("condition", "")
        action = trig.get("action", "")
        if not cond or not action:
            continue
        cls = icon_classes[(i + len(notes)) % len(icon_classes)]
        items.append(f"""
    <li class="trigger-item">
      <div class="trigger-icon {cls}">!</div>
      <div style="font-size:13px;line-height:1.7">
        <span style="color:var(--text-dim)">触发：</span><span style="color:var(--text)">{_e(cond)}</span>
        <span style="color:var(--border2);margin:0 6px">→</span>
        <span style="color:var(--accent);font-weight:600">{_e(action)}</span>
      </div>
    </li>""")

    if not items:
        return ""

    return f"""
<div class="section">
  <div class="section-title">行动计划</div>
  <div class="card">
    <ul class="trigger-list">{''.join(items)}</ul>
    <div style="margin-top:14px;font-size:11px;color:var(--text-dim)">
      以上条目由 AI Phase 2 生成，基于当期量化数据，下次更新后重新评估触发状态。
    </div>
  </div>
</div>"""


def _section_scenario(portfolio: dict) -> str:
    ai_dec = portfolio.get("ai_decision") or {}
    sc     = ai_dec.get("scenario_analysis") or {}
    bull   = sc.get("bull_case", "")
    base   = sc.get("base_case", "")
    bear   = sc.get("bear_case", "")
    if not any([bull, base, bear]):
        return ""

    def _clip(s, n=200):
        clipped = (s or "")[:n] + ("…" if len(s or "") > n else "")
        return _e(clipped)

    return f"""
<div class="section">
  <div class="section-title">情景分析</div>
  <div class="scenario-grid">
    <div class="scenario-card sc-bull">
      <div class="sc-label">🟢 牛市情景</div>
      <div class="sc-text">{_clip(bull, 250)}</div>
    </div>
    <div class="scenario-card sc-base">
      <div class="sc-label">🔵 基准情景</div>
      <div class="sc-text">{_clip(base, 250)}</div>
    </div>
    <div class="scenario-card sc-bear">
      <div class="sc-label">🔴 熊市情景</div>
      <div class="sc-text">{_clip(bear, 250)}</div>
    </div>
  </div>
</div>"""


def _section_global_macro(signal: dict) -> str:
    gm = signal.get("global_macro") or {}
    if not gm.get("available") or not gm.get("regions"):
        return ""

    regions  = gm["regions"]
    strongest = gm.get("strongest", "")
    weakest   = gm.get("weakest", "")

    label_map = {"强势": ("ml-strong", "🟢"), "偏强": ("ml-ok", "🔵"),
                 "温和扩张": ("ml-ok", "🔵"), "中性": ("ml-neutral", "🟡"),
                 "放缓": ("ml-neutral", "🟡"), "偏弱": ("ml-weak", "🔴"),
                 "弱势": ("ml-weak", "🔴")}

    rows = []
    for region, data in sorted(regions.items(),
                                key=lambda x: -(x[1].get("score") or 0)):
        label = data.get("label", "—")
        cls, emoji = label_map.get(label, ("ml-neutral", ""))
        star = " ★" if region == strongest else (" ▼" if region == weakest else "")
        gdp  = data.get("gdp_growth")
        infl = data.get("inflation")
        r1y  = data.get("return_1y")
        score = data.get("score")

        rows.append(f"""
    <tr>
      <td style="font-weight:600;color:var(--text-bright)">{_e(region)}{star}</td>
      <td><span class="macro-label {cls}">{emoji} {_e(label)}</span></td>
      <td class="td-right {_ret_class(score) if score else ''}">{_f(score,1)}/10</td>
      <td class="td-right {_ret_class(gdp) if gdp else ''}">{_pct(gdp,1,True) if gdp is not None else '—'}</td>
      <td class="td-right {'neg' if infl and infl>3 else ''}">{_pct(infl,1) if infl is not None else '—'}</td>
      <td class="td-right {_ret_class(r1y) if r1y else ''}">{_pct(r1y,1,True) if r1y is not None else '—'}</td>
    </tr>""")

    return f"""
<div class="section">
  <div class="section-title">全球宏观区域对比</div>
  <div class="card" style="padding:0;">
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>区域</th><th>状态</th><th class="td-right">机会评分</th>
          <th class="td-right">GDP 增速</th><th class="td-right">通胀率</th>
          <th class="td-right">近1年涨幅</th>
        </tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
  </div>
</div>"""


def _footer(date_str: str) -> str:
    return f"""
<div class="main">
  <div class="report-footer">
    QDII 基金投研系统 · {date_str} 自动生成 &ensp;|&ensp;
    本报告不构成投资建议，投资者应结合自身风险承受能力独立判断
  </div>
</div>"""
