# R542 最近一年失败归因设计

## 目标

对冻结的 R539C 13 因子组合做最近一年失败归因，回答：

1. 最近一年亏损来自因子 IC 失效，还是市场 beta、行业/风格暴露、组合构建或交易成本？
2. 13 个因子各自在最近一年是贡献还是拖累？
3. 止损、低换手、行业上限和 10 仓集中度分别造成了什么？
4. 与沪深300、PIT 成分等权组合相比，组合的相对表现到底如何？

本轮回溯只做诊断，不选择新因子、不调权重、不产生 active version、订单、纸面目标或生产配置。

## 数据与冻结输入

- R539C result/preregistration；
- R536A 13 个独立簇代表；
- R526 PIT 成员和行业；
- R530/R531/R532/R533 冻结面板与评分；
- R537/R538/R539 正式回测 runtime；
- `data/regime_history/part.parquet`（如果覆盖不足，必须显式记录缺口，不得外推）。

## 时间窗口

- 训练：2022-05-05 至 2024-12-31；
- validation：2025-01-01 至 2025-10-13；
- recent：2025-10-14 至 2026-11-05；
- full：2022-05-05 至 2026-11-05。

## 冻结组合

- 13 个 R536A 独立代表，等权 rank composite；
- 10 仓、60 日、行业上限 3；
- stop=-12%，无 trailing；
- T+1 开盘，2x 成本基线；
- 无杠杆，最大敞口 100%。

## 诊断矩阵

### 1. 因子 IC 与分组收益

每个因子计算：

- train、validation、recent、full 的 directional rank IC；
- industry+size neutral IC；
- recent 与 train 的 IC 变化；
- recent 的 top quintile - bottom quintile forward 5d spread；
- 覆盖率。

### 2. 单因子与留一法正式回测

每个因子运行同冻结执行参数下的：

- single：只使用该因子；
- leave-one-out：去掉该因子；
- full 和 recent 两个窗口；
- 2x 成本。

另外运行：

- baseline 全因子 full/recent；
- baseline 无止损 full/recent，作为交易层对照。

所有产生组合收益的运行必须走项目 `run_with_evidence()` 并紧跟 `require_formal_evidence()`。

### 3. 相对基准

- 沪深300指数；
- PIT HS300 成分等权组合；
- 计算 recent/full 的策略、沪深300、等权基准收益和超额。

### 4. 风险与暴露

- 策略对沪深300的 beta；
- 月度收益和超额；
- 最大回撤区间；
- 持仓的行业、市值分位、平均持有期；
- 止损退出占比、出场原因分布、交易集中度；
- 市场 regime 覆盖内的日收益和超额。

### 5. 因子贡献定义

- `loo_contribution = baseline_total_return - leave_one_out_total_return`；
  - 正数：该因子对组合有正贡献；
  - 负数：该因子拖累组合。
- 这只是一种组合层面的边际贡献，不是因果归因。

## 判定

- 本轮不设 promotion gate。
- 只要 basline recent/full 与 R539C 对齐、所有 formal evidence 通过、13 个因子诊断完整，诊断即为 `complete`。
- 最终结论固定为 `rejected` 或 `inconclusive`，不能给 active/watch-only。
- 5x 成本只作 warning-only 诊断，不做硬否决。

## 输出

- 不可变 JSON/Markdown + `.sha256`；
- ResearchLedger 绑定；
- 资产台账新版本；
- 明确 known limits 和数据覆盖缺口。
