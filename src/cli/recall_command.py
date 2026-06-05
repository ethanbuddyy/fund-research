"""语义检索子命令（run.py --recall）：在已沉淀的语料上做 BM25 词法检索。

独立分支，不触发数据采集；只读 documents 表。
"""

from __future__ import annotations


def run_recall(query: str) -> None:
    from ..retrieval.recall import recall, is_enabled

    if not is_enabled():
        print("[检索] 检索层已关闭（settings.yaml: retrieval.enabled = false）。")
        print("  打开后重新运行 python3 run.py 积累语料，再 --recall 检索。")
        return

    print(f"[检索] 查询：「{query}」")
    try:
        hits = recall(query)
    except Exception as e:
        print(f"[检索] 失败：{e}")
        return

    if not hits:
        print("[检索] 无命中。")
        print("  提示：语料随 python3 run.py（叙事/报告）与单基金研判（--analyze）逐步积累；")
        print("  也可确认 settings.yaml: retrieval.enabled 为 true。")
        return

    print(f"[检索] 命中 {len(hits)} 条（按相关度排序）：\n")
    for i, h in enumerate(hits, 1):
        date = h.meta.get("date", "") if isinstance(h.meta, dict) else ""
        meta_str = f"  {date}" if date else ""
        title = h.title or "(无标题)"
        print(f"  {i}. [{h.doc_type}] {title}  score={h.score}{meta_str}")
        snippet = (h.snippet or "").replace("\n", " ")
        if len(snippet) > 160:
            snippet = snippet[:160] + "…"
        print(f"     {snippet}\n")
