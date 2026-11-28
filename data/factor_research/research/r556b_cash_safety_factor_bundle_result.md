# R556 R550 因子库对账

> `statistical_screen`：只核对可复用因子与 IC，不声明组合收益或生产可用。

## 因子

- 主因子：`uf_r550_cash_safety_rank`
- 覆盖率：90.84%

## IC

| 口径 | 全周期 | 训练 | 验证 | 近期 |
| --- | ---: | ---: | ---: | ---: |
| 原始 | 0.0593 | 0.0842 | -0.0225 | 0.0557 |
| 行业+规模中性 | 0.0429 | 0.0587 | -0.0040 | 0.0369 |

## 门槛

- all_definitions_loaded: 通过
- coverage_min: 通过
- full_ic_min: 通过
- train_ic_min: 通过
- validation_ic_positive: 未过
- recent_ic_positive: 通过
- neutral_full_ic_min: 通过

## 结论

rejected

## 边界

- R556 is a factor-only statistical reconciliation and makes no portfolio-return claim.
- The reusable rank composite preserves the source factor signs but is not numerically identical to R531's internal composite.
- A negative validation IC blocks factor-set promotion even if full and recent IC are positive.
- No active version, orders, paper targets or production enablement are produced.
