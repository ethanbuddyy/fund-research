# 基金投资私人幕僚系统

融合**巴菲特 · 格雷厄姆 · 博格 · 西格尔 · 彼得林奇**五位投资大师方法论的 QDII 基金投研系统。
自动采集宏观/市场/估值/基金数据，生成市场择时信号，对基金综合评分，并给出核心—卫星组合建议。
内置走向前回测引擎验证策略有效性。

> ⚠️ **免责声明**：本系统仅供研究与学习，所有输出不构成投资建议。投资有风险，决策需自负。

---

## 一、系统逻辑

数据从采集到建议的完整链路：

```
采集层 ─────────────► 分析层 ─────────────► 决策层
FRED 宏观             宏观周期判断           市场综合信号(择时)
World Bank/OECD 全球  全球区域宏观           基金综合评分
yfinance 市场行情     市场估值(真实CAPE)      核心-卫星组合建议
multpl 估值           市场情绪(VIX)          大师策略共识
天天基金 净值/持仓     基金绩效(夏普/回撤)
                     五大师策略
```

### 1. 市场综合信号（择时）

`src/recommender/signals.py` 把多个**去相关**的因子加权成 `composite_raw`(0–10)：

| 因子 | 权重 | 来源 | 是否独立于股价 |
|------|------|------|----------------|
| 趋势 | 30% | 标普500 vs 252日均线 | 否(价格) |
| 宏观周期 | 20% | 经济周期四阶段 + 美联储方向 | ✅ |
| 估值 | 20% | **真实 Shiller CAPE 分位** | ✅(真实盈利,非价格) |
| 逆向情绪 | 15% | VIX + 1月动量 | 否(价格/波动) |
| 信用利差 | 15% | FRED 高收益债 OAS | ✅ |

> 设计要点：估值改用真实 CAPE 后不再是股价的线性函数；并引入独立的信用利差因子，
> 把「纯标普价格/波动」驱动占比从早期的约 80% 降到约 45%。

信号阈值 → 仓位建议：

| `composite_raw` | 信号 | 核心 / 卫星 / 现金 |
|-----------------|------|--------------------|
| ≥ 7.0 | 重仓进取 | 70% / 25% / 5% |
| ≥ 5.0 | 标配稳健 | 60% / 30% / 10% |
| ≥ 3.0 | 谨慎防守 | 50% / 20% / 30% |
| < 3.0 | 减仓防守 | 35% / 15% / 50% |

### 2. 基金综合评分

`src/recommender/scorer.py` 对每只基金五维打分(合计百分制)：

| 维度 | 权重 | 说明 |
|------|------|------|
| 历史绩效 | 25% | 各期收益**年化后**对 20%/年 目标打分 |
| 风险调整 | 20% | 夏普 / 最大回撤 / 波动率 |
| 策略匹配 | 20% | 按**资产类别**与当前信号匹配 |
| 择时 | 20% | 当前市场综合信号分 |
| 成本 | 15% | 真实费率 |

### 3. 组合构建

`src/recommender/portfolio.py`：核心仓配宽基指数，卫星仓配行业/主动/主题，
按信号决定的核心/卫星/现金比例分配权重，并附大师提示与区域宏观强弱注记。

---

## 二、数据接口

