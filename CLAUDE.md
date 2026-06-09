# 基金投研系统 — 量化分析 · 项目纪律

> 四层链路:**采集 → 分析 → 决策 → 报告**。数字由确定性 Python 算出,LLM 只读不改。
> 本文件是 AI 协作的运行契约;面向人类的完整说明见 `README.md`「一、系统架构」。

## 架构与数据流

单向流(下游只读上游产物,不回头请求重算、不改参数):

```
config/settings.yaml + src/domain/factor_config.py   ← 指标字典/权重(唯一真相源)
  → src/collectors/        采集层:FRED宏观 / World Bank·OECD / yfinance行情 / 估值 / 天天基金 / Baostock ETF
  → src/analyzers/         分析层:宏观周期 / 估值 / 情绪 / 叙事 / 基金绩效
  → src/recommender/       决策层:signals → scorer → portfolio(产出 signal/scores_df/portfolio)
  → src/reports/           报告层:report_builder(MD) + html_report_builder(HTML)
  → reports/  +  /mnt/e/WSL-output/
```

数据落地 SQLite:`data/fund_research.db`(路径见 `settings.yaml: db_path`)。

| 层 | 目录 | 职责 |
|----|------|------|
| 编排 | `src/application/update_pipeline.py` | **唯一编排点**,`run.py` 与 `scheduler.py` 共用;改采集顺序/步骤只动这里 |
| 配置 | `config/settings.yaml` + `src/domain/` | 阈值/权重/指标字典 + TypedDict 契约 |
| 采集 | `src/collectors/` | 数据源选择=网络检测+优先级+缓存,**非 LLM 决定** |
| 分析 | `src/analyzers/` `src/analysis/` | 周期/估值/情绪/叙事/单基金研判 |
| 决策 | `src/recommender/` | 六因子信号、基金评分、核心-卫星组合 |
| AI 增强 | `src/ai/` | 三阶段决策,**LLM 调用全部隔离在此** |
| 报告 | `src/reports/` | 读 signal/portfolio → MD + HTML,只读不回算 |
| 检索 | `src/retrieval/` | BM25 词法检索(可升级 embedding);沉淀语料(`documents` 表)+ `--recall` 语义搜索 + RAG 注入 |
| 回测 | `src/backtester/engine.py` | 走向前回测(无前视),`backtest.py` 独立入口 |
| 溯源 | `src/utils/provenance.py` | REAL/PARTIAL/MOCK 记录与聚合 |

## 核心纪律

**1. 配置驱动,禁止魔法数字** —— 业务参数(阈值/权重/指数/区域)全在 `config/settings.yaml`;
六因子与区域权重在 `src/domain/factor_config.py`,`signals.py` 与 `backtester/engine.py`
**共同引用此处**。新增/改阈值同步改字典,勿在代码硬编码。〔代码强制:权重集中定义〕

**2. LLM 只读不执行 + 不造数字** —— 量化计算全在 `quant` 链路(collectors→analyzers→recommender)
纯函数完成;LLM 仅做语言增强,**不决定数据源、不改数字、不编造数字**。AI 受
`settings.yaml: ai_analysis.enabled` 总开关 + `skip_on_mock_data` 闸门控制。
**数字纪律(prompt 强制)**:LLM 只能引用「输入中已给的量化事实」;系统**算不出**的指标
(情景收益率/alpha/回撤/发生概率/beta/历史分位均值/类比年份)**禁止给具体数值**,只用定性方向语。
情景仓位更进一步——LLM 只选 `target_tier`(档名枚举)+ 基金调整方向,**绝对百分比由
`domain/scoring.py: POSITION_TIERS` 确定性回填**,LLM 不自算、不把多基金权重相加推总仓位
(否则必现算术矛盾)。改 Phase1/2 的 schema/prompt 时务必保留这些约束,这是 Claude 输出质量的底线。
〔代码强制:仅 `src/ai/client.py` 接触 LLM SDK;provider=anthropic/deepseek/openai,OpenAI 兼容
路径已适配 DeepSeek 思考型模型工具调用(`backend.py`)〕

