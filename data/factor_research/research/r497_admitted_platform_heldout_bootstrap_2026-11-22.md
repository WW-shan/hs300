# R497 R496 冻结样本独立播种 Bootstrap

本轮不重新选样，只对 R496 选中平台用新随机种子重跑绝对与配对超额 bootstrap。

- 冻结样本：`p20_h50_e60_x50_c2`
- 判定：**拒绝**

| 窗口 | 配对天数 | 绝对 p05 | 超额 p05 | 缺失基准日 |
| --- | ---: | ---: | ---: | --- |
| test | 365 | -13.45% | -36.88% | ['2026-10-15'] |
| recent | 257 | -10.77% | -19.14% | ['2026-10-15'] |

## 门槛

- test_absolute_p05_positive: 未过
- recent_absolute_p05_positive: 未过
- test_paired_excess_p05_positive: 未过
- recent_paired_excess_p05_positive: 未过
- sample_unchanged: 通过

## 边界

- This reuses R496 curves and does not create new market data.
- A different bootstrap seed is not an independent holdout.
- R496 remains rejected.
- No leverage and no production activation.
