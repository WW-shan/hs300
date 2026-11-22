# R512 成长加速度显著性修正

| 复合因子 | 周期 | 原始IC | 中性IC | NW p | q | 年同号 | 有效日期 | 门槛 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `growth_acceleration` | 5 | 0.00532184 | 0.00744924 | 0.08200002 | 0.47555139 | 0.8 | 1088 | 拒绝 |
| `growth_acceleration` | 20 | 0.00948001 | 0.01161136 | 0.15851713 | 0.47555139 | 0.8 | 1073 | 拒绝 |
| `growth_acceleration` | 60 | 0.01606745 | -0.0057785 | 0.6432801 | 0.9776095 | 0.6 | 1033 | 拒绝 |
| `quality_confirmed_growth` | 5 | -0.00109146 | 0.00227729 | 0.66475658 | 0.9776095 | 0.4 | 1088 | 拒绝 |
| `quality_confirmed_growth` | 20 | -0.00298208 | 0.00243743 | 0.83444411 | 0.9776095 | 0.4 | 1073 | 拒绝 |
| `quality_confirmed_growth` | 60 | -0.00938542 | -0.00054108 | 0.9776095 | 0.9776095 | 0.8 | 1033 | 拒绝 |

## 边界

- R511 remains unchanged and is the source of the composite definitions.
- Approximately 78% of financial rows use statutory-deadline fallback dates.
- This is an IC audit, not a portfolio backtest.
- No leverage and no production activation.
