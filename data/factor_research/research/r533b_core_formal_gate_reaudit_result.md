# R533B 正式门禁字段重审

本轮只修正 `annual_return -> annual`、`max_drawdown -> mdd` 的映射，不重跑、不改参数。

- R533A 原结论：rejected
- 重审结论：rejected
- v2 状态：rejected
- 失败路径：['core_2x.recent_return', 'stress.recent_return']
- 验证期年化：0.0163

## 关键指标

| 项目 | 年化 | Sharpe | MDD | 近一年 |
| --- | ---: | ---: | ---: | ---: |
| 主组合 2x | 0.1433 | 1.03 | -0.1449 | -0.0482 |
| 两因子对照 2x | 0.128 | 0.98 | None | -0.0379 |
| 3x 压力 | 0.1353 | 0.98 | -0.1473 | -0.0558 |
| 5x 诊断 | 0.1197 | 0.88 | -0.1517 | -0.0706 |

## 诊断

- 5× warnings：[]
- 5× 硬否决：False

## 边界

- This is a frozen-result gate-key correction, not a new backtest.
- No factor, execution parameter, window, cost or gate threshold changed.
- R533A original result and rejection remain immutable.
- The corrected decision still depends on the recent-year and stress gates.
- No leverage, orders, paper targets or active version are produced.
