"""6因子权重与区域权重的唯一定义——signals.py 和 engine.py 共同引用此处。"""

# 趋势27% + 宏观18% + 估值18% + 情绪13.5% + 信用13.5% + 全球宏观10%
# 设计要点：全球宏观以年度 World Bank + 月度 OECD CLI 为基础，数据独立于美股价格；
# 并入后"纯标普价格/波动"驱动占比降至约 40%。
FACTOR_WEIGHTS: dict[str, float] = {
    "trend":        0.27,
    "macro":        0.18,
    "valuation":    0.18,
    "sentiment":    0.135,
    "credit":       0.135,
    "global_macro": 0.10,
}

# QDII 资产规模权重（用于全球宏观因子的区域加权）
REGION_WEIGHTS_QDII: dict[str, float] = {
    "美国": 0.40, "全球": 0.20, "日本": 0.12,
    "欧洲": 0.12, "德国": 0.08, "亚洲": 0.08,
}
