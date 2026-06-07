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
from .report_builder import (
    _key_conclusions, _trigger_conditions, primary_contradiction,
    market_narrative, alloc_logic_text, region_exposure, rule_action_items,
)

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
        _section_conclusion(signal, portfolio),
        _section_data_quality(prov_data, overall_mode),
        _section_market(signal, composite, raw_score),
        _section_global_macro(signal),
        _section_allocation(portfolio, core_pct, sat_pct, cash_pct),
        _section_scenario(portfolio),
        _section_funds(portfolio),
        _section_alternates(portfolio),
        _section_risk(portfolio, signal),
        _section_action(signal, portfolio),
        _section_backtest(backtest),
        _section_appendix(signal),
        _section_adversarial(portfolio),
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


def _section_conclusion(signal: dict, portfolio: dict) -> str:
    """首页结论：关键结论 + 本期最重要触发条件（与 MD 第一章同源）。"""
    conclusions = _key_conclusions(signal, portfolio)
    triggers = _trigger_conditions(signal, portfolio)
    if not conclusions and not triggers:
        return ""

    conc_html = "".join(
        f'<li style="margin-bottom:8px;">{_e(c)}</li>' for c in conclusions
    )
    trig_html = "".join(f"""
    <li class="trigger-item">
      <div class="trigger-icon trigger-signal">!</div>
      <div style="font-size:13px;color:var(--text);line-height:1.7">{_e(t)}</div>
    </li>""" for t in triggers)

    return f"""
<div class="section">
  <div class="section-title">首页结论</div>
  <div class="two-col">
    <div class="card">
      <div style="font-size:12px;color:var(--text-dim);text-transform:uppercase;letter-spacing:.8px;margin-bottom:12px;">关键结论</div>
      <ol style="padding-left:18px;font-size:13px;color:var(--text);line-height:1.7;margin:0;">{conc_html}</ol>
    </div>
    <div class="card">
      <div style="font-size:12px;color:var(--text-dim);text-transform:uppercase;letter-spacing:.8px;margin-bottom:12px;">本期最重要触发条件</div>
      <ul class="trigger-list">{trig_html}</ul>
    </div>
  </div>
</div>"""


