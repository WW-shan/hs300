# GBM 综合市场压力测试设计

## 目标

在固定几何布朗运动（GBM）世界中，检查冻结执行骨架的选择、成本、止损、相关性和价格类信号是否会出现结构性失效。研究类型固定为 `synthetic_market_stress`，只用于诊断，不得冒充真实 HS300 历史回测，也不得产生 active version、订单、纸面目标或生产启用。

## 非目标

- 不验证真实财报、情绪、行业、宏观因子，因为 GBM 数据没有这些字段。
- 不把随机价格路径上的收益当作可交付策略收益。
- 不优化真实因子参数，不替代正式项目回测。
- 不把 5× 成本作为硬否决；它是 warning-only 压力诊断。

## 冻结数据模型

- 模型：几何布朗运动，交易日收益服从固定漂移和波动率。
- 情景：
  - neutral：年化漂移 0%，年化波动 20%；
  - bull：年化漂移 8%，年化波动 18%；
  - bear：年化漂移 -8%，年化波动 25%；
  - high_volatility：年化漂移 0%，年化波动 35%。
- 每个情景 100 条独立路径，固定 master seed `20261125`，路径种子由 `SeedSequence(master_seed, scenario_index, path_id)` 确定。
- 股票数 300，行业 30 个，每行业 10 只。
- 每条路径生成 60 日 warmup + 504 日评价窗口；OHLC 使用 GBM 收盘价包络，成交量恒为正，不模拟停牌和涨跌停。
- 相关性结构：
  - independent：`beta=0`；
  - common_market：`beta=0.7`，即个股日收益为 `beta * market_shock + sqrt(1-beta^2) * idiosyncratic_shock`。
- 独立与共同市场结构共享同一条路径的市场/特质随机数，只改变合成方式。

## 信号模式

所有分数在交易日 t 收盘后可知，成交统一延迟到 t+1 开盘；warmup 内分数为 0。

1. `random_null`：与价格未来无关的确定性随机分数，用于空对照。
2. `momentum_20`：过去 20 日收益的横截面百分位。
3. `momentum_60`：过去 60 日收益的横截面百分位。
4. `reversal_5`：过去 5 日收益的负向横截面百分位。
5. `low_volatility_20`：过去 20 日日收益波动率的负向横截面百分位。
6. `momentum_low_vol_20`：20 日动量百分位与 20 日低波百分位的等权合成。

分数归一化到 0–100；入场阈值 70，离场阈值 40，每日最多 30 个候选，最终最多 10 个持仓，每个行业最多 3 个候选。

## 冻结执行骨架

- 项目 `FactorRankResearchMatrixStrategy` 生成信号矩阵；
- 项目 `build_market_matrix_from_signals` 应用 `entry_delay_bars=1` 和 `exit_delay_bars=1`；
- 项目 `BacktestEngine.simulate_market_matrix` + `MatcherConfig` 撮合；
- `matching=open_t+1`，无杠杆，最大敞口 100%；
- 10 个持仓，最长持有 60 日，无 trailing stop；
- 止损变体：`-12%` 与 `None`；
- 成本变体：
  - 2× 主诊断：commission 0.0004，stamp tax 0.0005，slippage 10 bps；
  - 3× 压力诊断：commission 0.0006，stamp tax 0.0005，slippage 15 bps；
  - 5× warning-only：commission 0.0010，stamp tax 0.0005，slippage 25 bps。
- 初始资金 50,000,000；等权仓位。合成价格波动后仍给 300 只对照股留足一手粒度，避免资金不足把等权对照长期压到低仓位。
- 只收集统计量，不落盘完整合成价格、权益曲线或逐笔交易。

## 同路径等权对照

每条情景/相关性/路径先生成同一份 300 只股票数据，然后用项目矩阵引擎买入并持有全部 300 只股票至评价期末。对照使用同一成本档位。策略与对照对齐评价窗口和成本，计算同路径 paired excess return。

## 全面组合矩阵

`4 情景 × 2 相关性 × 6 信号 × 2 止损 × 3 成本 × 100 路径 = 28,800` 个策略路径观测；另有 `4 × 2 × 3 × 100 = 2,400` 个同路径等权观测。

每个 cell（情景/相关性/信号/止损/成本）必须恰好有 100 条路径。JSON 保存每个 cell 的 p05/p50/p95、跑赢等权比例、paired excess 的 sign-test p 值、Sharpe、MDD、换手和交易数；不保存原始路径数组。

## 判定与输出

- 结论只允许 `diagnostic_only`。
- 必须完成的 gate：
  - 所有 288 个 cell 完整；
  - 每个 cell 100 条路径；
  - 6 种信号和 4 种情景全部出现；
  - 2×/3×/5× 成本全部完成；
  - 止损/不止损全部完成；
  - 同路径等权对照完成；
  - 无杠杆且最大敞口不超过 100%。
- 成本单调性、止损差异、共同市场相关性差异只作为诊断统计，不作为准入结论。
- 结果通过 `publish_research_artifacts` 不可变发布 JSON/Markdown + sidecar，并写 ResearchLedger。
- Markdown 必须说明：这不是真实历史业绩；不能验证 13 因子 alpha；随机空对照偏离 0 时只能说明执行骨架/选择偏差，不能说明真实因子有效。

## 证伪检查

- 同 seed 重放必须 byte-identical；不同 path 必须不同。
- OHLC 必须满足 `low <= min(open, close)`、`high >= max(open, close)`，价格和成交量必须为正。
- 修改未来价格不得改变过去分数。
- 信号 t 只能在 t+1 开盘成交。
- 2×/3×/5× 参数必须严格符合冻结成本表。
- 项目矩阵策略路径与对照路径都必须来自当前项目引擎。
