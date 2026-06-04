"""QDII 基金核心标的库（单一事实来源）

集中维护基金代码、名称、类型、基准、地区、真实费率与资产类别，
供 collector / scorer / 回测 / 种子下载脚本共用，避免各处重复且口径不一。

字段说明：
  fund_code     基金代码
  fund_name     基金简称
  fund_type     运作类型（ETF/LOF/被动指数/主动QDII/...）
  benchmark     跟踪基准
  region        投资地区
  expense_ratio 真实年费率（管理费+托管费，不含申赎），来源：基金合同/天天基金F10
  asset_class   资产类别（用于策略匹配，比名字关键词稳健）：
                broad_equity   宽基股票
                growth_equity  成长/科技
                sector_equity  行业主题
                bond           债券
                commodity      商品

最近核对日期：2026-06-02
核对结论：
  - 519977 长信全球债券：QDII债券基金（投资全球债券市场），分类 bond 正确；
    与可转债基金（如 519977 在某些平台的展示混淆）无关，此处以天天基金 F10 为准。
  - 费率均来源基金合同，与申购费（前端）区分（expense_ratio 为管理+托管，不含申购）。
  - 待补充：印度/越南市场宽基 QDII（目前通过 fund_screener 规则筛选覆盖，核心库未收录）。
"""

