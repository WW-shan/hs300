# R505 R499 单日建仓上限审计

- 选中：`cap_2`
- 判定：**拒绝**
- 检验年化：18.07%；Sharpe：1.380；MDD：-10.19%
- 近一年年化：17.14%；Sharpe：1.260；MDD：-9.78%
- 检验 p05：1.52%
- 近一年 p05：-4.77%

## 门槛

- training_platform_exists: 通过
- validation_bootstrap_positive: 未过
- test_annual_min: 通过
- test_sharpe_min: 通过
- test_mdd_min: 通过
- recent_annual_min: 通过
- stress_3x_positive: 通过
- test_bootstrap_positive: 通过
- recent_bootstrap_positive: 未过
- test_excess_hs300_positive: 通过
- no_leverage: 通过

## 边界

- A daily cap changes deployment timing, not factor ranking.
- Cap variants are correlated and are not independent trials.
- The test/recent windows have been observed before.
- Current HS300 membership retains survivorship bias.
- 5x cost is warning-only and never a hard rejection.
- No leverage and no production activation.