def _section_data_quality(prov_data: dict, overall_mode: str) -> str:
    items = []
    src_labels = {"macro": "宏观数据", "market": "市场数据",
                  "fund": "基金净值", "valuation": "估值数据", "news": "新闻情绪"}
    for src, label in src_labels.items():
        if src not in prov_data:
            continue
        info = prov_data[src]
        mode = info.get("mode", "—")
        cls  = {"real": "dq-real", "partial": "dq-partial", "mock": "dq-mock"}.get(mode, "")
        mode_icon = {"real": "✅ 真实", "partial": "⚠️ 部分", "mock": "❌ 模拟"}.get(mode, mode)
        rows = info.get("rows", "—")
        try:
            rows_str = f"{int(rows):,}"
        except (TypeError, ValueError):
            rows_str = str(rows)
        updated = (info.get("updated_at") or "")[:16]
        items.append(f"""
      <div class="dq-item {cls}">
        <div class="dq-src">{label}</div>
        <div class="dq-mode">{mode_icon}</div>
        <div class="dq-rows">{rows_str}</div>
        <div class="dq-date">{updated}</div>
      </div>""")

    # 过期数据警告（与 MD 第二章同源）
    stale_html = ""
    try:
        from ..utils import provenance as prov_mod
        warnings = prov_mod.check_staleness()
        if warnings:
            warn_items = "".join(f'<li style="margin-bottom:4px;">{_e(w)}</li>' for w in warnings)
            stale_html = (
                '<div style="margin-top:12px;padding:10px 14px;border-radius:var(--radius);'
                'background:rgba(245,183,49,.06);border:1px solid rgba(245,183,49,.2);">'
                '<div style="font-size:12px;font-weight:600;color:var(--amber);margin-bottom:6px;">⚠️ 过期数据警告</div>'
                f'<ul style="margin:0;padding-left:18px;font-size:12px;color:var(--amber);">{warn_items}</ul></div>'
            )
        else:
            stale_html = (
                '<div style="margin-top:10px;font-size:13px;color:var(--green);">'
                '✅ 所有数据源均在有效期内。</div>'
            )
    except Exception:
        stale_html = ""

    # 检索增强层状态（提醒用户该可选板块的开关与语料量；fail-soft）
    retrieval_note = ""
    try:
        from ..retrieval.recall import status, status_line
        st = status()
        color = "var(--green)" if st["enabled"] else "var(--muted, #888)"
        retrieval_note = (
            f'<div style="margin-top:10px;font-size:13px;color:{color};">'
            f'🔎 {_e(status_line())}</div>'
        )
    except Exception:
        retrieval_note = ""

    return f"""
<div class="section">
  <div class="section-title">数据质量</div>
  <div class="dq-grid">{''.join(items)}</div>
  {stale_html}
  {retrieval_note}
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

    # 主矛盾 / 叙事 / 仓位推导（与 MD 第三章同源）
    contradiction = primary_contradiction(signal)
    narrative_text, narrative_src = market_narrative(signal)
    alloc_logic = alloc_logic_text(signal)

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
      <div style="margin-top:14px;padding-top:14px;border-top:1px solid var(--border);">
        <div style="font-size:11px;color:var(--text-dim);text-transform:uppercase;letter-spacing:.8px;margin-bottom:6px;">仓位推导逻辑</div>
        <div style="font-size:13px;color:var(--text);line-height:1.7;">{_e(alloc_logic)}</div>
      </div>
    </div>
    <div class="card" style="display:flex;flex-direction:column;gap:14px;">
      <div>
        <div style="font-size:11px;color:var(--text-dim);text-transform:uppercase;letter-spacing:.8px;margin-bottom:8px;">当前主要矛盾</div>
        <div class="narrative">{_e(contradiction) or '（暂无）'}</div>
      </div>
      <div style="flex:1;">
        <div style="font-size:11px;color:var(--text-dim);text-transform:uppercase;letter-spacing:.8px;margin-bottom:8px;">市场叙事 {_e(narrative_src)}</div>
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
        strat_s = _f(f.get("strategy_score"), 1)
        cost_s = _f(f.get("cost_score"), 1)
        consist_s = _f(f.get("consistency_score"), 1)
        er_str = f"{float(er)*100:.2f}%" if er is not None else "—"

        rat = rationales.get(code, {})
        conviction = {"high": "高", "medium": "中", "low": "低"}.get(rat.get("conviction_level", ""), "—")
        reason_raw = rat.get("cycle_fit", "") or ""
        reason = reason_raw[:80] + ("…" if len(reason_raw) > 80 else "")
        risk_raw = rat.get("risk_note", "") or ""
        risk_note = risk_raw[:80] + ("…" if len(risk_raw) > 80 else "")

        rows.append(f"""
    <tr>
      <td><span style="font-family:monospace;font-size:12px;color:var(--text-dim)">{code}</span></td>
      <td style="max-width:180px;">
        <div style="font-weight:600;color:var(--text-bright);margin-bottom:2px;">{_e(name[:28])}</div>
        {role_tag}
      </td>
      <td class="td-right" style="font-weight:700;color:var(--accent)">{weight:.1f}%</td>
      <td class="td-center">{score_str}</td>
      <td class="td-right">{perf_s}</td>
      <td class="td-right">{risk_s}</td>
      <td class="td-right">{strat_s}</td>
      <td class="td-right">{cost_s}</td>
      <td class="td-right">{consist_s}</td>
      <td class="td-right">{er_str}</td>
      <td class="td-center">{sig_tag}</td>
      <td class="td-center" style="color:var(--text-dim)">{conviction}</td>
      <td style="max-width:200px;font-size:12px;color:var(--text-dim);line-height:1.5">{_e(reason) or '—'}</td>
      <td style="max-width:180px;font-size:12px;color:var(--text-dim);line-height:1.5">{_e(risk_note) or '—'}</td>
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
          <th class="td-right">风险</th><th class="td-right">策略</th>
          <th class="td-right">费率分</th><th class="td-right">一致性</th>
          <th class="td-right">管理费</th><th class="td-center">信号</th>
          <th class="td-center">置信</th><th>推荐理由</th><th>主要风险</th>
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
        note = ("角色重叠（宽基已满3席）"
                if ("标普" in name or "S&P" in name or "全球" in name)
                else "策略匹配稍低或换仓门槛未达")
        rows.append(f"""
    <tr>
      <td><span style="font-family:monospace;font-size:12px;color:var(--text-dim)">{code}</span></td>
      <td style="color:var(--text)">{_e(name[:30])}</td>
      <td class="td-center"><span style="font-weight:700;color:{c}">{_f(sc,1)}</span></td>
      <td class="td-right">{_f(f.get('performance_score'),1)}</td>
      <td class="td-right">{_f(f.get('risk_score'),1)}</td>
      <td class="td-right">{_f(f.get('strategy_score'),1)}</td>
      <td class="td-right">{_f(f.get('cost_score'),1)}</td>
      <td style="font-size:12px;color:var(--text-dim)">{_e(note)}</td>
    </tr>""")

    return f"""
<div class="section">
  <div class="section-title">备选基金</div>
  <div class="card" style="padding:0;">
    <div style="padding:14px 18px 0;font-size:12px;color:var(--text-dim);">以下基金综合评分优秀，但未入选本期组合（换仓门槛 10 分，或角色已由更高分基金占据）：</div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>代码</th><th>基金名称</th><th class="td-center">综合分</th>
          <th class="td-right">绩效</th><th class="td-right">风险</th>
          <th class="td-right">策略</th><th class="td-right">费率分</th><th>备注</th>
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

    # 区域暴露（与 MD 第七章同源）
    region_exp = region_exposure(all_f)
    region_rows = "".join(f"""
    <tr>
      <td style="color:var(--text-bright);font-weight:600;white-space:nowrap">{_e(region)}</td>
      <td style="color:var(--text)">{_e('、'.join(items))}</td>
    </tr>""" for region, items in region_exp.items())
    region_block = f"""
    <div class="card">
      <div style="font-size:12px;color:var(--text-dim);text-transform:uppercase;letter-spacing:.8px;margin-bottom:14px;">区域暴露</div>
      <table><tbody>{region_rows}</tbody></table>
    </div>""" if region_rows else ""

    return f"""
