# R452 财报价值/质量/安全复合因子

| 复合因子 | 5日中性IC | 20日中性IC | 60日中性IC | 相对四因子60日增量 | 60日NW p | 60日q | 全部门槛 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `value_quality` | 0.026516 | 0.041048 | 0.072524 | 0.005473 | 6.197e-05 | 7.968e-05 | pass |
| `value_cash` | 0.025557 | 0.042988 | 0.068321 | 0.002429 | 0.00025081 | 0.00025081 | reject |
| `value_quality_safety` | 0.026566 | 0.041881 | 0.070559 | 0.006653 | 0.00019185 | 0.00021583 | reject |

- 通过候选：value_quality, value_quality_safety

## 结论

`value_quality` 是这次唯一的正向对照：它在 5/20/60 日行业+规模中性 IC、年度同号、近一年方向和相对当前四因子增量上都通过。`value_quality_safety` 虽然 5/20 日通过，但 60 日增量年度同号不足，只保留为候选证据。`value_cash` 的绝对 IC 很强，但没有证明相对现有四因子的边际贡献，因此拒绝。

## 边界

- This is a composite of existing catalog factors, not a new raw data source.
- The current HS300 membership applied to history retains survivorship bias.
- Industry classification is a current snapshot, not historical vintage.
- Financial PIT is verified at the builder level; announcement-source anomalies are addressed by statutory-date fallback.
- No leverage and no portfolio/order promotion.
