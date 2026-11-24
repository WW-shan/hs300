# R530 统一 PIT 因子库蒙特卡洛复核

## 因子库规模

- 因子：235
- 参数族：85
- 历史参数点：472
- 冻结候选：15

## 候选结论

| 家族 | 参数 | 数据类 | full IC | validation IC | recent IC | q | bootstrap 10/20/40 | 结论 |
| --- | ---: | --- | ---: | ---: | ---: | ---: | --- | --- |
| cs_spread | 60 | price | 0.027429 | 0.024189 | 0.032404 | 1.002e-05 | 0.0147/0.0137/0.0149 | admitted_for_research |
| idio_vol | 20 | price | 0.03084 | 0.030024 | 0.041917 | 7e-08 | 0.0195/0.0179/0.0184 | admitted_for_research |
| vol_of_vol | 20 | price | 0.025722 | 0.017775 | 0.032002 | 3.9e-06 | 0.0144/0.0143/0.0148 | admitted_for_research |
| amihud | 120 | price | 0.007739 | -0.006515 | 0.003487 | 0.12279667 | -0.0021/-0.0034/-0.0038 | rejected |
| amount_level | 5 | price | 0.015727 | 0.004172 | 0.022301 | 0.01071863 | 0.0028/0.0013/0.0002 | admitted_for_research |
| annual_volatility | 10 | price | 0.027966 | 0.027122 | 0.03451 | 7.17e-06 | 0.0154/0.0148/0.0159 | admitted_for_research |
| atr_risk | 5 | price | 0.033282 | 0.030616 | 0.041442 | 1.25e-06 | 0.0196/0.0191/0.0203 | admitted_for_research |
| bollinger_width | 10 | price | 0.019537 | 0.015443 | 0.014325 | 0.00032616 | 0.0093/0.0086/0.0083 | admitted_for_research |
| max_return | 60 | price | 0.036555 | 0.027949 | 0.042663 | 0.0 | 0.0238/0.0230/0.0236 | admitted_for_research |
| turnover_level | 5 | price | 0.019921 | 0.016457 | 0.031654 | 0.00090015 | 0.0080/0.0081/0.0077 | admitted_for_research |
| repurchase_event_decay | 20 | flow | 0.025112 | 0.016813 | -0.001902 | 0.00312292 | 0.0084/0.0086/0.0115 | rejected |
| dividend_yield_absolute | 0.015 | financial | 0.029837 | 0.02383 | 0.044395 | 0.00013112 | 0.0140/0.0142/0.0153 | admitted_for_research |
| dividend_yield_quantile | 0.4 | financial | 0.024221 | 0.023894 | 0.041548 | 0.0006898 | 0.0097/0.0098/0.0105 | admitted_for_research |
| margin_change_quantile | 0.5 | flow | 0.005972 | 0.012145 | 0.01426 | 0.07650477 | -0.0006/-0.0009/-0.0004 | rejected |
| repurchase_holding | 20.0 | flow | 0.024753 | 0.017462 | -0.002103 | 0.00375646 | 0.0077/0.0076/0.0107 | rejected |

## 维度能力

| 数据类 | 候选 | 研究准入 | 拒绝 |
| --- | ---: | ---: | ---: |
| financial | 2 | 2 | 0 |
| flow | 3 | 0 | 3 |
| price | 10 | 9 | 1 |

## 结论

- 总体结论：admitted_for_research
- 结论计数：{'admitted_for_research': 11, 'rejected': 4}

## 边界

- Frozen historical recommendations are revalidated, not newly discovered.
- The R526 membership and industry vintages are reconstructed research inputs.
- Statistical admission is not portfolio performance evidence.
- No leverage, orders, paper targets or active version are produced.
