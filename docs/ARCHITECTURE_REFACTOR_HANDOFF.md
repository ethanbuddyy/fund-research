# 架构修复执行交接单

> 接手对象：Claude Code  
> 执行原则：渐进迁移、保持行为兼容、每阶段独立验证。不要一次性搬目录或重写系统。

## 一、任务目标

当前项目已经形成 `collectors → analyzers → recommender → reports` 的目录分层，但运行时仍依赖
SQLite、JSON 快照和可变 `dict` 进行隐式通信。本次修复的目标不是引入复杂架构，而是优先解决：

1. 同一次运行中，数据库信号与最终组合使用的信号可能不一致。
2. `portfolio_snapshot.json` 同时承担上期组合、换仓门槛、止损基准和报告对比，正确性依赖调用顺序。
3. 决策模块混合数据读取、业务计算、AI 调用和持久化。
4. 报告层直接读取数据库、配置和快照，并包含业务规则。
5. `MarketSignal` / `PortfolioRecommendation` 是跨模块可变大字典，边界过软。

最终应保留单机 SQLite + 文件存储，不引入 Web 框架、消息队列、容器编排、ORM 或重量级依赖注入。

## 二、开始前必读

- `CLAUDE.md`
- `src/application/update_pipeline.py`
- `src/recommender/signals.py`
- `src/recommender/scorer.py`
- `src/recommender/portfolio.py`
- `src/utils/portfolio_tracker.py`
- `src/domain/types.py`
- `src/reports/report_builder.py`
- `src/reports/html_report_builder.py`
- `tests/test_pipeline_integration.py`
- `tests/test_report_builder.py`
- `tests/test_holdings_checker.py`

必须保留 `CLAUDE.md` 中的关键不变量，尤其是：

- 止损检查必须基于上次组合，不能读取本次刚写入的快照。
- LLM 只读，不计算或修改确定性数字。
- 报告层不能反向触发决策重算。
- 回测口径保持独立。
- `POSITION_TIERS` 和 `FACTOR_WEIGHTS` 继续作为单一真相源。

## 三、已确认的风险点

### 1. 市场信号存在两个版本

`generate_market_signal()` 在 `src/recommender/signals.py` 中先写入 `market_signals`，随后
`update_pipeline.run_update()` 可能因止损直接修改内存中的：

```python
signal["composite_signal"]
signal["core_allocation"]
signal["satellite_allocation"]
signal["cash_allocation"]
```

数据库保存的可能是止损前信号，组合、报告和调度日志使用的却是止损后信号。

### 2. 组合快照存在时序耦合

`src/recommender/portfolio.py` 在组合构建结束时覆盖 `data/portfolio_snapshot.json`。

该文件同时被以下逻辑使用：

- `portfolio_tracker.py`：作为上期净值基准。
- `portfolio.py`：作为换仓门槛的上期持仓。
- `report_builder.py`：作为上期组合进行换仓对比。

报告生成发生在快照覆盖之后，因此可能退化为“本期和本期比较”。

### 3. 决策函数包含过多副作用

`generate_market_signal()` 同时读取配置、查询数据库、调用分析器、调用新闻采集、执行量化计算、
调用 AI、生成叙事并保存数据库。

`build_portfolio_recommendation()` 同时查询数据库、读取旧快照、选基、调用 AI 并写入新快照。

### 4. 报告层不是纯渲染

报告层当前会：

- 直接读取 provenance、配置、数据库和快照。
- 推导关键结论、风险和调仓触发条件。
- 在 Markdown 和 HTML 中分别维护部分相同规则。
- 由 HTML 导入 Markdown 构建器的私有函数。
- 由 `report_editor.py` 延迟反向导入 `report_builder.py`。

### 5. 类型契约无法阻止状态漂移

`src/domain/types.py` 使用 `TypedDict(total=False)`，所有字段均可缺失，嵌套结构大量使用
`dict[str, Any]`。该设计可以暂时保留，但新增核心边界不得继续扩大无结构字典。

## 四、执行阶段

每个阶段完成后单独运行测试并检查 diff。除非前一阶段验收通过，否则不要进入下一阶段。

### 阶段 1：统一组合状态所有权

优先级：最高。

新增建议文件：

```text
src/infrastructure/
├── __init__.py
└── portfolio_state_store.py
```

如果不希望现在新增 `infrastructure/`，可先放在：

```text
src/utils/portfolio_state_store.py
```

但所有快照路径和 JSON 解析必须集中到该模块。