<div class="section">
  <div class="section-title">组合暴露与风险</div>
  {region_block}
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


def _section_action(signal: dict, portfolio: dict) -> str:
    ai_dec = portfolio.get("ai_decision") or {}
    notes  = ai_dec.get("position_sizing_notes") or []
    trigs  = ai_dec.get("rebalance_triggers") or []

    items = []
    icons = ["↕", "↓", "↑", "⟳", "⚡", "📌"]
    icon_classes = ["trigger-vix", "trigger-credit", "trigger-signal",
                    "trigger-score", "trigger-vix", "trigger-signal"]

    # 无 AI 决策时退回规则层行动条目（与 MD 第八章同源），避免本节空白
    if not notes and not trigs:
        rule_items = rule_action_items(signal, portfolio)
        body = "".join(f"""
    <li class="trigger-item">
      <div class="trigger-icon {icon_classes[i % len(icon_classes)]}">{icons[i % len(icons)]}</div>
      <div style="font-size:13px;color:var(--text);line-height:1.7">{_e(t)}</div>
    </li>""" for i, t in enumerate(rule_items))
        if not body:
            return ""
        return f"""
<div class="section">
  <div class="section-title">行动计划</div>
  <div class="card">
    <ul class="trigger-list">{body}</ul>
    <div style="margin-top:14px;font-size:11px;color:var(--text-dim)">
      以上条目由规则层生成（开启 AI 分析后将提供更精细的操作建议），下次更新后重新评估触发状态。
    </div>
  </div>
</div>"""

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


_VERDICT_HTML = {
    "sound":             ("green", "🟢 未发现实质问题"),
    "minor_concerns":    ("amber", "🟡 有需注意的小瑕疵"),
    "material_concerns": ("red",   "🔴 存在实质问题，使用前请人工复核"),
}
_SEV_HTML = {"high": ("red", "🔴 高"), "medium": ("amber", "🟡 中"), "low": ("text-dim", "⚪ 低")}
_CAT_CN_HTML = {
    "data_contradiction": "与数据矛盾", "unsupported_claim": "无依据断言",
    "overstated_conviction": "过度自信", "missing_risk": "遗漏风险",
    "internal_inconsistency": "自相矛盾",
}