**3. 决策层 ↔ 报告层 单向** —— 决策层产出 `signal`/`scores_df`/`portfolio`(内存 dict,契约见
`src/domain/types.py: MarketSignal`);报告层只消费,不反向调决策重算。AI 三阶段挂载点:
phase1 嵌在 `recommender/signals.py`,**phase2 与 phase3 均在 `recommender/portfolio.py` 调用**
(phase3 紧随 phase2 复核其产物),报告层 `src/reports/` 只**渲染** phase3 结果
(横幅 `review_banner`/复核块 `review_action_caveat`/附录全表 `_adversarial_findings_table`),
不调用 AI。〔现状:`recommender` 不 import `reports`;AI 隔离在
`src/ai/`,AI 层不反向 import 报告层(`domain/scoring.py` 作为最底层供两边共用情景渲染纯函数)。
注意——文档理想中的 `shared/` JSON 物理隔离墙在本项目**未落地**,signal 以内存 dict 传递,改字段名须顾下游〕

**报告三层结构(2026-06 重构,改报告前必读)** —— 正文四层:① 本期决策 ② 为什么(证据)
③ 买什么·卖什么 ④ 何时改变;数据可信度/备选池/回测/算法参数/对抗审查全文收进**折叠
审计附录**(`<details>`)。MD(`report_builder.py`)与 HTML(`html_report_builder.py`)**结构同源**,
改一边须同步另一边。两条新不变量:
(a) **六因子表权重必取 `factor_config.FACTOR_WEIGHTS`**,禁止硬编码,且须含 `global_macro`——
否则用户算不平综合分(回归 `tests/test_report_builder.py::TestA1SixFactorTable`)。
(b) **触发条件单一真相源 `report_editor.canonical_triggers`**:正文「何时改变」是唯一整列出处,
首页只放 `headline_triggers`(最关键 1 条)+提示;情景表用 `format_scenario_case(.., include_actions=False)`
只说「会怎样」,不重复操作。改触发渲染勿在各处各写一遍(回归 `TestThreeLayerStructure`)。

**4. 溯源必含 + 内容哈希缓存** —— 两层都在 `src/utils/provenance.py`:
(a) 模式溯源:`record(source, mode)` 标注 real/partial/mock,`overall_mode()` 聚合
(任一 mock→不可用于决策),`run.py` 打印 `banner()`;随机模拟数据**禁止**当真实行情。
(b) 缓存按 **主键 + `data_hash` + `config_hash`** 失效(`DataResult`/`cache_get`/`cache_put`/
`cached_fetch`):配置一变 config_hash 变→旧缓存作废;原始 payload 内容寻址落
`data/raw/<source>/<hash>.json`(不可变快照,供复现/审计)。表 `data_cache` 见数据字典。
新增缓存点用 `cached_fetch(source, fetch_fn, source_id=, config_subset=, max_age_days=)`。〔代码强制〕

**5. 算法歧义必问** —— 涉及算法/规则/计算口径的指令,动手前先问清"X 是 A 还是 B";
用户给完整规则前不靠猜来回试错。〔约定〕

**6. 检索层只读增强,不改数字** —— `src/retrieval/` 沉淀「用完即弃」文本(叙事/区域/研判)、
截留新闻原文、收编历史报告进 `documents` 表(内容寻址去重,复用 `compute_data_hash`),
供 BM25 词法检索。受 `settings.yaml: retrieval.enabled` 总开关 + `inject_into_ai` 注入闸门控制:
**关闭注入则 AI 三阶段 prompt 与现状逐字一致**(回归保护)。总开关是**单一真相源**
(`retrieval.recall.is_enabled()`,所有入口含新闻截留均据此短路);报告「数据可信度」板块
显示该层当前状态(`status_line()`:开关/RAG 注入/语料量),提醒用户这一可选板块的存在与状态。检索仅做证据增强,不进量化计算、
不改信号/评分。后端经 `bm25.py: Retriever` 协议封装,日后加 embedding 后端实现同接口即可热插拔
(`retrieval.backend` 切换)。ingestion 挂在唯一编排点 `update_pipeline.run_update()` 末尾(`ingest_run`)。
〔代码强制:`documents` 表入 `_KNOWN_TABLES` 须同步 `docs/data_dictionary.md`,防漂移测试强制〕

