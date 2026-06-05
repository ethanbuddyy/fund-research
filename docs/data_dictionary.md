# 数据字典（Data Dictionary）

> **单一事实来源 / 防漂移**：本文件记录每张表的用途、字段语义、单位口径，
> 尤其是「字段名与实际含义不一致」的**复用约定**——这类口头约定正是会随时间
> 漂移、导致静默错误的地方（参见 Anthropic 自助数据分析实践）。
>
> **维护规则**：改动 `src/utils/database.py`（或 `fund_analyzer.py` 里的
> `fund_year_returns`）的表结构时，**必须在同一个 PR 内同步更新本文件**。
> `tests/test_data_dictionary.py` 会断言本文件记录的表集合与
> `database._KNOWN_TABLES` 完全一致——新增表却不写文档会让测试变红。
>
> 表名以 `## \`table_name\`` 二级标题标注（测试据此解析），请勿改动该格式。

## 通用约定

- **`updated_at`**：所有表均有，写入时刻（`datetime('now')`，UTC），非业务日期。
- **`date` 字段**：业务日期，统一 `YYYY-MM-DD` 字符串（非 DATE 类型）。
- **`source` 字段**：数据来源标记（如 `eastmoney` / `akshare` / `multpl` / `yfinance`），
  与 `collection_meta` 的 provenance 模式（real/partial/mock）配合判断可信度。
- **比率/费率单位**：除特别说明外，`expense_ratio` / `fund_fees.rate` 等费率字段
  一律是**小数**（`0.005` = 0.5%），不是百分数。
- **`fund_code`**：基金代码字符串（含前导零，如 `"000934"`），始终按 TEXT 处理。

---

## `macro_data`

FRED 美国宏观时间序列（长表）。

- `series_id`：FRED 序列 ID（如 `GDPC1` / `CPIAUCSL` / `FEDFUNDS`）。
- `value`：**原始值，单位随 series 而定**——利率类是百分数（`5.25` = 5.25%），
  GDP 类是十亿美元，指数类是点位。下游按 `series_id` 解释，切勿统一当百分数。
- 主键语义：`UNIQUE(series_id, date)`。

## `global_macro`

World Bank + OECD 各区域宏观（长表）。

- `region`：区域名（中文，如 `美国` / `欧元区`）。
- `indicator`：指标名（`gdp_growth` / `inflation` / `unemployment`）。
- `value`：年度百分比值（World Bank 口径）。

## `market_data`

yfinance 指数 / VIX / 商品 / 板块 ETF 日线（长表）。

- `symbol`：yfinance 代码（`^GSPC` / `^VIX` / `GC=F` / `XLK`…）。
- `open/high/low/close/volume`：OHLCV，原始价格点位。
- 主键语义：`UNIQUE(symbol, date)`。

## `valuation_data`

真实估值序列（multpl / Shiller / yfinance 三级冗余）。

- `metric`：`cape`（Shiller CAPE）或 `sp500_pe`。
- `value`：估值点位（倍数），非百分数。
- 主键语义：`UNIQUE(metric, date)`。

## `fund_list`

基金主表（每基金一行）。

- `expense_ratio`：综合年费率，**小数**（`0.006` = 0.6%）。
- `total_assets`：规模，**单位：元**（下游常 `/1e8` 转「亿」）。
- `inception_date`：成立日，`YYYY-MM-DD`；缺失时 `--analyze` 用它估算「任职年限代理」。
- `nav` / `nav_date`：最新单位净值及其日期。
- **迁移列**（`init_database` 增量 ALTER 添加）：
  - `mgmt_fee`：管理费率（小数，年）。
  - `custody_fee`：托管费率（小数，年）。

## `fund_nav_history`

基金净值历史（长表）。

- `nav`：单位净值。`acc_nav`：累计净值（缺失时回退等于 `nav`）。
- `daily_return`：当日涨跌幅（**百分数**，如 `0.5` = +0.5%），可为 NULL。
- 净值已按人民币口径对齐（yfinance 美元代理价已用 USD/CNY 汇率换算后拼接）。

## `fund_performance`

