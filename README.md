# 基金投资私人幕僚系统

QDII 基金投研系统。自动采集宏观/市场/估值/基金数据，生成市场择时信号，对基金综合评分，
给出核心—卫星组合建议，并输出一份结构完整的 Markdown 投研报告。
内置走向前回测引擎验证策略有效性。

> ⚠️ **免责声明**：本系统仅供研究与学习，所有输出不构成投资建议。投资有风险，决策需自负。

---

## 一、系统逻辑

数据从采集到报告的完整链路：

```
采集层 ─────────────► 分析层 ─────────────► 决策层 ─────────────► 报告层
FRED 宏观             宏观周期判断           市场综合信号(择时)     Markdown 投研报告
World Bank/OECD 全球  全球区域宏观           基金综合评分           · 首页结论+触发条件
yfinance 市场行情     市场估值(真实CAPE)      核心-卫星组合建议      · 五因子得分表
multpl 估值           市场情绪(VIX)          市场叙事(文字观察)     · 推荐基金明细
天天基金 净值/持仓     基金绩效(夏普/回撤)    AI 增强分析(可选)      · 行动计划
                                                                    · 回测验证
                                                                    · 数据可信度披露
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

`src/recommender/scorer.py` 三轮流水线对每只基金五维打分（合计百分制）：

| 维度 | 权重 | 说明 |
|------|------|------|
| 历史绩效 | 30% | **类别内百分位排名**（宽基/成长/行业/债券各自参照同类） |
| 风险调整 | 25% | 夏普/回撤/波动率的**类别内百分位**（0.4/0.35/0.25 加权） |
| 策略匹配 | 20% | 按资产类别与当前市场信号匹配 |
| 成本 | 15% | 费率（越低越好） |
| 一致性 | 10% | 跨期收益稳定性：正收益占比 + 低离散度 |

> **设计要点**：绩效和风险均在资产类别内横向比较，避免债券基金因绝对收益低被错判为劣质；
> 市场择时信号仅影响仓位比例，不参与单基金排名（消除双重计算）。
> 评分核心纯函数（`category_percentile` / `consistency_score` / `cost_score`）
> 提取至 `src/domain/scoring.py`，生产路径与回测路径共用同一实现，保证口径一致。

### 3. 组合构建

`src/recommender/portfolio.py`：核心仓配宽基指数，卫星仓配行业/主动/主题，
按信号决定的核心/卫星/现金比例分配权重，并附区域宏观强弱注记。

**换仓门槛**：新候选基金须比当前持仓高出 `score_threshold`（默认 10 分）才触发替换建议，
防止因细微分差导致频繁调仓（QDII 来回成本可达 0.5–1.5%）。

### 4. 投研报告生成

`src/reports/report_builder.py`：每次 `python run.py` 结束后自动生成
`reports/YYYY-MM-DD_fund_research_report.md`，包含十个章节：

| 章节 | 内容 |
|------|------|
| 首页结论 | 综合信号、建议仓位、3条关键结论（数据引用）、可执行触发条件 |
| 数据可信度 | provenance 明细表、mock 警告、过期提示 |
| 市场主线 | 主要矛盾、五因子得分表（权重+贡献）、市场叙事 |
| 资产配置 | 核心/卫星/现金、换仓 diff（快照对比）、情景分析 |
| 推荐基金表 | 全量维度 + 推荐理由 + 主要风险（AI Phase 2 填充） |
| 备选基金 | top_picks 中未入选的前5只，含未入选原因 |
| 组合暴露与风险 | 区域暴露、费率、QDII 特有风险清单 |
| 行动计划 | 可执行操作条目，含触发条件和操作幅度（AI Phase 2 优先） |
| 回测验证 | 四基准对比、信号有效性、年度拆解、幸存者偏差披露 |
| 附录 | 数据源、评分权重、信号阈值、原始指标快照 |

报告生成失败不中断数据采集主流程。

### 5. 市场叙事

`src/analyzers/narrative.py`：基于量化数据生成可读性文字观察，覆盖四个维度：
估值水位（CAPE / PE / ERP / 总市值-GDP 比）、市场情绪（VIX）、
基金成本格局（低费率指数基金可用性）、板块趋势（各行业 ETF 近一月涨跌）。
叙事层仅供理解参考，不参与量化评分与买卖决策。

---

## 二、数据接口

所有数据源在不可用时**自动降级为模拟数据并明确标记**(见[数据真实性](#五数据真实性provenance))。

| 数据 | 来源 | 需要 Key | 采集器 |
|------|------|:--------:|--------|
| 美国宏观(GDP/CPI/PCE/利率/失业/信用利差/曲线/**股权总市值/名义GDP**) | **FRED API** | ✅ 免费 | `macro_collector.py` |
| 全球区域宏观(各国 GDP/通胀/失业) | **World Bank** | ❌ | `global_macro_collector.py` |
| 领先指标 CLI | **OECD** | ❌(尽力而为) | `global_macro_collector.py` |
| 市场行情(指数/VIX/商品/板块ETF) | **yfinance** | ❌ | `market_collector.py` |
| 市场估值(真实 Shiller CAPE / 标普PE) | **multpl.com** | ❌ | `valuation_collector.py` |
| QDII 基金池(规则筛选) | **天天基金 QDII排行** | ❌ | `fund_screener.py` |
| 基金真实净值 + 持仓 + 经理 | **天天基金 pingzhongdata** | ❌ | `eastmoney_collector.py` |
| 基金列表/净值(备选) | **akshare** | ❌ | `fund_collector.py` |
| 基金净值种子(一次性下载) | **天天基金 lsjz** | ❌ | `tools/download_seed_data.py` |

**FRED Key**：免费申请 https://fred.stlouisfed.org/docs/api/api_key.html (限速 120次/分，本系统每次采集仅约 10 次请求)。配置见下。

> **FRED Key 配置方式（二选一）**：
> - 环境变量 `FRED_API_KEY`（推荐，优先级更高）：`export FRED_API_KEY=xxxx`
> - 配置文件 `config/settings.yaml` 的 `fred_api_key` 字段（留空则降级为模拟数据）

> **FRED 序列说明**：巴菲特指标现使用真实 FRED 数据计算：`NCBEILQ027S`（美国非金融企业股权总市值，十亿美元）/ `GDP`（名义 GDP，SAAR，十亿美元）；两者均不可用时退回标普500点位近似并标注 `estimated`。

---

## 三、MCP 决策分析扩展（Claude Code）

本项目通过 `.mcp.json` 为 Claude Code 提供四个 MCP 服务器，增强对话式投资决策能力：

| 服务器 | 工具数 | 用途 |
|--------|--------|------|
| `sequential-thinking` | 1 | Anthropic 官方：将复杂决策拆解为可审计的多步思维链 |
| `yfinance-market` | 30 | 美股实时行情、财务报表、分析师评级、期权链、市场新闻 |
| `technical-analysis` | 3 | 项目原生：RSI/MACD/布林带/均线，多标的技术指标横向对比 |
| `stockreport` | — | A股/港股/美股 K线/财务/宏观/分红（Baostock + AkShare） |

### 安装 MCP 扩展
```bash
# 安装 yfinance-market-mcp（PyPI）
pip install yfinance-market-mcp "mcp[cli]"