def _section_adversarial(portfolio: dict) -> str:
    """AI 对抗审查结论（仅启用并有结果时渲染，否则空串）。所有动态文本均转义。"""
    review = portfolio.get("adversarial_review")
    if not review:
        return ""
    color, label = _VERDICT_HTML.get(review.get("overall_verdict"), ("text-dim", "—"))
    conf = {"high": "高", "medium": "中", "low": "低"}.get(review.get("confidence"), "—")
    findings = review.get("findings") or []

    if findings:
        rows = "".join(f"""
    <tr>
      <td style="white-space:nowrap">{_SEV_HTML.get(f.get('severity'), ('text-dim','—'))[1]}</td>
      <td style="white-space:nowrap;color:var(--text-dim)">{_e(_CAT_CN_HTML.get(f.get('category'), f.get('category')))}</td>
      <td style="color:var(--text)">{_e((f.get('claim') or '')[:60])}</td>
      <td style="color:var(--text-dim)">{_e((f.get('issue') or '')[:100])}</td>
      <td style="color:var(--accent)">{_e((f.get('suggested_fix') or '')[:80])}</td>
    </tr>""" for f in findings)
        table = f"""
    <div class="table-wrap" style="margin-top:12px;">
      <table>
        <thead><tr><th>严重度</th><th>类别</th><th>被质疑的主张</th><th>问题</th><th>建议修正</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>"""
    else:
        table = '<div style="margin-top:10px;color:var(--text-dim);font-size:13px;">未提出具体问题。</div>'

    summary = f'<div style="margin-top:8px;font-size:13px;color:var(--text);">{_e(review.get("summary",""))}</div>' if review.get("summary") else ""

    return f"""
<div class="section">
  <div class="section-title">AI 对抗审查</div>
  <div class="card">
    <div style="font-size:12px;color:var(--text-dim);margin-bottom:10px;">
      由独立的「挑错」子智能体复核 AI 投资决策（与数据矛盾 / 无依据 / 过度自信 / 遗漏风险 / 自相矛盾）。此为可靠性防线，非二次背书。
    </div>
    <div style="font-size:15px;font-weight:700;color:var(--{color})">{label}</div>
    <div style="font-size:12px;color:var(--text-dim);margin-top:2px;">审查置信度：{conf}</div>
    {summary}
    {table}
  </div>
</div>"""


def _bt_metric_row(label: str, sm, ewbh, spm, b6040, key, fmt="pct", dec=2) -> str:
    def cell(m):
        v = (m or {}).get(key)
        return _pct(v, dec) if fmt == "pct" else _f(v, 3)
    return f"""
    <tr>
      <td style="color:var(--text-dim)">{label}</td>
      <td class="td-right" style="color:var(--text-bright);font-weight:600">{cell(sm)}</td>
      <td class="td-right">{cell(ewbh)}</td>
      <td class="td-right">{cell(spm)}</td>
      <td class="td-right">{cell(b6040)}</td>
    </tr>"""


