# R467 风险预算退化修正

R466 中 4 个 sleeve、25% 权重上限会让四种风险预算方法退化成等权；本轮保留原证据，排除这种退化样本后重新确认。

- 原样本：160
- 退化样本：34
- 非退化且训练+验证合格：123
- 验证 bootstrap 正尾：0
- 邻域稳定：0
- 选中：`compact4|min_variance|lb60|rb60|cap0.40`
- 判定：**拒绝**
- 检验年化：4.43%
- 检验 Sharpe：0.479
- 检验 MDD：-11.73%
- 近一年年化：-2.93%
- 检验 p05：-9.25%
- 近一年 p05：-14.47%

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
- lookback：60
- rebalance：60
- max_weight：0.40
- 不同权重向量：13
- 3× 检验年化：2.92%

## 边界

- This is a correction audit, not a new factor or strategy.
- R466 evidence is preserved and not overwritten.
- Sleeve curves retain current HS300 survivorship bias.
- Dynamic weights use trailing returns only.
- 5x cost is warning-only and never a hard rejection.
- No leverage and no production activation.