## 关键不变量(改动前必读)

- **止损顺序**:`update_pipeline` 中浮亏追踪(`portfolio_tracker.update_and_check`)必须在
  `build_portfolio_recommendation` **之前**——快照读的是上次净值;放后面会读到刚写入的当前值,
  本期收益恒为 0,止损失效。触发后先覆盖 `signal` 仓位档再建组合,保证口径一致。
- **回测口径独立**:`backtester/engine.py` 的 `_compute_signal` 是**无前视**口径,字段与
  生产链 `signals.py` 不完全相同,**不复用** `MarketSignal` 契约。
- **实际GDP**:周期判断用 `GDPC1`(实际GDP)而非名义 `GDP`,否则通胀算进增长→系统性偏"扩张"。
- **TypedDict 是契约**:`signal`/`portfolio` 跨 8+ 模块传递;改 key 名前 grep 全下游,
  类型检查(mypy/pyright)能帮你发现遗漏。
- **仓位档位单一真相源**:`domain/scoring.py: POSITION_TIERS`(4 档→核心/卫星/现金)被
  `classify_signal`、报告情景渲染(`format_scenario_case`)、AI 情景三处共用。改档位数字只动这里;
  **绝不让 LLM 在 prompt 里自己写仓位百分比**(回归会立刻引出算术矛盾,见纪律#2)。
- **Phase3 审查事实须与 Phase2 同源(防漂移)**:`phase3_adversarial_reviewer._format_facts`
  提供的量化事实**必须覆盖 Phase1/2 决策时实际所见的同一套数据**(因子分/估值含 CAPE 分位/
  宏观含核心 PCE/分区域 GDP/个基细分分),否则审查员会把「决策引用了此处缺失的数据」
  误判为「无依据」的**假阳性**。个基细分分字段须与 `phase2_portfolio_advisor._format_funds`
  保持同步(已在两处 docstring 互相标注)。
- **思考型模型的 max_tokens**:DeepSeek `deepseek-v4-pro`/`reasoner` 等的 reasoning_tokens
  也计入 `max_tokens`,过小会被推理榨干致输出截断;Phase3 `adversarial_review.max_tokens`
  须给足(claude 3000 够,思考型给 8000)。该项在 `settings.yaml`(gitignored),换机须重配。

## 入口命令

| 命令 | 用途 |
|------|------|
| `python3 run.py` | 完整流程:采集→信号→评分→组合→报告(MD+HTML) |
| `python3 run.py --backtest` | 附带走向前回测(注入报告第九章) |
| `python3 run.py --analyze <代码或名称>` | 单基金综合研判 |
| `python3 run.py --search <关键词>` | 基金代码搜索 |
| `python3 run.py --recall <查询>` | 语义检索已沉淀语料(叙事/新闻/研判/历史报告),独立、不触发采集 |
| `python3 run.py --check-holdings [文件/内联]` | 持仓健康诊断(默认 `config/my_holdings.yaml`) |
| `python3 scheduler.py` | 每日定时调度(北京时间 08:30) |
| `python3 backtest.py --attribution` | 独立回测 + 因子归因 |
| `pytest` | 测试套件(配置见 `pyproject.toml`) |

## 协作减负(Subagent 外移)

下列场景用 Agent 工具外移,主上下文只接收结构化结论:读 PDF/长 log/单文件 >5k 行、
跨文件 grep 全项目、网页抓取聚合多 URL、长链路量化计算(只要结论不要过程)。
中等代码修改(单文件 <200 行)不外移——上下文损失 > 收益。
