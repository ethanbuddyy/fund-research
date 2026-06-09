"""三源语料汇聚到 store。

- ingest_run：把「本次运行产出、渲染进报告后即丢弃」的文本沉淀下来
  （市场叙事 / 组合备注）。
- ingest_reports_dir：收编历史报告 reports/*.md，按 H2 标题分块。
- ingest_fund_analysis：单基金研判（analyze_command 调用）。

全部 fail-soft：任何一条失败只打印告警、不阻断主流程（沿用项目采集层风格）。
ingestion 受 retrieval.enabled 总开关控制——关掉则一条都不写。
"""

from __future__ import annotations

from collections.abc import Mapping
import re
from pathlib import Path
from typing import Any, Optional

from ..utils.config import load_config
from .store import upsert_document

# 项目根（src/retrieval/ingest.py → 上溯两级）
_ROOT = Path(__file__).resolve().parents[2]

_H2 = re.compile(r"^##\s+(.*)$", flags=re.MULTILINE)


def _enabled() -> bool:
    # 单一真相源：复用 recall.is_enabled（懒导入避免加载顺序耦合）
    try:
        from .recall import is_enabled
        return is_enabled()
    except Exception:
        return False


def _index_reports_flag() -> bool:
    try:
        return bool(load_config().get("retrieval", {}).get("index_reports_dir", True))
    except Exception:
        return False


def ingest_run(signal: Mapping[str, Any], scores_df=None, portfolio: Optional[Mapping[str, Any]] = None) -> int:
    """持久化本次运行的「用完即弃」文本。返回入库条数。"""
    if not _enabled():
        return 0
    n = 0
    date = (signal or {}).get("date", "") if signal else ""

    # 市场叙事（规则模板 或 AI phase1 的 market_narrative，统一落在 narrative.insights）
    try:
        insights = (signal or {}).get("narrative", {}).get("insights", []) or []
        text = "\n".join(s for s in insights if s)
        if text.strip():
            meta = {
                "date": date,
                "composite_signal": (signal or {}).get("composite_signal", ""),
                "data_source": (signal or {}).get("data_source", ""),
                "ai_enhanced": (signal or {}).get("narrative", {}).get("ai_enhanced", False),
            }
            if upsert_document("narrative", date, f"市场叙事 {date}", text, meta):
                n += 1
    except Exception as e:
        print(f"[retrieval.ingest] 叙事落库跳过: {e}")

    # 组合投资备注（AI phase2 产出，否则无）
    try:
        notes = (portfolio or {}).get("investment_notes", "")
        if isinstance(notes, str) and notes.strip():
            if upsert_document(
                "narrative", date, f"组合备注 {date}", notes, {"date": date, "kind": "portfolio_notes"}
            ):
                n += 1
    except Exception as e:
        print(f"[retrieval.ingest] 组合备注落库跳过: {e}")

    # 顺带收编历史报告（幂等去重，多跑无害）
    if _index_reports_flag():
        n += ingest_reports_dir()

    return n


def ingest_fund_analysis(result: dict) -> int:
    """单基金研判落库（analyze_command 调用）。结论 → fund_analysis；地区展望 → region。"""
    if not _enabled() or not result:
        return 0
    n = 0
    info = result.get("fund_info", {}) or {}
    code = str(result.get("fund_code") or info.get("fund_code") or "")
    name = str(info.get("fund_name") or "")

    try:
        concl = result.get("conclusion", {}) or {}
        summary = concl.get("summary", "") if isinstance(concl, dict) else ""
        if summary.strip():
            if upsert_document(
                "fund_analysis", code, f"{name} 研判结论", summary, {"fund_code": code, "fund_name": name}
            ):
                n += 1
    except Exception as e:
        print(f"[retrieval.ingest] 研判落库跳过: {e}")

    try:
        ro = result.get("region_outlook") or {}
        focus = ro.get("focus_region") or {}
        rsum = focus.get("summary", "")
        if isinstance(rsum, str) and rsum.strip():
            region = focus.get("name", "") or info.get("region", "")
            if upsert_document(
                "region", region, f"地区展望 {region}", rsum, {"region": region, "fund_code": code}
            ):
                n += 1
    except Exception as e:
        print(f"[retrieval.ingest] 地区展望落库跳过: {e}")

    return n


def ingest_reports_dir(dirpath: Optional[str] = None) -> int:
    """扫 reports/*.md，按 H2 标题分块入库（doc_type=report）。幂等去重。返回新增块数。"""
    if not _enabled():
        return 0
    base = Path(dirpath) if dirpath else (_ROOT / "reports")
    if not base.exists():
        return 0
    n = 0
    for md in sorted(base.glob("*.md")):
        try:
            content = md.read_text(encoding="utf-8")
        except Exception:
            continue
        for title, chunk in _split_by_h2(content):
            if not chunk.strip():
                continue
            if upsert_document(
                "report", md.name, title, chunk, {"file": md.name}
            ):
                n += 1
    return n


def _split_by_h2(content: str) -> list[tuple[str, str]]:
    """按 `## 标题` 把 markdown 切块；每块含标题行及其后正文到下一个 H2。"""
    matches = list(_H2.finditer(content))
    if not matches:
        return [("(全文)", content)]
    chunks: list[tuple[str, str]] = []
    # H2 之前的导言（含 H1）
    if matches[0].start() > 0:
        intro = content[: matches[0].start()].strip()
        if intro:
            chunks.append(("(导言)", intro))
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        chunks.append((title, content[m.start() : end].strip()))
    return chunks