绩效指标（每基金一行，由 `fund_analyzer` 重算）。

- `annualized_return` / `volatility` / `max_drawdown`：**百分数**口径。
- `sharpe_ratio`：无量纲。
- 主键语义：`fund_code UNIQUE`。

## `fund_scores`

五维综合评分结果（每基金一行，由 `scorer` 写入）。

- `total_score`：0–100。其余 `*_score`：各维度得分。
- `signal` / `recommendation`：买入/持有/回避 等文本标签。

## `fund_holdings`

资产配置穿透 + 重仓（长表，天天基金）。

- `stock_ratio` / `bond_ratio` / `cash_ratio`：占比，**百分数**（`90` = 90%）。
- `stock_codes`：重仓股代码，**逗号拼接的字符串**（非 JSON 数组）。
- `managers`：序列化的经理信息文本。
- **迁移列**：`turnover_rates`（TEXT，逐年换手率序列）、`region_breakdown`（TEXT，区域占比）。

## `fund_manager`

⚠️ **存在字段语义复用，最易误用** —— 由 `eastmoney_collector` 写入：

- `avg_annual_return`：**字段名是「年化收益」，但实际复用存东财「综合评分」（0–100）**，
  不是任何收益率。报告里按 `/100` 评分展示（见 `report_builder.py` 经理段）。
- `return_3y`：**字段名是「近3年收益」，但实际复用存「任期累计收益%」**（百分数），
  不是滚动 3 年收益。
- `return_1y` / `return_5y`：保留字段，当前采集器未必填充。
- `work_start_date`：任职起始（文本，可能是时长描述）。
- `total_assets_managed`：在管规模（TEXT，含单位的原始字符串）。
- 主键语义：`UNIQUE(fund_code, name)`。
> 复用原因：东财 `pingzhongdata` 的经理数据结构与本表字段不一一对应，
> 为避免新增表而复用语义相近字段；改采集逻辑或字段含义时务必同步本节与
> `report_builder.py` 的经理渲染段。

## `fund_turnover`

逐年换手率（长表）。

- `turnover_rate`：年换手率，**百分数**。
- 主键语义：`UNIQUE(fund_code, year)`。

## `fund_fees`

申购/赎回费率明细（长表）。

- `fee_type`：`purchase` / `redemption`。
- `rate`：费率，**小数**（`0.015` = 1.5%）。
- `rate_desc` / `amount_min` / `amount_max`：分档条件描述与金额区间。

## `fund_year_returns`

逐年收益（长表）。**注意：建表在 `src/analyzers/fund_analyzer.py`，不在 `database.py`。**

- `return_pct`：该年收益率，**百分数**。
- 主键语义：`(fund_code, year)`（采集逻辑保证唯一）。

## `market_signals`

每日市场综合信号快照（每日一行，`signals._save_signal` 写入）。

- `composite_signal`：四档之一（重仓进取/标配稳健/谨慎防守/减仓防守）。
- `core_allocation` / `satellite_allocation` / `cash_allocation`：**小数**（`0.6` = 60%）。
- `cape` / `vix` / `buffett_indicator` / `equity_risk_premium`：对应指标点位。
- 主键语义：`date UNIQUE`。
> 与运行时传递的 `MarketSignal`（`src/domain/types.py`）是两套口径：本表是**持久化的精简子集**，
> `MarketSignal` 是内存中的完整结构（含 30+ 字段、嵌套子对象）。

## `news_sentiment`

新闻情绪缓存（按日期+来源）。

- `source`：`alphavantage` / `finnhub`。
- `bullish_pct` / `bearish_pct`：多空占比，**小数**（0–1）。
- `news_score` / `buzz` / `articles_count`：情绪分、热度、文章数。

## `collection_meta`

数据来源 provenance（每数据源一行，`utils/provenance.py`）。

- `source`：`macro` / `market` / `fund` / `valuation` 等（**主键**，每源一行，UPSERT 覆盖）。
- `mode`：`real` / `partial` / `mock`——决定报告/CLI 的可信度横幅；
  关键源任一为 `mock` 则整体视为不可用于决策。
- `rows` / `detail`：本次采集行数与说明。
