# R533A 正式回测签名修正 Amendment

## 背景

R533 在正式运行的首个训练候选被 `require_formal_evidence` 拦截：`lookahead=insufficient`、`execution_timing=insufficient`。没有发布 R533 result JSON/Markdown，也没有查看验证或近一年收益。

## 根因

`prepare_matrix_optimization` 的 prepared signature 保存的是调用方请求的 460 只 R526 历史成员并集；但 prepared market matrix 只保留本地有行情轴的 298 只。R533 的 `build_formal_config` 错用了过滤后的 298 只市场轴覆盖 `config.symbols`，导致：

- prepared signature 与 run signature 不一致；
- 引擎返回 `共享基础矩阵与当前回测配置不匹配`；
- 交易数为 0；
- 证据审计无法形成 lookahead / execution timing 比较样本。

## 修正

R533A 新增 `config_symbols_for_prepared(prepared)`，从 `prepared.signature[1]` 原样复制请求股票池，不再使用 `prepared.market_data.symbols` 覆盖运行配置。

回归测试：

`test_config_symbols_follow_prepared_requested_universe_not_filtered_market_axes`

## 不变项

- 六个因子、方向和参数不变；
- 训练/验证/近一年窗口不变；
- 9 组执行参数网格不变；
- 5000 次 block bootstrap 和 seed 不变；
- v2 门禁、2×主成本、3×压力、5×仅 warning 不变；
- R533 原预注册、runner hash 和失败 backtest run 原样保留。

## 研究身份

修正后的预注册使用 `R533A` 和新文件名，不覆盖 R533。失败记录：

`data/factor_research/research/r533_attempt1_signature_mismatch_failure.json`
