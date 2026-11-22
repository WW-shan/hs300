# R491 R490 五腿权重质量审计

本轮固定 R490 的 5 条腿与执行参数，只用训练+验证 IC 生成固定权重。

- 样本数：4
- 训练+验证合格：4
- 选中：`equal`
- 判定：**拒绝**
- 检验年化：16.10%；Sharpe：1.150；MDD：-12.76%
- 近一年年化：9.72%；Sharpe：0.740；MDD：-11.49%
- 检验相对 HS300：+16.28%
- 近一年相对 HS300：+19.39%
- 检验 p05：-6.18%
- 近一年 p05：-10.48%

## 门槛

- training_platform_exists: 通过
- test_annual_min: 通过
- test_sharpe_min: 通过
- test_mdd_min: 通过
- recent_annual_min: 通过
- stress_3x_positive: 通过
- test_bootstrap_positive: 未过
- recent_bootstrap_positive: 未过
- test_excess_hs300_positive: 通过
- no_leverage: 通过

## 边界

- IC weighting is estimated from historical data and is not guaranteed to persist.
- All variants share the same five legs and are correlated.
- Test/recent windows are reused confirmation windows.
- Current HS300 membership retains survivorship bias.
- 5x cost is warning-only and never a hard rejection.
- No leverage and no production activation.
