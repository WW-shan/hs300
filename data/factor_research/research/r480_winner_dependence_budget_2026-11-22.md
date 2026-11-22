# R480 赢家依赖预算与 8 腿子集

本轮从 R476 的 9 腿库中预注册 4 个 8 腿子集，用训练+验证选择，检验+近一年只确认；沪深300 使用 `000300.SH`。

- 样本数：4
- 训练+验证合格：2
- 选中：`core8_no_ocf_margin`
- 判定：**拒绝**
- 检验年化：6.16%；Sharpe：0.490；MDD：-16.56%
- 近一年年化：7.89%；Sharpe：0.620；MDD：-10.19%
- 检验相对 HS300：+1.09%
- 近一年相对 HS300：+17.49%
- 检验 bootstrap p05：-14.32%
- 近一年 bootstrap p05：-8.88%
- 训练/验证 top-5 正收益占比：25.13% / 27.82%
- 检验/近一年 top-5 正收益占比：23.41% / 24.50%

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

- The four subsets remove one leg each; they are not an exhaustive combinatorial search.
- Test and recent windows are reused confirmation windows.
- Current HS300 membership retains survivorship bias.
- Current industry snapshot is not historical vintage.
- 5x cost is warning-only and never a hard rejection.
- No leverage and no production activation.
