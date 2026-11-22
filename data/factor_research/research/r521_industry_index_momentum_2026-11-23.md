# R521 申万行业指数动量与参数蒙特卡洛

- 参数样本：24
- 行业数：28
- 选中：`risk_adjusted_momentum_60_1`
- 判定：**拒绝**

## 选中参数

- 回看：60 日
- 打分：risk_adjusted_momentum
- 持仓：1
- 训练年化：1.99%
- 验证年化：-7.25%
- 检验年化：-9.97%
- 检验 Sharpe：-0.082
- 检验 MDD：-41.80%
- 近一年年化：-7.34%
- 检验 p05：-63.06%
- 近一年 p05：-60.92%

## 门槛

- train_annual_min: 未过
- validation_annual_min: 未过
- test_annual_min: 未过
- test_sharpe_min: 未过
- test_mdd_min: 未过
- recent_annual_min: 未过
- recent_total_p05_positive: 未过
- test_total_p05_positive: 未过
- stress_3x_positive: 未过
- parameter_plateau: 未过
- fdr_q_max: 未过

## 边界

- Industry-index returns are not directly tradable without validated fund mapping.
- R520 industry index history does not prove historical stock industry membership.
- The test and recent windows have been observed in prior research.
- Costs are 2x/3x/5x the local stock 1x round-trip estimate; the index ETF implementation would add tracking and fund-specific costs not modeled here.
- Monthly month-end signals and next-open execution simplify intraday execution and liquidity.
- The 5x cost result is warning-only, not a hard gate.
- No leverage and no production activation.