CORE_QDII_FUNDS = [
    # ── 美国宽基 ──────────────────────────────────────
    # mgmt_fee/custody_fee 来源：各基金合同/天天基金F10，核对日期 2026-06-04
    {"fund_code": "513100", "fund_name": "纳斯达克100ETF(华夏)",  "fund_type": "ETF",     "benchmark": "纳斯达克100", "region": "美国", "expense_ratio": 0.006,  "mgmt_fee": 0.005,  "custody_fee": 0.001,  "asset_class": "growth_equity"},
    {"fund_code": "513500", "fund_name": "标普500ETF(南方)",      "fund_type": "ETF",     "benchmark": "标普500",     "region": "美国", "expense_ratio": 0.006,  "mgmt_fee": 0.005,  "custody_fee": 0.001,  "asset_class": "broad_equity"},
    {"fund_code": "159941", "fund_name": "纳斯达克ETF(博时)",     "fund_type": "ETF",     "benchmark": "纳斯达克100", "region": "美国", "expense_ratio": 0.006,  "mgmt_fee": 0.005,  "custody_fee": 0.001,  "asset_class": "growth_equity"},
    {"fund_code": "040046", "fund_name": "华安标普500增强",       "fund_type": "增强指数", "benchmark": "标普500",     "region": "美国", "expense_ratio": 0.012,  "mgmt_fee": 0.010,  "custody_fee": 0.002,  "asset_class": "broad_equity"},
    {"fund_code": "006479", "fund_name": "易方达标普科技",        "fund_type": "被动指数", "benchmark": "标普科技",    "region": "美国", "expense_ratio": 0.012,  "mgmt_fee": 0.010,  "custody_fee": 0.002,  "asset_class": "sector_equity"},
    {"fund_code": "206005", "fund_name": "博时标普500ETF联接",    "fund_type": "ETF联接", "benchmark": "标普500",     "region": "美国", "expense_ratio": 0.0085, "mgmt_fee": 0.0075, "custody_fee": 0.001,  "asset_class": "broad_equity"},
    {"fund_code": "161130", "fund_name": "标普500指数LOF(富国)",  "fund_type": "LOF",     "benchmark": "标普500",     "region": "美国", "expense_ratio": 0.006,  "mgmt_fee": 0.005,  "custody_fee": 0.001,  "asset_class": "broad_equity"},
    {"fund_code": "002803", "fund_name": "摩根标普500指数",       "fund_type": "被动指数", "benchmark": "标普500",     "region": "美国", "expense_ratio": 0.007,  "mgmt_fee": 0.006,  "custody_fee": 0.001,  "asset_class": "broad_equity"},
    # ── 日本市场 ──────────────────────────────────────
    {"fund_code": "513880", "fund_name": "华夏野村日经225ETF",    "fund_type": "ETF",     "benchmark": "日经225",     "region": "日本", "expense_ratio": 0.006,  "mgmt_fee": 0.005,  "custody_fee": 0.001,  "asset_class": "broad_equity"},
    {"fund_code": "513000", "fund_name": "华安日本股票ETF",       "fund_type": "ETF",     "benchmark": "MSCI日本",    "region": "日本", "expense_ratio": 0.006,  "mgmt_fee": 0.005,  "custody_fee": 0.001,  "asset_class": "broad_equity"},
    {"fund_code": "164403", "fund_name": "工银日本股票LOF",       "fund_type": "LOF",     "benchmark": "MSCI日本",    "region": "日本", "expense_ratio": 0.018,  "mgmt_fee": 0.015,  "custody_fee": 0.003,  "asset_class": "broad_equity"},
    {"fund_code": "015691", "fund_name": "华泰柏瑞日经225ETF",    "fund_type": "ETF",     "benchmark": "日经225",     "region": "日本", "expense_ratio": 0.006,  "mgmt_fee": 0.005,  "custody_fee": 0.001,  "asset_class": "broad_equity"},
    {"fund_code": "050026", "fund_name": "博时日本ETF联接",       "fund_type": "ETF联接", "benchmark": "日经225",     "region": "日本", "expense_ratio": 0.006,  "mgmt_fee": 0.005,  "custody_fee": 0.001,  "asset_class": "broad_equity"},
    # ── 欧洲/德国市场 ─────────────────────────────────
    {"fund_code": "513030", "fund_name": "华安德国DAX ETF",      "fund_type": "ETF",     "benchmark": "DAX",         "region": "德国", "expense_ratio": 0.006,  "mgmt_fee": 0.005,  "custody_fee": 0.001,  "asset_class": "broad_equity"},
    {"fund_code": "160218", "fund_name": "博时德国DAX ETF联接",   "fund_type": "ETF联接", "benchmark": "DAX",         "region": "德国", "expense_ratio": 0.006,  "mgmt_fee": 0.005,  "custody_fee": 0.001,  "asset_class": "broad_equity"},
    {"fund_code": "164701", "fund_name": "招商欧洲精选LOF",       "fund_type": "LOF",     "benchmark": "MSCI欧洲",    "region": "欧洲", "expense_ratio": 0.018,  "mgmt_fee": 0.015,  "custody_fee": 0.003,  "asset_class": "broad_equity"},
    {"fund_code": "001548", "fund_name": "汇添富欧洲市场",        "fund_type": "主动QDII", "benchmark": "MSCI欧洲",    "region": "欧洲", "expense_ratio": 0.011,  "mgmt_fee": 0.009,  "custody_fee": 0.002,  "asset_class": "broad_equity"},
    {"fund_code": "003318", "fund_name": "易方达欧洲基金",        "fund_type": "被动指数", "benchmark": "MSCI欧洲",    "region": "欧洲", "expense_ratio": 0.0132, "mgmt_fee": 0.011,  "custody_fee": 0.0022, "asset_class": "broad_equity"},
    # ── 全球/亚洲 ─────────────────────────────────────
    {"fund_code": "270023", "fund_name": "广发全球精选",          "fund_type": "主动QDII", "benchmark": "MSCI全球",    "region": "全球", "expense_ratio": 0.0140, "mgmt_fee": 0.012,  "custody_fee": 0.002,  "asset_class": "broad_equity"},
    {"fund_code": "110022", "fund_name": "易方达亚洲精选",        "fund_type": "主动QDII", "benchmark": "MSCI亚洲",    "region": "亚洲", "expense_ratio": 0.0165, "mgmt_fee": 0.014,  "custody_fee": 0.0025, "asset_class": "broad_equity"},
    {"fund_code": "481010", "fund_name": "工银全球股票",          "fund_type": "主动QDII", "benchmark": "MSCI全球",    "region": "全球", "expense_ratio": 0.0165, "mgmt_fee": 0.014,  "custody_fee": 0.0025, "asset_class": "broad_equity"},
    {"fund_code": "485010", "fund_name": "工银全球精选",          "fund_type": "主动QDII", "benchmark": "MSCI全球",    "region": "全球", "expense_ratio": 0.0165, "mgmt_fee": 0.014,  "custody_fee": 0.0025, "asset_class": "broad_equity"},
    # ── 行业主题/商品/债券 ────────────────────────────
    {"fund_code": "164906", "fund_name": "华宝标普油气LOF",       "fund_type": "LOF",     "benchmark": "标普油气",    "region": "全球", "expense_ratio": 0.0072, "mgmt_fee": 0.006,  "custody_fee": 0.0012, "asset_class": "sector_equity"},
    {"fund_code": "000934", "fund_name": "汇添富全球互联网",      "fund_type": "主动QDII", "benchmark": "纳斯达克100", "region": "美国", "expense_ratio": 0.018,  "mgmt_fee": 0.015,  "custody_fee": 0.003,  "asset_class": "growth_equity"},
    {"fund_code": "519977", "fund_name": "长信全球债券",          "fund_type": "QDII债券", "benchmark": "全球债券",    "region": "全球", "expense_ratio": 0.009,  "mgmt_fee": 0.007,  "custody_fee": 0.002,  "asset_class": "bond"},
]

