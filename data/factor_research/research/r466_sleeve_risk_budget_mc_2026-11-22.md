# R466 因子袖风险预算蒙特卡洛

本轮在 R464 因子袖和 R465 尾部诊断之后，检验 sleeve 层等权、逆波动、ERC、最小方差风险预算能否改善尾部；训练+验证只选样，检验+近一年只确认。

- 单因子袖：9 个
- 组合样本：160
- 训练+验证合格：157
- 验证 bootstrap 正尾：0
- 邻域稳定：0
- 选中：`compact4|min_variance|lb20|rb20|cap0.25`
- 判定：**拒绝**
- 检验年化：5.06%
- 检验 Sharpe：0.513
- 检验 MDD：-14.31%
- 近一年年化：-2.49%
- 检验 p05：-11.40%
- 近一年 p05：-16.99%

## 门槛

- training_platform_exists: 通过
- validation_bootstrap_positive: 未过
- neighborhood_platform_pass: 未过
- test_annual_min: 通过
- test_sharpe_min: 通过
- test_mdd_min: 通过
- recent_annual_min: 未过
- stress_3x_positive: 通过
- test_total_p05_positive: 未过
- recent_total_p05_positive: 未过
- no_leverage: 通过

## 选中规则

- universe：compact4
- method：min_variance
- lookback：20
- rebalance：20
- max_weight：0.25
- 3× 检验年化：3.47%
- 5× warning 检验年化：0.34%

## 边界

- This is a research mix of separately backtested sleeves, not a broker-level allocation.
- Sleeve curves are generated from current HS300 membership and retain survivorship bias.
- Dynamic risk weights use trailing returns only; no future returns are used.
- The test and recent windows have been observed before.
- 5x cost is warning-only and never a hard rejection.
- No leverage and no production activation.
