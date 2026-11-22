# R456 PB+OCF 紧凑组合验证

| 变体 | 训练年化 | 验证年化 | 检验年化 | 检验Sharpe | 检验MDD | 近一年年化 | 检验p05 | 近一年p05 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| compact_only | 7.85% | 16.43% | - | - | - | - | - | - |
| four | 18.39% | 21.98% | - | - | - | - | - | - |
| four_plus_compact_10 | 17.78% | 26.52% | - | - | - | - | - | - |
| four_plus_compact_20 | 15.08% | 23.20% | - | - | - | - | - | - |

- 选中：`four_plus_compact_20`，10 仓 / 30 日
- 判定：**拒绝**
- 检验年化：3.34%
- 检验 Sharpe：0.320
- 检验 MDD：-14.57%
- 近一年年化：1.79%
- 检验 p05：-17.58%
- 近一年 p05：-18.22%

## 门槛

- train_annual_min: 通过
- validation_annual_min: 通过
- test_annual_min: 通过
- test_sharpe_min: 未过
- test_mdd_min: 通过
- recent_annual_min: 通过
- stress_3x_positive: 通过
- test_total_p05_positive: 未过
- recent_total_p05_positive: 未过
- increment_vs_four_test_positive: 通过
- increment_vs_four_recent_positive: 未过
- no_leverage: 通过

## 结论

紧凑的 PB+OCF 两腿组合保留了训练和验证信号，加入四因子后检验段有正增量，但近一年增量转负，检验 Sharpe 和两条 bootstrap 尾部都未过。这个结构适合继续做风险层研究，不作为可执行候选。

## 边界

- The compact composite uses only two financial legs selected from an already-observed ablation.
- Current HS300 membership applied to history retains survivorship bias.
- Industry classification is a current snapshot.
- Financial PIT is builder-audited; announcement ambiguity uses statutory fallback.
- No leverage; test/recent are confirmation only.
- A passing candidate remains watch-only, not active.
