# R477 冻结因子库诊断

本轮不重新选样：直接冻结 R476 的 9 腿组合和参数，只解释其检验/近一年表现为何不能晋级。

- 冻结样本：`base_plus_inventory_growth_minus_cogs_growth_at_20`
- 组合腿数：9
- 检验年化：3.11%；Sharpe：0.290；MDD：-20.08%
- 近一年年化：11.10%；Sharpe：0.820；MDD：-11.71%
- 检验相对基准：10.31%（策略 4.56%）
- 近一年相对基准：-5.36%（策略 11.43%）
- 检验交易数：107；正/负：54/53
- 总盈亏：45607.95；去掉最大 1/5/10 笔后：26292.76/-39116.34/-90416.96

## 检验期逐年收益

- 2025: 2.86%
- 2026: 1.65%

## 近一年逐年收益

- 2025: 1.33%
- 2026: 9.97%

## 门槛与结论

- 本轮只做冻结样本诊断，不产生新的 watch 候选。
- R476 仍为 rejected；`active_version` 保持 null。

## 边界

- The R476 sample was selected on train+validation; this round does not select a new strategy.
- Test and recent windows are reused confirmation windows, not fresh holdouts.
- Current HS300 membership retains survivorship bias.
- Diagnostics explain fragility; they do not promote the R476 strategy.
- 5x cost is warning-only and never a hard rejection.
- No leverage and no production activation.