def _section_backtest(backtest: Optional[dict]) -> str:
    """回测与策略验证（与 MD 第九章同源）。未回测/失败时给出占位说明。"""
    if backtest is None:
        return """
<div class="section">
  <div class="section-title">回测与策略验证</div>
  <div class="card">
    <div style="font-size:13px;color:var(--text-dim);line-height:1.8">
      本次运行未执行回测（回测耗时较长，默认跳过）。<br>
      如需回测验证，请单独运行：<code style="color:var(--accent)">python backtest.py</code>
    </div>
  </div>
</div>"""

    if "error" in backtest:
        return f"""
<div class="section">
  <div class="section-title">回测与策略验证</div>
  <div class="card"><div style="color:var(--red)">⚠️ 回测失败：{_e(backtest['error'])}</div></div>
</div>"""

    sm    = backtest.get("strat_metrics", {})
    ewbh  = backtest.get("ewbh_metrics", {})
    spm   = backtest.get("sp500_metrics", {})
    b6040 = backtest.get("b6040_metrics", {})
    ds    = backtest.get("data_source", "unknown")
    ds_label = {"real": "✅ 真实数据", "partial": "⚠️ 部分真实/近似",
                "mock": "❌ 含模拟数据(仅演示)"}.get(ds, ds)
    start = backtest.get("start_date", "—")
    end   = backtest.get("end_date", "—")
    n_periods = backtest.get("n_periods", "—")

    alpha_ewbh  = (sm.get("annualized_return", 0) or 0) - (ewbh.get("annualized_return", 0) or 0)
    alpha_sp500 = (sm.get("annualized_return", 0) or 0) - (spm.get("annualized_return", 0) or 0)

    metric_rows = (
        _bt_metric_row("累计收益", sm, ewbh, spm, b6040, "total_return") +
        _bt_metric_row("年化收益", sm, ewbh, spm, b6040, "annualized_return") +
        _bt_metric_row("夏普比率", sm, ewbh, spm, b6040, "sharpe_ratio", "num") +
        _bt_metric_row("最大回撤", sm, ewbh, spm, b6040, "max_drawdown") +
        _bt_metric_row("年化波动率", sm, ewbh, spm, b6040, "volatility") +
        _bt_metric_row("月度胜率", sm, ewbh, spm, b6040, "win_rate")
    )

    # 信号有效性
    sig_stats = backtest.get("signal_stats")
    sig_block = ""
    if sig_stats is not None and not (hasattr(sig_stats, "empty") and sig_stats.empty):
        srows = []
        try:
            for _, row in sig_stats.iterrows():
                s = str(row.get("信号", ""))
                n = row.get("出现次数", "—")
                sp_r = row.get("SP500次月均收益%", None)
                if s == "重仓进取":
                    ok = "✓ 有效" if sp_r and float(sp_r) > 1.5 else "△ 弱" if sp_r and float(sp_r) > 0 else "✗ 失效"
                elif s in ("谨慎防守", "减仓防守"):
                    ok = "✓ 有效" if sp_r and float(sp_r) < 0.5 else "△ 弱"
                else:
                    ok = "—"
                srows.append(f'<tr><td>{_e(s)}</td><td class="td-right">{n}</td>'
                             f'<td class="td-right">{_pct(sp_r,2)}</td><td>{ok}</td></tr>')
        except Exception:
            srows = []
        if srows:
            sig_block = f"""
    <div style="margin-top:16px;font-size:12px;color:var(--text-dim);text-transform:uppercase;letter-spacing:.8px;margin-bottom:8px;">信号有效性验证</div>
    <div class="table-wrap"><table>
      <thead><tr><th>信号</th><th class="td-right">出现次数</th><th class="td-right">SP500次月均收益</th><th>有效性</th></tr></thead>
      <tbody>{''.join(srows)}</tbody>
    </table></div>"""

    # 幸存者偏差修正对照
    surv_block = ""
    corrected = backtest.get("corrected_strat_metrics")
    surv_stats = backtest.get("survivorship_stats", {})
    if corrected:
        bias = (sm.get("annualized_return", 0) or 0) - (corrected.get("annualized_return", 0) or 0)
        avg_premature = surv_stats.get("avg_premature_per_period", 0)
        surv_block = f"""
    <div style="margin-top:16px;font-size:12px;color:var(--text-dim);text-transform:uppercase;letter-spacing:.8px;margin-bottom:8px;">幸存者偏差修正对照</div>
    <div style="font-size:12px;color:var(--text-dim);margin-bottom:8px;">仅允许使用成立日 ≤ 调仓日的基金参与评分（平均每期剔除 {avg_premature:.1f} 只）。</div>
    <div class="table-wrap"><table>
      <thead><tr><th>指标</th><th class="td-right">原始策略</th><th class="td-right">幸存者修正</th><th class="td-right">偏差溢价</th></tr></thead>
      <tbody>
        <tr><td>年化收益</td><td class="td-right">{_pct(sm.get('annualized_return'),2)}</td><td class="td-right">{_pct(corrected.get('annualized_return'),2)}</td><td class="td-right">{_pct(bias,2)}/年</td></tr>
        <tr><td>夏普比率</td><td class="td-right">{_f(sm.get('sharpe_ratio'),3)}</td><td class="td-right">{_f(corrected.get('sharpe_ratio'),3)}</td><td class="td-right">—</td></tr>
        <tr><td>最大回撤</td><td class="td-right">{_pct(sm.get('max_drawdown'),2)}</td><td class="td-right">{_pct(corrected.get('max_drawdown'),2)}</td><td class="td-right">—</td></tr>
      </tbody>
    </table></div>"""

    # 因子归因
    attr_block = ""
    attr = backtest.get("factor_attribution")
    if attr and "factors" in attr:
        base_ann = attr.get("base_annual_return", 0)
        arows = []
        for fname, info in sorted(attr["factors"].items(), key=lambda x: -x[1]["contribution_pct"]):
            arows.append(
                f'<tr><td>{_e(info["label"])}</td>'
                f'<td class="td-right">{info["base_weight"]*100:.1f}%</td>'
                f'<td class="td-right">{_pct(info["ablated_annual"],2)}</td>'
                f'<td class="td-right">{_pct(info["contribution_pct"],2)}</td>'
                f'<td>{_e(info["contribution_label"])}</td></tr>'
            )
        attr_block = f"""
    <div style="margin-top:16px;font-size:12px;color:var(--text-dim);text-transform:uppercase;letter-spacing:.8px;margin-bottom:8px;">因子归因（逐因子屏蔽实验）</div>
    <div style="font-size:12px;color:var(--text-dim);margin-bottom:8px;">基准策略（6因子全开）年化：<b style="color:var(--text)">{_pct(base_ann,2)}</b>；贡献 = 基准 − 屏蔽后。</div>
    <div class="table-wrap"><table>
      <thead><tr><th>因子</th><th class="td-right">原权重</th><th class="td-right">屏蔽后年化</th><th class="td-right">边际贡献</th><th>评级</th></tr></thead>
      <tbody>{''.join(arows)}</tbody>
    </table></div>"""

    surv_note = backtest.get("survivorship_note", "")
    surv_warn = (f'<div style="color:var(--amber);font-size:12px;margin-top:6px;">⚠️ 幸存者偏差：{_e(surv_note)}</div>'
                 if surv_note else "")

    return f"""
<div class="section">
  <div class="section-title">回测与策略验证</div>
  <div class="card">
    <div style="display:flex;gap:24px;flex-wrap:wrap;font-size:12px;color:var(--text-dim);margin-bottom:14px;">
      <span>数据来源：<b style="color:var(--text)">{ds_label}</b></span>
      <span>回测周期：<b style="color:var(--text)">{start} ～ {end}</b>（{n_periods} 个调仓周期）</span>
    </div>
    {surv_warn}
    <div class="table-wrap"><table>
      <thead><tr><th>指标</th><th class="td-right">本策略</th><th class="td-right">等权买持</th><th class="td-right">标普500</th><th class="td-right">60/40</th></tr></thead>
      <tbody>{metric_rows}</tbody>
    </table></div>
    <div style="margin-top:12px;display:flex;gap:24px;flex-wrap:wrap;font-size:13px;">
      <span style="color:var(--text-dim)">超额 vs 等权买持：<b class="{_ret_class(alpha_ewbh)}">{_pct(alpha_ewbh,2)}/年</b></span>
      <span style="color:var(--text-dim)">超额 vs 标普500：<b class="{_ret_class(alpha_sp500)}">{_pct(alpha_sp500,2)}/年</b></span>
    </div>
    {surv_block}
    {sig_block}
    {attr_block}
    <div style="margin-top:14px;font-size:11px;color:var(--text-dim)">回测结论仅供参考，不构成投资建议。历史绩效不代表未来表现。</div>
  </div>
</div>"""