# 代码 → 真实费率 的快速映射（akshare 路径用它回填，避免清零）
EXPENSE_RATIO_BY_CODE = {f["fund_code"]: f["expense_ratio"] for f in CORE_QDII_FUNDS}
MGMT_FEE_BY_CODE      = {f["fund_code"]: f["mgmt_fee"]      for f in CORE_QDII_FUNDS if "mgmt_fee" in f}
CUSTODY_FEE_BY_CODE   = {f["fund_code"]: f["custody_fee"]   for f in CORE_QDII_FUNDS if "custody_fee" in f}

# 代码 → 资产类别 / 基准 / 地区
ASSET_CLASS_BY_CODE = {f["fund_code"]: f["asset_class"] for f in CORE_QDII_FUNDS}
BENCHMARK_BY_CODE   = {f["fund_code"]: f["benchmark"]   for f in CORE_QDII_FUNDS}
REGION_BY_CODE      = {f["fund_code"]: f["region"]      for f in CORE_QDII_FUNDS}


# ── 按名称客观推断基准/地区（供 fund_screener 对全市场 QDII 分类去重）──
# 顺序敏感：更具体的关键词在前（如“标普科技/标普油气”先于“标普500”）。
_BENCHMARK_RULES = [
    (["标普科技", "标普500科技", "标普信息科技"], "标普科技"),
    (["油气", "石油", "天然气", "标普油气"], "标普油气"),
    (["纳斯达克", "纳指", "NASDAQ"], "纳斯达克100"),
    (["标普500", "标普 500", "S&P500", "标普500"], "标普500"),
    (["日经", "日経", "225"], "日经225"),
    (["DAX", "德国"], "DAX"),
    (["恒生", "香港", "港股", "H股"], "恒生指数"),
    (["MSCI日本", "日本"], "MSCI日本"),
    (["欧洲", "MSCI欧洲", "欧股"], "MSCI欧洲"),
    (["亚洲", "亚太", "MSCI亚洲"], "MSCI亚洲"),
    (["印度"], "印度市场"),
    (["越南"], "越南市场"),
    (["全球", "海外", "MSCI全球", "世界"], "MSCI全球"),
    (["债"], "全球债券"),
    (["黄金", "金矿"], "黄金"),
]

_REGION_RULES = [
    (["纳斯达克", "纳指", "标普", "S&P", "美国", "美股", "NASDAQ"], "美国"),
    (["日经", "日本", "日経"], "日本"),
    (["DAX", "德国"], "德国"),
    (["恒生", "香港", "港股", "H股"], "香港"),
    (["欧洲", "欧股"], "欧洲"),
    (["印度"], "印度"),
    (["越南"], "越南"),
    (["亚洲", "亚太"], "亚洲"),
    (["全球", "海外", "世界"], "全球"),
]


def infer_benchmark(fund_name: str, benchmark: str = "") -> str:
    """按名称/已知基准客观推断跟踪基准；无法判断时返回名称本身（保证可去重）。"""
    text = f"{benchmark} {fund_name}"
    for kws, label in _BENCHMARK_RULES:
        if any(k in text for k in kws):
            return label
    return (benchmark or fund_name or "").strip()


def infer_region(fund_name: str, benchmark: str = "") -> str:
    text = f"{benchmark} {fund_name}"
    for kws, label in _REGION_RULES:
        if any(k in text for k in kws):
            return label
    return "全球"


