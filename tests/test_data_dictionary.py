"""数据字典防漂移测试。

operationalize「文档随 schema 同 PR 更新」：断言 docs/data_dictionary.md 记录的
表集合与 database._KNOWN_TABLES 完全一致。新增/删除表却不同步文档 → 测试变红。
同时校验那几处「字段语义复用」约定确实被文档显式记录，防止它们被静默遗忘。
"""
import re
from pathlib import Path

from src.utils import database as db

_DOC = Path(__file__).resolve().parent.parent / "docs" / "data_dictionary.md"


def _documented_tables() -> set[str]:
    r"""解析二级标题 `## \`table_name\`` 中的表名。"""
    text = _DOC.read_text(encoding="utf-8")
    return set(re.findall(r"^##\s+`([a-z_]+)`", text, flags=re.MULTILINE))


class TestSchemaDocSync:
    def test_doc_exists(self):
        assert _DOC.exists(), "数据字典 docs/data_dictionary.md 缺失"

    def test_documented_tables_match_whitelist(self):
        documented = _documented_tables()
        known = set(db._KNOWN_TABLES)
        missing = known - documented      # schema 有、文档没写 → 必须补文档
        extra = documented - known        # 文档写了、schema 没有 → 文档过时
        assert not missing, f"以下表未在数据字典中记录（请补文档）: {sorted(missing)}"
        assert not extra, f"数据字典记录了不存在的表（请清理文档）: {sorted(extra)}"


class TestCriticalConventionsDocumented:
    """那些「字段名 ≠ 实际含义」的高危复用约定必须显式写进文档。"""

    def test_manager_field_reuse_documented(self):
        text = _DOC.read_text(encoding="utf-8")
        assert "avg_annual_return" in text and "综合评分" in text, \
            "fund_manager.avg_annual_return 复用存综合评分的约定未在文档说明"
        assert "return_3y" in text and "任期累计收益" in text, \
            "fund_manager.return_3y 复用存任期累计收益的约定未在文档说明"

    def test_fund_year_returns_location_noted(self):
        text = _DOC.read_text(encoding="utf-8")
        assert "fund_analyzer" in text, \
            "fund_year_returns 建表位置（不在 database.py）应在文档注明"