所有数据源在不可用时**自动降级为模拟数据并明确标记**(见[数据真实性](#四数据真实性provenance))。

| 数据 | 来源 | 需要 Key | 采集器 |
|------|------|:--------:|--------|
| 美国宏观(GDP/CPI/PCE/利率/失业/信用利差/曲线) | **FRED API** | ✅ 免费 | `macro_collector.py` |
| 全球区域宏观(各国 GDP/通胀/失业) | **World Bank** | ❌ | `global_macro_collector.py` |
| 领先指标 CLI | **OECD** | ❌(尽力而为) | `global_macro_collector.py` |
| 市场行情(指数/VIX/商品/板块ETF) | **yfinance** | ❌ | `market_collector.py` |
| 市场估值(真实 Shiller CAPE / 标普PE) | **multpl.com** | ❌ | `valuation_collector.py` |
| QDII 基金池(规则筛选) | **天天基金 QDII排行** | ❌ | `fund_screener.py` |
| 基金真实净值 + 持仓 + 经理 | **天天基金 pingzhongdata** | ❌ | `eastmoney_collector.py` |
| 基金列表/净值(备选) | **akshare** | ❌ | `fund_collector.py` |
| 基金净值种子(一次性下载) | **天天基金 lsjz** | ❌ | `tools/download_seed_data.py` |

**FRED Key**：免费申请 https://fred.stlouisfed.org/docs/api/api_key.html (限速 120次/分，本系统每次采集仅约 10 次请求)。配置见下。

---

## 三、安装与使用

### 安装
```bash
pip install -r requirements.txt
cp config/settings.yaml.example config/settings.yaml   # 首次：复制配置模板
# 编辑 config/settings.yaml，填入 FRED Key（留空则宏观走模拟数据）
```

### 运行
```bash
python run.py                 # 完整流程：初始化 → 采集 → 生成信号
python run.py --fetch-only    # 仅采集与分析，不做后续
python run.py --skip-fetch    # 跳过采集（用已有数据）

python backtest.py            # 走向前回测（默认参数）
python backtest.py --top 8 --freq Q --cash 10   # 调参：前8只/季度调仓/现金上限10%

python tools/download_seed_data.py   # 一次性下载基金净值种子CSV（无 akshare 时备用）
python scheduler.py           # 常驻调度器，每天北京时间 08:30 自动更新
python scheduler.py --once    # 立即执行一次更新
```

### 配置要点（`config/settings.yaml`，已 gitignore，不入库）
- `fred_api_key`：FRED 密钥
- `fred_series`：FRED 序列(实际GDP用 `GDPC1`、通胀优先 `PCEPILFE`、曲线 `T10Y2Y`、信用 `BAMLH0A0HYM2`)
- `global_macro`：World Bank/OECD 区域与指标
- `market_indices` / `sector_etfs`：行情标的
- `fund_screener`：基金池筛选规则(成立年限/费率/去重/上限/排序)
- `scoring_weights` / `strategy_params`：评分权重与大师阈值

---

## 四、数据真实性（provenance）

每个采集器都会记录本次用的是真实数据还是模拟数据(`collection_meta` 表)，
信号带 `data_source` 字段，CLI 打印数据真实性横幅：

- ✅ **real** — 全部真实数据
- ⚠️ **partial** — 部分真实/近似(如估值回退到点位近似)
- ❌ **mock** — 含模拟数据，仅供界面演示，**不可用于实际决策**

回测结果还会明确披露**幸存者偏差**(基金池为当前在运作的基金，未含已清盘者，收益为乐观上界)。

---

## 五、目录结构

```
fund-research/
├── run.py                      # 一键入口：采集 + 信号
├── scheduler.py                # 每日定时调度（北京时间 08:30）
├── backtest.py                 # 回测分析入口
├── config/settings.yaml(.example)  # 配置（API Key + 结构性配置）
├── src/
│   ├── collectors/             # 采集层
│   │   ├── macro_collector.py        # FRED 美国宏观
│   │   ├── global_macro_collector.py # World Bank + OECD 全球宏观
│   │   ├── market_collector.py       # yfinance 市场行情
│   │   ├── valuation_collector.py    # multpl 真实 CAPE/PE
│   │   ├── fund_screener.py          # 规则筛选 QDII 基金池
│   │   ├── eastmoney_collector.py    # 天天基金 pingzhongdata 净值+持仓
│   │   ├── fund_collector.py         # akshare/CSV/模拟（备选）
│   │   └── news_collector.py         # VIX 推导市场情绪
│   ├── analyzers/              # 分析层
│   │   ├── macro_analyzer.py         # 经济周期四阶段
│   │   ├── global_macro_analyzer.py  # 各区域宏观周期
│   │   ├── valuation.py              # 估值指标(真实优先)
│   │   ├── fund_analyzer.py          # 绩效(夏普/回撤/波动)
│   │   └── masters/                  # 五大师策略
│   ├── recommender/            # 决策层
│   │   ├── signals.py                # 市场综合信号
│   │   ├── scorer.py                 # 基金综合评分
│   │   └── portfolio.py              # 组合构建
│   ├── backtester/engine.py    # 走向前回测引擎（无前视偏差）
│   └── utils/
│       ├── config.py / database.py / provenance.py
│       └── fund_universe.py          # 基金标的库 + 分类/去重规则
└── tools/download_seed_data.py # 净值种子下载
```

### 数据库表（SQLite，`data/fund_research.db`）
`macro_data` `global_macro` `market_data` `valuation_data` `fund_list` `fund_nav_history`
`fund_holdings` `fund_performance` `fund_scores` `market_signals` `collection_meta`

---

## 六、已实现的功能

- ✅ 多源数据采集，全部带**失败降级 + 真实性标记**
- ✅ 美国宏观(实际GDP/核心PCE/收益率曲线/信用利差) + 全球区域宏观(World Bank/OECD)
- ✅ **真实 Shiller CAPE/PE** 估值(非股价线性近似)，历史分位用真实序列
- ✅ 经济周期四阶段判断 + 美联储方向
- ✅ 五大师策略(格雷厄姆/巴菲特/博格/西格尔/林奇)共识
- ✅ 去相关的市场综合信号(5因子) → 仓位建议
- ✅ 基金五维综合评分(年化口径统一) + 核心-卫星组合
- ✅ **规则驱动的基金池筛选**(成立年限/费率/份额合并/按指数去重)
- ✅ 天天基金 pingzhongdata **真实净值全历史 + 持仓 + 经理**
- ✅ 走向前回测引擎(无前视偏差，披露幸存者偏差)
- ✅ 每日定时调度(北京时区自适应)

---

## 七、待进一步打磨的方向

| 方向 | 说明 |
|------|------|
| **基金池：宽基保底** | 当前按收益排序偏向高收益主动基金，可加「每地区至少保留 1 只宽基指数」规则 |
| **基金池：规模过滤** | `min_aum_yi` 已占位，需用 pingzhongdata 规模历史富集后启用 AUM 硬过滤 |
| **持仓接入打分** | `fund_holdings` 已落库真实行业暴露，可让策略匹配/卫星筛选用真实持仓(需处理回测口径一致) |
| **巴菲特指标真实化** | 当前仍为点位近似(已标记 estimated)，可用 FRED Wilshire/GDP 计算真实值 |
| **真实新闻情绪** | `news_collector` 现为 VIX 推导，可接 Finnhub 等真实新闻情绪 |
| **回测幸存者偏差** | 纳入已清盘基金需历史成分数据；当前以披露为主 |
| **全球宏观入信号** | 现为上下文/组合注记，若并入量化信号需历史全球宏观保证回测口径一致 |
| **基金元数据校对** | 核心库个别基金名称/分类可能过时(如 519977 实为可转债债券) |

---

## 八、技术备注

- **无前视偏差回测**：每个调仓日仅用截至该日的数据快照；真实 CAPE 按日期 as-of 引用。
- **live 与回测同口径**：信号权重、策略匹配、年化口径在实时与回测中保持一致。
- **时区**：调度器把北京时间 08:30 自动换算为系统本地时区。
- **依赖**：pandas / numpy / yfinance / akshare / fredapi / requests / PyYAML / schedule / scipy。