def strategy_match_score(asset_class: str, composite_signal: str) -> float:
    """资产类别 × 综合信号 → 策略匹配分（scorer 与回测共用，确保口径一致）。"""
    if composite_signal == "重仓进取":
        return {"growth_equity": 9.0, "sector_equity": 8.0, "broad_equity": 7.5,
                "commodity": 6.0, "bond": 4.0}.get(asset_class, 6.0)
    elif composite_signal in ("标配稳健", "谨慎防守"):
        return {"broad_equity": 8.0, "growth_equity": 7.0, "sector_equity": 6.5,
                "commodity": 5.5, "bond": 6.0}.get(asset_class, 6.5)
    else:  # 减仓防守
        return {"bond": 8.0, "broad_equity": 7.0, "commodity": 6.0,
                "sector_equity": 5.0, "growth_equity": 4.5}.get(asset_class, 6.0)


def holdings_adjusted_strategy_score(
    asset_class: str,
    composite_signal: str,
    stock_ratio: float | None,
    bond_ratio: float | None,
    cash_ratio: float | None,
) -> float:
    """用真实持仓比例精修策略匹配分（仅在有 fund_holdings 数据时生效）。

    逻辑：
      - 无持仓数据 → 直接返回基于 asset_class 的 strategy_match_score（原行为）
      - 有持仓数据 → 评估实际股票/债券/现金比例与当前信号的匹配度，
        按 70% 资产类别基础分 + 30% 真实持仓适配分 混合

    这样设计的好处：
      - 同样标注为 broad_equity 的基金，若实际股票仓位只有 60%（含大量现金），
        在"重仓进取"信号下的得分会低于高股票仓位基金，评分更精准。
      - 不影响无持仓数据的基金（退回原行为），保证数据有无时评分口径一致。
    """
    base = strategy_match_score(asset_class, composite_signal)

    if stock_ratio is None and bond_ratio is None:
        return base  # 无持仓数据，保持原行为

    sr = float(stock_ratio or 0)
    br = float(bond_ratio or 0)
    cr = float(cash_ratio or 0)

    if composite_signal == "重仓进取":
        # 期望高股票仓位（>= 85% 满分 10，60% 约 7）
        holdings_fit = min(10.0, sr / 8.5)
    elif composite_signal == "减仓防守":
        # 期望安全资产（债券+现金）占比 >= 80%
        safe = br + cr
        holdings_fit = min(10.0, safe / 8.0)
    elif composite_signal == "谨慎防守":
        # 中等安全：股票 50-75% 最佳，过高或过低扣分
        if 50 <= sr <= 75:
            holdings_fit = 8.0
        elif sr > 85 or sr < 30:
            holdings_fit = 4.0
        else:
            holdings_fit = 6.5
    else:  # 标配稳健
        # 股票 60-85% 均可，略低或略高轻扣
        if 60 <= sr <= 85:
            holdings_fit = 8.5
        elif 45 <= sr < 60 or 85 < sr <= 95:
            holdings_fit = 7.0
        else:
            holdings_fit = 5.0

    return round(base * 0.70 + holdings_fit * 0.30, 2)


def classify_asset_class(fund_code: str = "", fund_type: str = "", fund_name: str = "",
                         benchmark: str = "") -> str:
    """优先用 universe 的精确归类；未知基金回退到基准/类型/名字推断（比纯名字关键词稳健）。"""
    code = str(fund_code)
    if code in ASSET_CLASS_BY_CODE:
        return ASSET_CLASS_BY_CODE[code]

    text = f"{fund_type} {fund_name} {benchmark}"
    if any(k in text for k in ["债", "bond", "Bond"]):
        return "bond"
    if any(k in text for k in ["油气", "原油", "黄金", "商品", "石油", "天然气"]):
        return "commodity"
    if any(k in text for k in ["纳斯达克", "科技", "互联网", "成长", "半导体", "芯片"]):
        return "growth_equity"
    if any(k in text for k in ["标普500", "MSCI", "日经", "DAX", "全球", "500", "宽基", "沪深"]):
        return "broad_equity"
    if any(k in text for k in ["医疗", "医药", "能源", "金融", "消费", "行业", "主题"]):
        return "sector_equity"
    return "broad_equity"  # 默认按宽基处理
