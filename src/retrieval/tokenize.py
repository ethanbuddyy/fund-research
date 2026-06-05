"""零依赖中英混合分词。

检索语料里既有英文新闻（latin 词），又有中文叙事/研判（无空格）。
策略：
- latin/数字连续段：`[a-z0-9]+` 小写切词（一个 token）。
- CJK 连续段：切**字符二元组**（bigram）——"美联储降息" → 美联,联储,储降,降息。
  单字成段（长度 1）退化为单字 token。

bigram 在不引入分词词典/外部依赖的前提下，对中文检索召回明显优于单字，
且与 BM25 的词频统计天然契合。
"""

import re

# 一段连续 latin/数字，或一段连续 CJK（其余字符作分隔符丢弃）
_LATIN = re.compile(r"[a-zA-Z0-9]+")
_CJK = re.compile(r"[一-鿿㐀-䶿]+")
# 把文本切成「类型块」：latin 块 vs CJK 块
_SEGMENT = re.compile(r"[a-zA-Z0-9]+|[一-鿿㐀-䶿]+")


def _cjk_bigrams(seg: str) -> list[str]:
    if len(seg) <= 1:
        return [seg]
    return [seg[i : i + 2] for i in range(len(seg) - 1)]


def tokenize(text: str) -> list[str]:
    """中英混合分词 → token 列表（可含重复，供词频统计）。"""
    if not text:
        return []
    tokens: list[str] = []
    for seg in _SEGMENT.findall(text):
        if _LATIN.fullmatch(seg):
            tokens.append(seg.lower())
        else:  # CJK 段
            tokens.extend(_cjk_bigrams(seg))
    return tokens
