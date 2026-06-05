"""报告展示层共享的「阈值 → 判定/标签」纯函数（单一事实来源）。

Markdown 渲染器(`reports/report_builder.py`)与 HTML 渲染器
(`reports/html_report_builder.py`)曾各自硬编码同一批业务阈值（VIX 偏高线、
信用利差偏紧线、趋势强弱线…）。改一处忘改另一处，会让两份报告对同一数据给出
互相矛盾的结论。这里把这些**业务口径**集中定义，两个渲染器统一引用；
各自的排版/配色 token 仍留在各自文件里（那是展示细节，不是业务口径）。

只放「会同时影响两个渲染器结论」的判定；纯展示性的颜色分档不放这里。
"""

# ── 业务阈值常量（改这里即可同时影响 MD 与 HTML 两份报告）──
VIX_ELEVATED = 25.0     # VIX 高于此值视为「偏高/高波动」，触发风险提示
VIX_NEUTRAL_LOW = 15.0  # VIX 在 [15, 25] 视为情绪中性区
CREDIT_TIGHT = 3.5      # 信用利差评分 ≤ 此值视为「信用环境偏紧」
CREDIT_LOOSE = 6.0      # 信用利差评分 ≥ 此值视为「流动性宽松」
TREND_STRONG = 6.5      # 趋势分 ≥ 此值为「强趋势」
TREND_WEAK = 3.5        # 趋势分 ≤ 此值为「弱趋势」


def _f(v):
    """容错转 float；None / 非数字返回 None。"""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def vix_elevated(vix) -> bool:
    """VIX 是否偏高（> 25）。None/非数字视为否。"""
    f = _f(vix)
    return f is not None and f > VIX_ELEVATED


def vix_neutral(vix) -> bool:
    """VIX 是否在情绪中性区 [15, 25]。None/非数字视为否。"""
    f = _f(vix)
    return f is not None and VIX_NEUTRAL_LOW <= f <= VIX_ELEVATED


def credit_tight(score) -> bool:
    """信用利差评分是否偏紧（≤ 3.5）。None/非数字视为否。"""
    f = _f(score)
    return f is not None and f <= CREDIT_TIGHT


def credit_loose(score) -> bool:
    """信用利差评分是否宽松（≥ 6.0）。None/非数字视为否。"""
    f = _f(score)
    return f is not None and f >= CREDIT_LOOSE


def trend_strong(score) -> bool:
    """趋势分是否为强趋势（≥ 6.5）。None/非数字视为否。"""
    f = _f(score)
    return f is not None and f >= TREND_STRONG


def trend_label(score) -> str:
    """趋势分 → 强趋势 / 弱趋势 / 中性趋势。None/非数字按中性处理。"""
    f = _f(score)
    if f is None:
        return "中性趋势"
    if f >= TREND_STRONG:
        return "强趋势"
    if f <= TREND_WEAK:
        return "弱趋势"
    return "中性趋势"
