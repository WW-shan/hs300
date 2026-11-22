# R478 因子库 bootstrap 尾部容忍度审计

本轮只审计 R476 已冻结的验证期 bootstrap 尾部，不放宽主门槛，也不使用检验/近一年重新选样。

- 审计样本：5
- 正尾：0
- 近零尾（≥ -1.00%）：2
- 负尾：3

## 分类

| 样本 | 候选权重 | 验证 p05 | 分类 |
| --- | ---: | ---: | --- |
| `base_plus_inventory_growth_minus_cogs_growth_at_10` | 10% | -0.46% | near_zero |
| `base_plus_inventory_growth_minus_cogs_growth_at_20` | 20% | -0.83% | near_zero |
| `base_plus_value_quality_safety_at_10` | 10% | -6.79% | negative |
| `base_plus_value_quality_safety_at_20` | 20% | -9.11% | negative |
| `base_plus_value_quality_safety_at_30` | 30% | -14.29% | negative |

## 结论

- “近零”只是更准确的尾部描述，不等同于通过 bootstrap，也不改变 R476 的拒绝状态。
- `active_version` 仍为 null，不生成订单或生产配置。

## 边界

- The tolerance is a diagnostic label, not a relaxed admission gate.
- The audit reuses R476 train+validation selections.
- Test and recent windows are not used to choose any parameter.
- R476 remains rejected regardless of this audit.
- No leverage and no production activation.
