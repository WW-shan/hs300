# R453 财报质量复合因子组合验证

| 变体 | 训练年化 | 验证年化 | 检验年化 | 检验Sharpe | 检验MDD | 近一年年化 | 3×检验年化 | 检验p05 | 近一年p05 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| four | 18.19% | 21.98% | - | - | - | - | - | - | - |
| five | 17.94% | 28.62% | - | - | - | - | - | - | - |
| value_quality_only | 33.78% | 21.84% | - | - | - | - | - | - | - |
| four_plus_value_quality_20 | 17.65% | 5.51% | - | - | - | - | - | - | - |
| five_plus_value_quality_20 | 15.89% | 4.96% | - | - | - | - | - | - | - |
| four_plus_value_quality_10 | 17.01% | 12.39% | - | - | - | - | - | - | - |

- 选中：`four_plus_value_quality_10`，10 仓 / 30 日
- 判定：**拒绝**
- 相对四因子：检验年化 14.24%；近一年 -10.86%

## 门槛

- train_annual_min: 通过
- validation_annual_min: 通过
- test_annual_min: 通过
- test_sharpe_min: 通过
- test_mdd_min: 通过
- recent_annual_min: 未过
- stress_3x_positive: 通过
- test_total_p05_positive: 未过
- recent_total_p05_positive: 未过
- increment_vs_four_test_positive: 通过
- increment_vs_four_recent_positive: 未过
- no_leverage: 通过

## 选中结果

- 检验年化：17.27%
- 检验 Sharpe：1.340
- 检验 MDD：-11.73%
- 检验 p05：-0.94%
- 近一年年化：-1.87%
- 近一年 p05：-22.42%

## 结论

加入 10% 的 `value_quality` 后，检验段年化和 Sharpe 明显高于原四因子，但近一年仍为负；检验 p05 约 -0.94%，近一年 p05 约 -22.42%，尾部没有通过。因此这次只确认了“复合因子有统计信号和中期组合增量”，没有形成可执行策略。

## 边界

- The composite is built from existing catalog factors and does not create new fundamental fields.
- The test and recent windows have been observed in prior rounds.
- Current HS300 membership applied to history retains survivorship bias.
- Industry classification is a current snapshot.
- Financial PIT is builder-audited; announcement-source ambiguity uses statutory fallback.
- 5x cost is warning-only and never a hard rejection.
- A passing portfolio is still only a watch candidate, not an active version.