提供最小接口：

```python
load_previous_portfolio() -> dict | None
save_current_portfolio(snapshot: dict) -> None
load_nav_state() -> dict
save_nav_state(nav: float, high_water_mark: float) -> None
```

执行要求：

1. `portfolio.py`、`portfolio_tracker.py`、`report_builder.py` 不再自行拼接快照路径。
2. `run_update()` 开始决策阶段前只读取一次上期快照。
3. 上期快照作为显式参数传给止损、组合选择和报告数据构建。
4. 本期组合快照必须在所有对比数据计算完成后再提交。
5. 保持现有 JSON 文件格式兼容，不迁移历史文件。
6. 快照损坏仍需明确输出警告，不得静默重置。

推荐调整后的顺序：

```text
读取 previous_state
→ 生成原始信号
→ 根据 previous_state 做止损检查
→ 构建本期组合并计算换仓变化
→ 生成本期完整结果
→ 持久化最终信号
→ 保存 current_state
→ 返回结果
```

验收条件：

- 止损只读取上期快照。
- 报告能正确显示新增和移除基金。
- 组合构建函数不再自动覆盖快照。
- 首次运行、旧格式快照、损坏快照均有测试。

### 阶段 2：保证最终信号只持久化一次

调整 `generate_market_signal()`：

1. 默认只负责生成信号，不在计算中途落库。
2. 可以暂时保留 `save` 参数兼容旧调用，但 `run_update()` 必须使用 `save=False`。
3. 新增明确的保存入口，例如：

```python
save_market_signal(signal: Mapping[str, Any]) -> None
```

4. 止损覆盖完成后，再保存最终信号。
5. 不允许 `update_pipeline.py` 手写 `0.35/0.15/0.50`，必须从
   `domain.scoring.POSITION_TIERS["减仓防守"]` 获取。
6. 调度器、持仓诊断从数据库读取的必须是最终信号。

建议把止损覆盖提取为纯函数：

```python
apply_stop_loss(
    signal: MarketSignal,
    stop_loss_info: Mapping[str, Any] | None,
) -> MarketSignal
```

不要原地修改传入对象，应返回新字典或新模型。

验收条件：

- 止损触发后，数据库、返回值、组合和报告中的信号及仓位完全一致。
- 同一日期只保存最终版本。
- 不再出现编排层硬编码仓位档位。

### 阶段 3：分离数据加载与纯计算

不要立即移动目录。先在现有模块中提取纯计算函数。

目标接口：

```python
compute_market_signal(inputs, config) -> MarketSignal
score_funds(funds, performance, holdings, market_signal, config) -> pd.DataFrame
select_portfolio(scores, funds, market_signal, previous_state, config) -> PortfolioRecommendation
```

现有公开函数可继续作为适配器：

```python
generate_market_signal()
score_all_funds()
build_portfolio_recommendation()
```

适配器负责读取数据库和配置，纯函数不得：

- 调用 `load_config()`。
- 调用 `read_table()` / `get_connection()`。
- 读写文件。
- 调用 AI。
- 打印日志。

AI Phase1/2/3 继续由应用流程调用，结果再附加到对应输出，不要塞进纯计算函数。

验收条件：

- 市场信号和组合选择核心可以只靠内存数据测试。
- 原有 CLI 行为保持不变。
- `mypy src/` 通过。

### 阶段 4：建立少量专用数据访问接口

不要封装全部表，也不要引入 ORM。

先建立三个窄接口：

```text
SignalRepository
FundRepository
PortfolioStateStore
```

最低职责：

- `SignalRepository`：保存和读取最终市场信号。
- `FundRepository`：读取基金基础数据、评分、最新净值和持仓。
- `PortfolioStateStore`：管理上期组合及净值状态。

应用层不应继续直接写 SQL。已有采集器可暂时继续使用 `database.py`，不要扩大改动面。

验收条件：

- `scheduler.py` 不直接执行 `SELECT market_signals`。
- `portfolio.py` 不直接查询最新净值。
- `holdings/checker.py` 可暂时保留批量 `read_table()`，不要求本阶段全部迁移。

### 阶段 5：增加共享报告模型

新增建议文件：

```text
src/reports/report_model.py
```

建立单一入口：

```python
build_report_model(
    signal,
    portfolio,
    scores,
    backtest,
    previous_state,
    provenance,
    config,
) -> ReportModel
```

`ReportModel` 至少包含：