# 安装 stockreport-mcp（外部仓库，无需 API Key）
bash tools/setup_mcp.sh
```

安装后在 Claude Code 中打开项目，接受提示即可使用所有 MCP 工具。

---

## 四、安装与使用

### 安装
```bash
pip install -r requirements.txt
cp config/settings.yaml.example config/settings.yaml   # 首次：复制配置模板
# 编辑 config/settings.yaml，填入 FRED Key（留空则宏观走模拟数据）
```

### 运行
```bash
# 完整流程：数据采集 → 信号 → 评分 → 组合 → 投研报告
python run.py

# 每日定时调度（北京时间 08:30，同样生成报告）
python scheduler.py
python scheduler.py --once    # 立即执行一次

# 单独回测（含幸存者偏差修正对照组）
python backtest.py                                    # 默认参数（自动运行修正对照组）
python backtest.py --top 8 --freq Q --cash 10         # 调参
python backtest.py --attribution                      # 因子归因分析（约10-15分钟）
python backtest.py --no-correction                    # 关闭幸存者偏差修正对照组

# 工具
python tools/download_seed_data.py   # 一次性下载基金净值种子 CSV
```

运行 `python run.py` 后，CLI 末尾会打印报告路径：
```
[报告] 投研报告已生成：reports/2026-06-02_fund_research_report.md
```

### 配置要点（`config/settings.yaml`，已 gitignore，不入库）
- `fred_api_key`：FRED 密钥
- `fred_series`：FRED 序列(实际GDP用 `GDPC1`、通胀优先 `PCEPILFE`、曲线 `T10Y2Y`、信用 `BAMLH0A0HYM2`)
- `global_macro`：World Bank/OECD 区域与指标
- `market_indices` / `sector_etfs`：行情标的
- `fund_screener`：基金池筛选规则(成立年限/费率/去重/上限/排序)
- `scoring_weights`：5维评分权重（绩效/风险/策略/成本/一致性）
- `strategy_params`：分析参数阈值（`valuation_thresholds` / `sentiment_thresholds` / `cost_filter`）
- `rebalancing.score_threshold`：换仓最小分差门槛（默认 10 分）
- `ai_analysis.enabled`：是否开启 Claude AI 两阶段增强分析（默认 false）

---

## 五、数据真实性（provenance）

每个采集器都会记录本次用的是真实数据还是模拟数据(`collection_meta` 表)，
信号带 `data_source` 字段，CLI 打印数据真实性横幅：

- ✅ **real** — 全部真实数据
- ⚠️ **partial** — 部分真实/近似(如估值回退到点位近似)
- ❌ **mock** — 含模拟数据，仅供界面演示，**不可用于实际决策**

**mock 警告**：若数据含模拟成分，投研报告第二章会明确标注"仅演示，不可用于实际决策"，防止演示数据被误用。

**数据过期检测**：`provenance.check_staleness()` 检查各数据源最后更新时间，超期时在 CLI 横幅和报告中追加警告（宏观≤7天，行情≤3天，基金/估值≤7天）。

回测结果还会明确披露**幸存者偏差**(基金池为当前在运作的基金，未含已清盘者，收益为乐观上界)。

---

## 六、目录结构

```
fund-research/
├── run.py                      # 一键入口：采集 → 信号 → 评分 → 组合 → 报告
├── scheduler.py                # 每日定时调度（北京时间 08:30）
├── backtest.py                 # 回测分析入口
├── config/settings.yaml(.example)  # 配置（API Key + 结构性配置）
├── reports/                    # 自动生成的 Markdown 投研报告（按日期命名）
├── src/
│   ├── application/
│   │   └── update_pipeline.py        # 统一更新编排（run/scheduler 共用）
│   ├── domain/
│   │   └── scoring.py                # 评分纯函数：category_percentile/
│   │                                 # consistency_score/cost_score/classify_signal 等
│   ├── reports/
│   │   └── report_builder.py         # Markdown 投研报告生成器（10章节）
│   ├── collectors/             # 采集层
│   │   ├── macro_collector.py        # FRED 美国宏观
│   │   ├── global_macro_collector.py # World Bank + OECD 全球宏观
│   │   ├── market_collector.py       # yfinance 市场行情
│   │   ├── valuation_collector.py    # multpl 真实 CAPE/PE
│   │   ├── fund_screener.py          # 规则筛选 QDII 基金池
│   │   ├── eastmoney_collector.py    # 天天基金 pingzhongdata 净值+持仓
│   │   ├── fund_collector.py         # akshare/CSV/模拟（备选）
│   │   └── news_collector.py         # 新闻情绪（AV/Finnhub，含 fallback）
│   ├── analyzers/              # 分析层
│   │   ├── macro_analyzer.py         # 经济周期四阶段
│   │   ├── global_macro_analyzer.py  # 各区域宏观周期
│   │   ├── valuation.py              # 估值指标(真实优先)
│   │   ├── fund_analyzer.py          # 绩效(夏普/回撤/波动)
│   │   └── narrative.py              # 市场文字叙事(不参与评分)
│   ├── recommender/            # 决策层
│   │   ├── signals.py                # 市场综合信号
│   │   ├── scorer.py                 # 基金综合评分
│   │   └── portfolio.py              # 组合构建 + AI Phase 2 注入
│   ├── ai/                     # AI 增强层（可选，配置开关控制）
│   │   ├── phase1_market_analyzer.py # Phase 1：市场解析
│   │   ├── phase2_portfolio_advisor.py # Phase 2：投资决策
│   │   ├── schemas.py                # Tool use JSON Schema
│   │   └── backend.py / client.py / cache_strategy.py
│   ├── backtester/engine.py    # 走向前回测引擎（无前视偏差）
│   └── utils/
│       ├── config.py / database.py / provenance.py
│       └── fund_universe.py          # 基金标的库 + 分类/去重规则
├── tools/
│   ├── download_seed_data.py       # 净值种子下载
│   ├── mcp_technical_analysis.py   # MCP 技术分析服务器（RSI/MACD/布林带）
│   └── setup_mcp.sh                # MCP 扩展依赖安装脚本
└── .mcp.json                       # Claude Code MCP 服务器配置
```

### 数据库表（SQLite，`data/fund_research.db`）
`macro_data` `global_macro` `market_data` `valuation_data` `fund_list` `fund_nav_history`
`fund_holdings` `fund_performance` `fund_scores` `market_signals` `collection_meta`

---

## 七、已实现的功能

- ✅ 多源数据采集，全部带**失败降级 + 真实性标记 + 过期检测**
- ✅ 美国宏观(实际GDP/名义GDP/核心PCE/收益率曲线/信用利差) + 全球区域宏观(World Bank/OECD)
- ✅ **真实 Shiller CAPE/PE** 估值(非股价线性近似)，历史分位用真实序列
- ✅ **真实巴菲特指标**：NCBEILQ027S / GDP（真实 FRED 数据，不可用时退回近似并标注）
- ✅ 经济周期四阶段判断 + 美联储方向
- ✅ 市场叙事层：估值/情绪/成本/板块趋势的文字观察（与量化信号解耦）
- ✅ 去相关的市场综合信号(5因子) → 仓位建议
- ✅ **类别相对化**基金五维评分（绩效/风险在同类中横向比较，消除跨类偏差）+ 换仓成本门槛
- ✅ **评分纯函数统一**：`src/domain/scoring.py` 消除生产与回测双实现，修复 NaN 排名 bug
- ✅ **规则驱动的基金池筛选**(成立年限/费率/份额合并/按指数去重)
- ✅ 天天基金 pingzhongdata **真实净值全历史 + 持仓 + 经理**
- ✅ 走向前回测引擎(无前视偏差，**四基准对比**，披露幸存者偏差)；60/40 基准正确纳入无风险利率对现金仓位的贡献
- ✅ **Markdown 投研报告**：10章节完整报告，首页结论+证据链+行动计划+数据可信度披露
- ✅ 每日定时调度(北京时区自适应，**信号变化自动通知**，自动生成每日报告)
- ✅ AI 两阶段增强（Claude Phase 1 市场解析 + Phase 2 投资决策，配置开关，失败自动 fallback；错误分级：API 错误简报，程序 bug 打印完整 traceback）
- ✅ **全链路鲁棒性**：各采集/分析层统一补充 NULL/NaN 防御，静默 `pass` 改为有诊断意义的 `[WARN]` 日志；宏观分析返回 `data_quality` 字段标记数据完整性
- ✅ **FRED Key 支持环境变量**：`FRED_API_KEY` 环境变量优先于配置文件，便于 CI/容器部署
- ✅ **回测嵌入报告**：`python run.py --backtest` 触发走向前回测，结果直接注入当期报告第九章（不加参数则保持原有流程，第九章显示"未执行"提示）
- ✅ **基金池宽基保底**：`classify_and_dedup` 在去重截断后，对 all_enriched 中有宽基候选但池中未覆盖的地区强制补入最优宽基基金，防止全美国高收益基金挤出日本/欧洲宽基
- ✅ **基金池规模过滤**：`apply_filters` 接入 `min_aum_yi` 配置；`_enrich_aum()` 从 `fund_list.total_assets` 补充规模数据（需 pingzhongdata 富集后生效，未富集时过滤自动失效/放行）
- ✅ **基金元数据核对**（2026-06-02）：25 只核心库基金逐一核验；519977 长信全球债券确认为 QDII 债券基金（`bond`），非可转债；费率来源基金合同，不含申购费
- ✅ **个人化输入**：`config/settings.yaml` 新增 `user_profile` 块（risk_tolerance / investment_horizon_years / 仓位上下界）；`apply_user_profile()` 在信号档位基础上叠加偏移，conservative 降 ~10% 权益、aggressive 升 ~10%，短期投资者额外收紧；调整明细在 CLI 打印并写入报告
- ✅ **回撤止损机制**：`portfolio_tracker.py` 追踪假设持仓的加权累计净值（初始 100），与历史高水位比较；`risk_management.stop_loss_pct` 超阈值时强制信号降至"减仓防守"（核心 35% / 卫星 15% / 现金 50%）；快照增加 weight_pct + nav 字段供追踪用
- ✅ **全球宏观并入量化信号**（第6因子）：`compute_global_macro_score()` 对 World Bank GDP/通胀/失业 + OECD CLI 按 QDII 资产规模权重（美国40%/全球20%/…）加权，得到 0-10 评分；6因子权重：趋势27%+宏观18%+估值18%+情绪13.5%+信用13.5%+全球宏观10%；回测引擎同步更新，无前视偏差
- ✅ **信号组件归因**（逐因子屏蔽回测）：`run_factor_attribution()` 对 6 个因子逐一置 0（权重重分配至其余因子），对比基准年化收益与屏蔽后年化，量化每因子边际贡献；`python backtest.py --attribution` 触发，结果注入报告第九章
- ✅ **持仓接入打分**：`holdings_adjusted_strategy_score()` 用 fund_holdings 的真实股票/债券/现金仓位精修策略匹配分（70% 资产类别基础分 + 30% 持仓适配分）；无数据时自动退回原行为；回测引擎与生产评分口径一致
- ✅ **回测幸存者偏差修正**：成立日期保存至 fund_list；每个调仓日 t0 仅允许使用成立日期 ≤ t0 的基金参与对照组评分；返回修正后年化/夏普/回撤，量化偏差溢价（原始 − 修正 = 乐观高估量）；`--no-correction` 可关闭

---

## 八、待进一步打磨的方向

| 方向 | 说明 |
|------|------|
| **回测幸存者偏差（完整修复）** | 已清盘基金需历史成分数据；当前修正了"尚未成立"偏差，已清盘基金仍以披露为主（外部数据源缺失） |

---

## 九、技术备注

- **无前视偏差回测**：每个调仓日仅用截至该日的数据快照；真实 CAPE 按日期 as-of 引用。
- **四基准对比**：回测同时输出「等权买入持有」基准（无择时无择基），可直观区分信号择时与基金选择的各自贡献；若策略跑输等权买持，说明择时未带来超额收益。60/40 基准的现金仓位按 `RF_ANNUAL / 12` 计息，与真实策略口径一致。
- **live 与回测同口径**：信号权重、策略匹配、评分口径在实时与回测中共用 `src/domain/scoring.py` 同一纯函数实现，消除此前双实现导致的口径漂移风险。
- **报告容错设计**：报告生成失败（如数据为空、权限问题）仅打印 warning，不中断数据采集与信号生成主流程。
- **信号变化通知**：调度器运行后对比新旧信号档位，档位变化时以 WARNING 级别写入日志，方便追踪关键转折点。
- **时区**：调度器把北京时间 08:30 自动换算为系统本地时区。
- **依赖**：pandas / numpy / yfinance / akshare / fredapi / requests / PyYAML / schedule / scipy / mcp[cli] / fastmcp / baostock（后三项为 MCP 扩展依赖，可选）。
