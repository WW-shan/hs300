# R465 R464 尾部诊断

- 样本：`lhs_044` / seven_vq

| 窗口 | 最差月 | 收益 | 负月数 | 正尾 p05 | 中位 p50 |
| --- | --- | ---: | ---: | ---: | ---: |
| validation | 2024-08 | -4.67% | 6/12 | -6.35% | 23.25% |
| test | 2026-06 | -8.84% | 9/19 | -11.41% | 12.04% |
| recent | 2026-06 | -10.44% | 9/14 | -17.20% | 1.52% |

## Bootstrap 块长敏感性

| 窗口 | block=10 p05 | block=20 p05 | block=40 p05 |
| --- | ---: | ---: | ---: |
| validation | -9.87% | -6.35% | -3.10% |
| test | -13.82% | -11.41% | -8.68% |
| recent | -20.93% | -17.20% | -14.89% |

## 结论

这是描述性尾部诊断，不重新选参，也不改变 R464 的拒绝结论。若三个块长下 p05 均偏负，说明问题不是单一块长设置，而是收益分布本身的下尾；下一轮应做独立的风险预算/尾部控制，而不是继续在同一组样本上挑参数。

## 边界

- Descriptive diagnosis only; it cannot turn a rejected candidate into a passing strategy.
- Bootstrap block sensitivity is not a new holdout.
- Current HS300 membership applied to history retains survivorship bias.
- No leverage and no order/paper target.