- 决策摘要。
- 关键结论。
- 因子表。
- 风险提示。
- 调仓变化。
- 触发条件。
- 推荐与备选基金。
- 算法参数。
- provenance 和回测附录数据。

Markdown 和 HTML 构建器只消费 `ReportModel`。迁移时允许旧接口内部先构建 model，再调用 renderer。

必须消除：

- HTML 对 `report_builder.py` 私有函数的导入。
- `report_editor.py` 对 `report_builder.py` 的反向延迟导入。
- 报告层直接读取 `portfolio_snapshot.json`。
- Markdown 和 HTML 各自维护仓位档位表、算法阈值或触发规则。

报告文件较大，不要求一次拆完。优先抽取共享业务含义，CSS 和格式化函数可以继续留在渲染器。

验收条件：

- 相同输入下，Markdown 与 HTML 使用同一关键结论、触发条件和调仓变化。
- 报告渲染过程不查询数据库、不读取配置和组合快照。
- 现有报告结构测试继续通过。

### 阶段 6：收紧核心类型

在前述边界稳定后再执行。

建议新增小型 dataclass 或更严格的 TypedDict：

```python
MarketFacts
MarketDecision
StopLossResult
FundScore
PortfolioFund
PortfolioRecommendation
PortfolioState
ReportModel
```

要求：

1. `MarketFacts` 与最终 `MarketDecision` 分开，避免止损覆盖原始事实。
2. 核心字段应必填；AI、叙事、审查结果可以可选。
3. 暂时保留旧 `dict` 输出兼容外部调用。
4. 不要为了类型化重写所有分析结果。

## 五、建议测试

优先补充以下回归测试：

1. 止损触发后，数据库信号等于 `run_update()` 返回信号。
2. 止损仓位来自 `POSITION_TIERS`，不是编排层硬编码。
3. 组合构建不会在报告对比前覆盖上期快照。
4. 上期 A/B、本期 B/C 时，报告显示新增 C、移除 A。
5. 损坏快照不会静默吞掉，并且主流程可继续。
6. `select_portfolio()` 在纯内存输入下可重复得到同一结果。
7. Markdown 和 HTML 的关键触发条件来自同一个 `ReportModel`。
8. AI 关闭时，纯量化路径行为与重构前一致。

每阶段至少运行：

```bash
pytest
mypy src/
```

若全量测试较慢，开发中可先运行：

```bash
pytest tests/test_pipeline_integration.py tests/test_report_builder.py tests/test_holdings_checker.py
```

阶段完成前仍需执行全量测试。

## 六、禁止事项

- 不要一次性把所有目录改名或搬迁。
- 不要引入 SQLAlchemy、Pydantic、FastAPI、Celery 等新框架。
- 不要改变评分算法、因子权重、仓位档位或报告投资含义。
- 不要把回测信号强行合并进生产 `MarketSignal`。
- 不要让报告层调用决策层重算。
- 不要删除旧 JSON 快照兼容逻辑。
- 不要顺手重写超过 1000 行的单基金分析模块。
- 不要为了“解耦”创建大量只有一个调用点的接口或抽象基类。
- 不要吞掉快照、数据库写入等关键状态错误。

## 七、提交拆分建议

建议按以下粒度分别提交：

1. `refactor: centralize portfolio state storage`
2. `fix: persist final post-risk market signal`
3. `refactor: extract pure signal and portfolio calculations`
4. `refactor: add focused signal and fund repositories`
5. `refactor: introduce shared report model`
6. `refactor: tighten core decision contracts`

每个提交应保持测试通过，不要把目录大搬迁和行为修改放在同一个提交。

## 八、完成定义

全部完成后应满足：

- 同一次运行只有一个最终市场决策版本。
- 上期组合、本期候选组合、本期已提交组合有明确区分。
- 止损、换仓门槛和报告对比不再依赖隐含调用顺序。
- 核心计算可脱离 SQLite、配置文件和 AI 独立测试。
- 报告层只消费准备好的展示模型。
- 新增业务规则主要修改领域策略或应用服务，不需要同步修改多个渲染器。
- 保持现有 CLI、SQLite 数据、报告输出和回测行为兼容。

## 九、第一轮执行边界

第一轮只执行阶段 1 和阶段 2，并提交测试结果及残余风险说明。

完成后先停下来复核以下内容，再决定是否进入阶段 3：

- 快照的读取和提交时点是否明确。
- 报告换仓比较是否真实使用上期组合。
- 止损后的最终信号是否已经统一落库。
- 是否仍有模块直接读取 `portfolio_snapshot.json`。

