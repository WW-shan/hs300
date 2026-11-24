# R535A 全参数族普查与训练期参数筛选

## 规模

- 参数族：85
- 参数点：472
- 结论计数：{'data_gap': 8, 'rejected': 77}

## 研究准入

| 家族 | 数据类 | 参数 | 训练 IC | validation IC | recent IC | q | 训练 block20 CI low |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| — | — | — | — | — | — | — | — |

## 数据缺口

- bond_futures_momentum
- bond_futures_trend_momentum
- etf_share_change
- hscei_momentum
- hsi_momentum
- hsi_relative_hs300
- market_skew
- qvix_zscore

## 结论

- 总体结论：rejected
- 准入家族：无

## 边界

- All parameter choices use training-period evidence only; validation/recent are confirmation windows.
- Block 10/20/40 are sensitivity checks; only block 20 CI is a hard significance gate.
- R526 membership and industry vintages are reconstructed research inputs.
- A statistical admission is not a portfolio return or production authorization.
- R535 attempt 1 failed before publication because the runner referenced a non-exported stats module; R535A is the explicit amendment.
- No leverage, orders, paper targets or active version are produced.