def _section_appendix(signal: dict) -> str:
    """附录：数据源 / 评分权重 / 信号阈值 / 当期关键原始指标（与 MD 第十章同源）。"""
    try:
        from ..utils.config import load_config
        cfg = load_config()
    except Exception:
        cfg = {}
    weights = cfg.get("scoring_weights", {})
    vp = cfg.get("strategy_params", {}).get("valuation_thresholds", {})

    macro = signal.get("macro", {})
    val = signal.get("valuation", {})

    raw_rows = [
        ("Shiller CAPE", _f(signal.get("cape"), 2)),
        ("标普500 P/E", _f(signal.get("sp500_pe"), 1)),
        ("VIX", _f(signal.get("vix"), 1)),
        ("巴菲特指标（总市值/GDP）", _f(val.get("buffett_indicator"), 2)),
        ("股权风险溢价 ERP", _f(val.get("equity_risk_premium"), 2) + "%"),
        ("联邦基金利率", _f(macro.get("fed_rate"), 2) + "%"),
        ("失业率", _f(macro.get("unemployment"), 2) + "%"),
        ("GDP 增速(YoY)", _f(macro.get("gdp_growth"), 2) + "%"),
        ("期限利差(10Y-2Y)", _f(macro.get("yield_curve"), 2) + "%"),
        ("综合评分", _f(signal.get("timing_score"), 3) + "/10"),
    ]
    raw_html = "".join(f'<tr><td style="color:var(--text-dim)">{_e(k)}</td>'
                       f'<td class="td-right" style="color:var(--text-bright);font-weight:600">{v}</td></tr>'
                       for k, v in raw_rows)

    weight_rows = [
        ("业绩（绩效）", weights.get("performance", 0.30)),
        ("风险调整（夏普+回撤+波动）", weights.get("risk_adjusted", 0.25)),
        ("策略匹配（信号适配）", weights.get("strategy_match", 0.20)),
        ("费率效率", weights.get("cost_efficiency", 0.15)),
        ("跨期一致性", weights.get("consistency", 0.10)),
    ]
    weight_html = "".join(f'<tr><td style="color:var(--text-dim)">{_e(k)}</td>'
                          f'<td class="td-right" style="color:var(--text-bright);font-weight:600">{v*100:.0f}%</td></tr>'
                          for k, v in weight_rows)

    threshold_rows = [
        ("综合评分 ≥ 7.0", "重仓进取：核心70%/卫星25%/现金5%"),
        ("综合评分 5.0–7.0", "标配稳健：核心60%/卫星30%/现金10%"),
        ("综合评分 3.0–5.0", "谨慎防守：核心50%/卫星20%/现金30%"),
        ("综合评分 < 3.0", "减仓防守：核心35%/卫星15%/现金50%"),
        ("CAPE 高估线", str(vp.get("cape_overvalued", 30))),
        ("CAPE 低估线", str(vp.get("cape_undervalued", 15))),
    ]
    threshold_html = "".join(f'<tr><td style="color:var(--text-dim);white-space:nowrap">{_e(k)}</td>'
                             f'<td style="color:var(--text)">{_e(v)}</td></tr>'
                             for k, v in threshold_rows)

    ds_rows = [
        ("宏观数据", "FRED API（GDP、PCE、FEDFUNDS、UNRATE、BAMLH0A0HYM2 等）"),
        ("市场数据", "yfinance（^GSPC、^VIX、SP500 历史）"),
        ("估值数据", "multpl.com CAPE / FRED"),
        ("全球宏观", "World Bank / OECD"),
        ("基金数据", "akshare / 天天基金 pingzhongdata"),
        ("新闻情绪", "Alpha Vantage NEWS_SENTIMENT / Finnhub（含 fallback）"),
    ]
    ds_html = "".join(f'<tr><td style="color:var(--text-dim);white-space:nowrap">{_e(k)}</td>'
                      f'<td style="color:var(--text)">{_e(v)}</td></tr>'
                      for k, v in ds_rows)

    return f"""
<div class="section">
  <div class="section-title">附录</div>
  <div class="two-col">
    <div class="card">
      <div style="font-size:12px;color:var(--text-dim);text-transform:uppercase;letter-spacing:.8px;margin-bottom:12px;">当期关键原始指标</div>
      <table><tbody>{raw_html}</tbody></table>
    </div>
    <div style="display:flex;flex-direction:column;gap:14px;">
      <div class="card">
        <div style="font-size:12px;color:var(--text-dim);text-transform:uppercase;letter-spacing:.8px;margin-bottom:12px;">基金评分权重</div>
        <table><tbody>{weight_html}</tbody></table>
      </div>
      <div class="card">
        <div style="font-size:12px;color:var(--text-dim);text-transform:uppercase;letter-spacing:.8px;margin-bottom:12px;">综合信号阈值</div>
        <table><tbody>{threshold_html}</tbody></table>
      </div>
    </div>
  </div>
  <div class="card" style="margin-top:14px;">
    <div style="font-size:12px;color:var(--text-dim);text-transform:uppercase;letter-spacing:.8px;margin-bottom:12px;">数据源</div>
    <table><tbody>{ds_html}</tbody></table>
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
