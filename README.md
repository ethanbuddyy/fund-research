<div align="center">

# 基金投资私人幕僚系统

**QDII 基金量化投研平台** · 自动采集 → 信号生成 → 评分组合 → Markdown 投研报告

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://python.org)
[![Data](https://img.shields.io/badge/数据源-FRED%20%7C%20multpl%20%7C%20yfinance%20%7C%20天天基金-green)](#二数据接口)
[![AI](https://img.shields.io/badge/AI%20增强-Claude%20Phase1%2FPhase2-orange?logo=anthropic)](https://anthropic.com)
[![MCP](https://img.shields.io/badge/MCP-4%20服务器-purple)](#三mcp-决策分析扩展)

> ⚠️ **免责声明**：本系统仅供研究与学习，所有输出不构成投资建议。投资有风险，决策需自负。

</div>

---

## 目录

- [系统架构](#一系统架构)
- [数据接口](#二数据接口)
- [MCP 扩展](#三mcp-决策分析扩展)
- [安装与使用](#四安装与使用)
- [数据真实性](#五数据真实性provenance)
- [目录结构](#六目录结构)
- [已实现功能](#七已实现功能)
- [待打磨方向](#八待进一步打磨的方向)
- [技术备注](#九技术备注)

---

## 一、系统架构

数据从采集到报告的完整四层链路：

```
┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐
│    采集层        │   │    分析层        │   │    决策层        │   │    报告层        │
├─────────────────┤   ├─────────────────┤   ├─────────────────┤   ├─────────────────┤
│ FRED 宏观        │──▶│ 宏观周期四阶段   │──▶│ 市场综合信号     │──▶│ Markdown 投研报告│
│ World Bank/OECD  │   │ 全球区域宏观     │   │ 基金综合评分     │   │ · 结论+触发条件  │
│ yfinance 行情    │   │ 估值(真实CAPE)   │   │ 核心-卫星组合    │   │ · 五因子得分表   │
│ multpl 估值      │   │ 情绪(VIX)       │   │ 市场叙事         │   │ · 推荐基金明细   │
│ 天天基金 净值/仓  │   │ 基金绩效         │   │ AI 增强(可选)    │   │ · 行动计划+回测  │
└─────────────────┘   └─────────────────┘   └─────────────────┘   └─────────────────┘
```

### 1. 市场综合信号（择时）

`src/recommender/signals.py` 将 6 个**去相关因子**加权合成 `composite_raw`（0–10 分）：

| 因子 | 权重 | 数据来源 | 独立于股价 |
|:-----|:----:|:--------|:----------:|
| 趋势 | 27% | 标普500 vs 252日均线 | — |
| 宏观周期 | 18% | 经济周期四阶段 + 美联储方向 | ✅ |
| 估值 | 18% | **真实 Shiller CAPE 分位** | ✅ |
| 逆向情绪 | 13.5% | VIX + 1月动量 | — |
| 信用利差 | 13.5% | FRED 高收益债 OAS | ✅ |
| 全球宏观 | 10% | World Bank GDP/通胀 + OECD CLI | ✅ |

> 设计要点：估值用真实 CAPE 历史分位而非股价线性近似；引入信用利差与全球宏观两个独立因子，将"纯标普价格/波动"驱动占比从早期约 80% 降至约 40%。

信号档位 → 仓位建议：

| 综合得分 | 信号 | 核心仓 | 卫星仓 | 现金 |
|:--------:|:----:|:------:|:------:|:----:|
| ≥ 7.0 | 🟢 重仓进取 | 70% | 25% | 5% |
| ≥ 5.0 | 🔵 标配稳健 | 60% | 30% | 10% |
| ≥ 3.0 | 🟡 谨慎防守 | 50% | 20% | 30% |
| < 3.0 | 🔴 减仓防守 | 35% | 15% | 50% |

### 2. 基金综合评分

`src/recommender/scorer.py` 五维百分制打分，绩效与风险均在**同类别内**横向比较：

| 维度 | 权重 | 说明 |
|:-----|:----:|:-----|
| 历史绩效 | 30% | 类别内百分位排名（宽基 / 成长 / 行业 / 债券各自对标） |
| 风险调整 | 25% | 夏普 / 回撤 / 波动率的类别内百分位（0.4 / 0.35 / 0.25 加权） |
| 策略匹配 | 20% | 资产类别与当前市场信号的匹配度（含真实持仓精修） |
| 成本 | 15% | 管理费率（越低越好） |
| 一致性 | 10% | 跨期收益稳定性：正收益占比 + 低离散度 |

> 市场择时信号只影响仓位比例，不参与单基金排名，消除双重计算。
> 评分核心纯函数提取至 `src/domain/scoring.py`，生产路径与回测路径共用同一实现。

### 3. 组合构建

`src/recommender/portfolio.py` 按信号仓位比例分配权重：核心仓配宽基指数，卫星仓配行业/主动/主题，并附区域宏观强弱注记。

**换仓门槛**：新候选基金须比当前持仓高出 `score_threshold`（默认 10 分）才触发替换，防止因细微分差频繁调仓（QDII 来回成本可达 0.5–1.5%）。

### 4. 投研报告（10 章节）

`src/reports/report_builder.py` 在每次 `python run.py` 后生成 `reports/YYYY-MM-DD_fund_research_report.md`：

| # | 章节 | 核心内容 |
|:-:|:-----|:--------|
| 1 | 首页结论 | 综合信号、建议仓位、3 条关键结论（含数据引用）、可执行触发条件 |
| 2 | 数据可信度 | provenance 明细表、mock 警告、过期提示 |
| 3 | 市场主线 | 主要矛盾、六因子得分表（权重 + 贡献）、市场叙事 |
| 4 | 资产配置 | 核心/卫星/现金、换仓 diff（快照对比）、情景分析 |
| 5 | 推荐基金 | 全量维度 + 推荐理由 + 主要风险（AI Phase 2 填充） |
| 6 | 备选基金 | top\_picks 中未入选的前 5 只，含未入选原因 |
| 7 | 组合风险 | 区域暴露、费率、QDII 特有风险清单 |
| 8 | 行动计划 | 可执行操作条目，含触发条件和操作幅度 |
| 9 | 回测验证 | 四基准对比、因子归因、年度拆解、幸存者偏差披露 |
| 10 | 附录 | 数据源、评分权重、信号阈值、原始指标快照 |

### 5. 市场叙事

`src/analyzers/narrative.py` 基于量化数据生成可读性文字观察，覆盖四个维度：估值水位（CAPE / PE / ERP / 巴菲特指标）、市场情绪（VIX）、基金成本格局、板块趋势（各行业 ETF 近一月涨跌）。叙事层仅供理解参考，不参与量化评分与买卖决策。

---

## 二、数据接口

所有数据源不可用时**自动降级并明确标记**（详见[数据真实性](#五数据真实性provenance)）。

| 数据类型 | 来源 | 需要 Key | 采集器 |
|:--------|:-----|:--------:|:-------|
| 美国宏观（GDP / CPI / PCE / 利率 / 失业 / 信用利差 / 股权总市值） | **FRED API** | ✅ 免费 | `macro_collector.py` |
| 全球区域宏观（各国 GDP / 通胀 / 失业） | **World Bank** | ❌ | `global_macro_collector.py` |
| 领先指标 CLI | **OECD** | ❌（尽力而为） | `global_macro_collector.py` |
| 市场行情（指数 / VIX / 商品 / 板块 ETF） | **yfinance** | ❌ | `market_collector.py` |
| 市场估值（真实 Shiller CAPE + 标普 PE） | **multpl.com**（主）→ **Shiller 官方 XLS**（备）→ yfinance（兜底） | ❌ | `valuation_collector.py` |
| QDII 基金池（规则筛选） | **天天基金 QDII 排行** | ❌ | `fund_screener.py` |
| 基金净值 + 持仓 + 经理 | **天天基金 pingzhongdata** | ❌ | `eastmoney_collector.py` |
| 基金列表 / 净值（备选） | **akshare** | ❌ | `fund_collector.py` |
| 基金净值种子（一次性） | **天天基金 lsjz** | ❌ | `tools/download_seed_data.py` |

### FRED API Key 配置

免费申请：<https://fred.stlouisfed.org/docs/api/api_key.html>（限速 120 次/分，本系统每次采集仅约 10 次请求）

```bash
# 方式一：环境变量（推荐，优先级更高）
export FRED_API_KEY=your_key_here

# 方式二：配置文件
# 编辑 config/settings.yaml → fred_api_key 字段（留空则降级为模拟数据）
```

> **FRED 序列说明**：巴菲特指标 = `NCBEILQ027S`（股权总市值，**百万美元**）÷ 1000 ÷ `GDP`（名义 GDP，十亿美元，SAAR）；任一序列不可用时退回标普 500 点位近似并标注 `estimated`。

---

## 三、MCP 决策分析扩展

本项目通过 `.mcp.json` 为 **Claude Code** 提供四个 MCP 服务器，增强对话式投资决策能力：

| 服务器 | 工具数 | 用途 |
|:-------|:------:|:-----|
| `sequential-thinking` | 1 | Anthropic 官方：将复杂决策拆解为可审计的多步思维链 |
| `yfinance-market` | 30 | 美股实时行情、财务报表、分析师评级、期权链、市场新闻 |
| `technical-analysis` | 3 | 项目原生：RSI / MACD / 布林带 / 均线，多标的技术指标横向对比 |
| `stockreport` | — | A 股 / 港股 / 美股 K 线 / 财务 / 宏观 / 分红（Baostock + AkShare） |

```bash
# 安装 yfinance-market-mcp（PyPI）
pip install yfinance-market-mcp "mcp[cli]"

# 安装 stockreport-mcp（外部仓库，无需 API Key）
bash tools/setup_mcp.sh
```

安装后在 Claude Code 中打开项目目录，接受提示即可使用所有 MCP 工具。

---

## 四、安装与使用

### 安装

```bash
pip install -r requirements.txt
cp config/settings.yaml.example config/settings.yaml
# 编辑 config/settings.yaml，填入 FRED Key（留空则宏观数据走模拟）
```

### 运行

```bash
# 完整流程：数据采集 → 信号 → 评分 → 组合 → 投研报告
python run.py

# 含回测（结果注入报告第九章）
python run.py --backtest

# 每日定时调度（北京时间 08:30）
python scheduler.py
python scheduler.py --once      # 立即执行一次

# 单独回测
python backtest.py                        # 默认参数（含幸存者偏差修正对照组）
python backtest.py --top 8 --freq Q --cash 10
python backtest.py --attribution          # 因子归因分析（约 10–15 分钟）
python backtest.py --no-correction        # 关闭幸存者偏差修正

# 工具
python tools/download_seed_data.py        # 一次性下载基金净值种子 CSV
```

运行后 CLI 末尾打印报告路径：

```
[报告] 投研报告已生成：reports/2026-06-02_fund_research_report.md
```

### 配置项说明（`config/settings.yaml`，已 gitignore）

| 配置键 | 说明 |
|:-------|:-----|
| `fred_api_key` | FRED 密钥（或用环境变量 `FRED_API_KEY`） |
| `fred_series` | FRED 序列 ID（`GDPC1` / `PCEPILFE` / `T10Y2Y` / `BAMLH0A0HYM2` 等） |
| `global_macro` | World Bank / OECD 区域与指标配置 |
| `market_indices` / `sector_etfs` | 行情采集标的 |
| `fund_screener` | 基金池筛选规则（成立年限 / 费率 / 份额合并 / 指数去重 / 规模下限） |
| `scoring_weights` | 五维评分权重（绩效 / 风险 / 策略 / 成本 / 一致性） |
| `strategy_params` | 分析阈值（`valuation_thresholds` / `sentiment_thresholds` / `cost_filter`） |
| `rebalancing.score_threshold` | 换仓最小分差门槛（默认 10 分） |
| `user_profile` | 个人化参数（风险偏好 / 投资期限 / 仓位上下界） |
| `risk_management.stop_loss_pct` | 组合级回撤止损阈值 |
| `ai_analysis.enabled` | 是否开启 Claude AI 两阶段增强分析（默认 `false`） |

---

## 五、数据真实性（provenance）

每个采集器记录本次数据来源，CLI 打印横幅，报告第二章附明细表：

| 标记 | 含义 |
|:----:|:-----|
| ✅ **real** | 全部真实数据 |
| ⚠️ **partial** | 部分真实 / 近似（如 CAPE 退回点位估算） |
| ❌ **mock** | 含模拟数据，**仅供界面演示，不可用于实际决策** |

**过期检测**：`provenance.check_staleness()` 超期时在 CLI 和报告中追加警告（宏观 ≤ 7 天，行情 ≤ 3 天，基金/估值 ≤ 7 天）。

**幸存者偏差**：回测结果明确披露基金池为当前在运作的基金，未含已清盘者，收益为乐观上界。

---

## 六、目录结构

```
fund-research/
├── run.py                          # 一键入口：采集 → 信号 → 评分 → 组合 → 报告
├── scheduler.py                    # 每日定时调度（北京时间 08:30）
├── backtest.py                     # 回测分析入口
├── config/
│   └── settings.yaml(.example)    # 配置（API Key + 结构性参数，已 gitignore）
├── reports/                        # 自动生成的 Markdown 投研报告（按日期命名）
├── src/
│   ├── application/
│   │   └── update_pipeline.py     # 统一更新编排（run / scheduler 共用）
│   ├── domain/
│   │   └── scoring.py             # 评分纯函数（生产与回测共用同一实现）
│   ├── reports/
│   │   └── report_builder.py      # Markdown 投研报告生成器（10 章节）
│   ├── collectors/                 # 采集层
│   │   ├── macro_collector.py     # FRED 美国宏观
│   │   ├── global_macro_collector.py  # World Bank + OECD 全球宏观
│   │   ├── market_collector.py    # yfinance 市场行情
│   │   ├── valuation_collector.py # multpl → Shiller XLS → yfinance（三级冗余）
│   │   ├── fund_screener.py       # 规则筛选 QDII 基金池
│   │   ├── eastmoney_collector.py # 天天基金 pingzhongdata 净值 + 持仓
│   │   ├── fund_collector.py      # akshare / CSV / 模拟（备选）
│   │   └── news_collector.py      # 新闻情绪（AV / Finnhub，含 fallback）
│   ├── analyzers/                  # 分析层
│   │   ├── macro_analyzer.py      # 经济周期四阶段
│   │   ├── global_macro_analyzer.py  # 各区域宏观周期
│   │   ├── valuation.py           # 估值指标（真实数据优先）
│   │   ├── fund_analyzer.py       # 绩效（夏普 / 回撤 / 波动）
│   │   └── narrative.py           # 市场文字叙事（不参与评分）
│   ├── recommender/                # 决策层
│   │   ├── signals.py             # 六因子市场综合信号
│   │   ├── scorer.py              # 基金五维综合评分
│   │   └── portfolio.py           # 组合构建 + AI Phase 2 注入
│   ├── ai/                         # AI 增强层（配置开关控制）
│   │   ├── phase1_market_analyzer.py   # Phase 1：市场解析
│   │   ├── phase2_portfolio_advisor.py # Phase 2：投资决策
│   │   ├── schemas.py             # Tool use JSON Schema
│   │   └── backend.py / client.py / cache_strategy.py
│   ├── backtester/
│   │   └── engine.py              # 走向前回测引擎（无前视偏差）
│   └── utils/
│       ├── config.py / database.py / provenance.py
│       ├── portfolio_tracker.py   # 持仓追踪 + 回撤止损
│       └── fund_universe.py       # 基金标的库 + 分类 / 去重规则
├── tools/
│   ├── download_seed_data.py      # 净值种子下载
│   ├── mcp_technical_analysis.py  # MCP 技术分析服务器
│   └── setup_mcp.sh               # MCP 扩展依赖安装
└── .mcp.json                       # Claude Code MCP 服务器配置
```

**SQLite 数据库表**（`data/fund_research.db`）：

`macro_data` · `global_macro` · `market_data` · `valuation_data` · `fund_list` · `fund_nav_history` · `fund_holdings` · `fund_performance` · `fund_scores` · `market_signals` · `collection_meta`

---

## 七、已实现功能

<details>
<summary><b>数据采集与真实性</b></summary>

- ✅ 多源采集，全部带**失败降级 + 真实性标记 + 过期检测**（real / partial / mock 三级）
- ✅ 美国宏观（实际 GDP / 名义 GDP / 核心 PCE / 收益率曲线 / 信用利差）+ 全球区域宏观（World Bank / OECD）
- ✅ **估值数据三级冗余**：multpl.com → Shiller 官方 XLS（Yale，1871–至今）→ yfinance PE；修复 multpl 正则（HTML 实体 `&#x2002;` 导致解析失败）
- ✅ **真实 Shiller CAPE / PE**（非股价线性近似），历史分位用真实 1865 个月序列计算
- ✅ **巴菲特指标单位修正**：NCBEILQ027S 为百万美元，补充 ÷1000 换算（修正前偏高 1000 倍）
- ✅ 天天基金 pingzhongdata **真实净值全历史 + 持仓 + 经理**
- ✅ **FRED Key 支持环境变量**：`FRED_API_KEY` 优先于配置文件，便于 CI / 容器部署

</details>

<details>
<summary><b>信号与评分</b></summary>

- ✅ **六因子去相关市场综合信号**：趋势 / 宏观周期 / 估值 / 逆向情绪 / 信用利差 / 全球宏观 → 仓位建议
- ✅ **全球宏观并入量化信号**（第 6 因子）：按 QDII 资产规模权重加权（美国 40% / 全球 20% / …）
- ✅ **类别相对化**基金五维评分（绩效 / 风险在同类中横向比较，消除跨类偏差）
- ✅ **评分纯函数统一**：`src/domain/scoring.py` 消除生产与回测双实现，修复 NaN 排名 bug
- ✅ **持仓接入打分**：用真实股票 / 债券 / 现金仓位精修策略匹配分（70% 类别基础 + 30% 持仓适配）
- ✅ **规则驱动的基金池筛选**（成立年限 / 费率 / 份额合并 / 按指数去重 / 规模下限）
- ✅ **基金池宽基保底**：去重截断后强制补入各地区最优宽基，防止全被高收益基金挤出

</details>

<details>
<summary><b>组合与风控</b></summary>

- ✅ **个人化输入**：`user_profile` 在信号档位基础上叠加偏移（保守 / 激进 / 投资期限），调整明细写入报告
- ✅ **回撤止损机制**：`portfolio_tracker.py` 追踪假设持仓高水位；超阈值时强制降至"减仓防守"（核心 35% / 卫星 15% / 现金 50%）
- ✅ 换仓成本门槛（默认 10 分），防止因细微分差频繁调仓

</details>

<details>
<summary><b>回测引擎</b></summary>

- ✅ **走向前回测**（无前视偏差）：每个调仓日仅用截至该日的数据快照，真实 CAPE 按日期 as-of 引用
- ✅ **四基准对比**：策略 vs 等权买持 vs 60/40 vs 纯现金；60/40 基准的现金仓位按 `RF_ANNUAL/12` 计息
- ✅ **因子归因**（`--attribution`）：逐因子屏蔽回测，量化各因子边际贡献，结果注入报告第九章
- ✅ **幸存者偏差修正**：每个调仓日仅允许成立日期 ≤ t0 的基金参与对照组，量化偏差溢价
- ✅ **回测嵌入报告**：`python run.py --backtest` 触发，结果直接注入当期报告第九章

</details>

<details>
<summary><b>报告与调度</b></summary>

- ✅ **10 章节 Markdown 投研报告**：首页结论 + 证据链 + 行动计划 + 数据可信度披露
- ✅ 每日定时调度（北京时区自适应，**信号变化自动通知**，档位变化时 WARNING 级别日志）
- ✅ **AI 两阶段增强**（Claude Phase 1 市场解析 + Phase 2 投资决策，配置开关，失败自动 fallback）
- ✅ **全链路鲁棒性**：NULL/NaN 防御统一补充，静默 `pass` 改为有诊断意义的 `[WARN]` 日志
- ✅ 报告生成失败不中断数据采集与信号生成主流程

</details>

---

## 八、待进一步打磨的方向

| 方向 | 现状 | 说明 |
|:-----|:----:|:-----|
| 回测幸存者偏差（完整修复） | 部分完成 | 已修正"尚未成立"偏差；已清盘基金因缺乏历史成分数据，仍以披露为主 |

---

## 九、技术备注

| 主题 | 说明 |
|:-----|:-----|
| 无前视偏差 | 每个调仓日仅用截至该日的数据快照；真实 CAPE 按日期 as-of 引用 |
| 四基准对比 | 同时输出"等权买入持有"基准，可直观区分信号择时与基金选择各自的贡献 |
| live 与回测同口径 | 信号权重、评分、策略匹配共用 `src/domain/scoring.py` 同一纯函数，消除双实现漂移 |
| 报告容错 | 报告生成失败仅打印 warning，不中断数据采集与信号生成主流程 |
| 时区 | 调度器把北京时间 08:30 自动换算为系统本地时区 |
| 依赖 | `pandas` / `numpy` / `yfinance` / `akshare` / `fredapi` / `requests` / `PyYAML` / `schedule` / `scipy` · 可选：`mcp[cli]` / `fastmcp` / `baostock`（MCP 扩展） |
