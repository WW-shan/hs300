# R545 稳定 7 因子风险预算与动态敞口

> 固定因子与执行参数，只改变无杠杆 budget_scale；recent 已观察。

## 选中风险政策

- 变体：`{'id': 'vol_10', 'kind': 'vol_target', 'target': 0.1, 'lookback': 60, 'floor': 0.5, 'order': 6}`
- Full 2×：年化 9.60%，Sharpe 1.02，MDD -11.26%，交易 198
- Recent 2×：年化 0.55%，Sharpe 0.10，MDD -13.73%，交易 54
- Validation 2×：年化 5.78%，Sharpe 0.50，MDD -5.51%

## 与 R543 对照

- Full 年化差：-3.79%
- Recent 收益差：-0.97%
- Full MDD 差：+5.14%

## 训练期变体

| 变体 | 年化 | Sharpe | MDD | 交易数 | 资格 |
| --- | ---: | ---: | ---: | ---: | --- |
| control | 22.69% | 1.46 | -10.01% | 117 | True |
| cash_80 | 18.05% | 1.44 | -8.34% | 117 | True |
| cash_60 | 13.33% | 1.42 | -6.48% | 117 | True |
| cash_40 | 8.79% | 1.40 | -4.44% | 117 | False |
| dd_10 | 22.69% | 1.46 | -10.01% | 117 | True |
| dd_15 | 22.69% | 1.46 | -10.01% | 117 | True |
| vol_10 | 16.19% | 1.51 | -6.15% | 117 | True |
| vol_12 | 18.28% | 1.51 | -6.79% | 117 | True |
| vol_12_dd_10 | 18.28% | 1.51 | -6.79% | 117 | True |

## 确认成本

| 成本 | Full 年化 | Full Sharpe | Full MDD | Recent 总收益 |
| --- | ---: | ---: | ---: | ---: |
| 2x | 9.60% | 1.02 | -11.26% | 0.57% |
| 3x | 9.12% | 0.97 | -11.45% | 0.06% |
| 5x | 8.21% | 0.88 | -11.69% | -1.20% |

## Fresh starts

| 起始日 | 总收益 | 年化 | Sharpe | MDD |
| --- | ---: | ---: | ---: | ---: |
| 2022-05-05 | 48.87% | 9.60% | 1.02 | -11.26% |
| 2023-01-03 | 47.41% | 11.10% | 1.06 | -11.73% |
| 2024-01-02 | 42.84% | 13.97% | 1.20 | -13.63% |
| 2025-01-02 | 7.49% | 4.18% | 0.41 | -14.26% |
| 2025-10-14 | 0.57% | 0.55% | 0.10 | -13.73% |
| 2026-01-05 | -4.84% | -6.00% | -0.52 | -14.31% |

## 门槛

- Core：False
- 3× stress：True
- Fresh-start：{'positive_count': 5, 'median': 0.25165, 'pass': True}
- 5× warnings：['negative_5x_recent']
- 失败项：['full_annual_min']
- 结论：`rejected`

## Bootstrap

- Full 2×：{'total_p05': 0.14405984922563664, 'total_p50': 0.4842340118628716, 'sharpe_p50': 1.0149131810959506, 'mdd_p05': -0.1488007282392284}
- Recent 2×：{'total_p05': -0.1480441232678158, 'total_p50': 0.0037582851700146414, 'sharpe_p50': 0.0881482444311213, 'mdd_p05': -0.19812761297206105}

## 边界

- R543 recent-year results were already observed; R545 recent is not independent OOS.
- Risk-budget variants are selected only on training-period metrics.
- Volatility targeting uses only the causal selected-candidate recent-return window.
- The PIT equal-weight benchmark is a costless diagnostic approximation.
- Five-times cost is warning-only and no leverage is used.
- No active version, orders, paper targets or production enablement are produced.
