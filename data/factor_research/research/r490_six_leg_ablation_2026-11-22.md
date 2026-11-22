# R490 R489 六腿消融

本轮逐个剔除 R489 的 6 条腿，检验每条腿对收益/尾部的作用。

- 样本数：6
- 训练+验证合格：4
- 选中：`ext_alt_hs300_idio_vol_40d_low`（去掉该腿）
- 判定：**拒绝**
- 检验年化：16.10%；Sharpe：1.150；MDD：-12.76%
- 近一年年化：9.72%；Sharpe：0.740；MDD：-11.49%
- 检验相对 HS300：+16.28%
- 近一年相对 HS300：+19.39%
- 检验 p05：-5.92%
- 近一年 p05：-10.65%

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

- Ablation identifies dependence inside R489, not causal alpha.
- All 5-leg samples share most members and are not independent trials.
- Test/recent windows are reused confirmation windows.
- Current HS300 membership retains survivorship bias.
- 5x cost is warning-only and never a hard rejection.
- No leverage and no production activation.
