# R458 情绪条件因子收益

| 情绪指标 | 股票袖 | 20日高-低IC | 20日p | 60日高-低IC | 60日p |
| --- | --- | ---: | ---: | ---: | ---: |
| market_emotion_score | value_quality_54 | 0.06364362 | 0.02937576 | 0.02857138 | 0.87053803 |
| market_emotion_score | idio_vol_40_low | 0.06493323 | 0.0065586 | -0.06309036 | 0.0098825 |
| market_profit_score | value_quality_54 | 0.07161606 | 0.00603857 | 0.08784658 | 0.22064317 |
| market_profit_score | idio_vol_40_low | 0.09997259 | 7.48e-06 | 0.05871828 | 0.42693378 |
| market_speculation_score | value_quality_54 | 0.055653 | 0.08787086 | 0.04535931 | 0.79660898 |
| market_speculation_score | idio_vol_40_low | 0.10200507 | 2.42e-06 | 0.03362194 | 0.28298571 |
| market_breadth_above_ma20 | value_quality_54 | 0.04505744 | 0.03126528 | 0.00037984 | 0.99514637 |
| market_breadth_above_ma20 | idio_vol_40_low | 0.00206754 | 0.7512783 | -0.07873029 | 0.02182375 |

## 结论

只有一个条件单元同时通过：在市场赚钱效应处于扩张历史高位时，40 日残差低波在 20 日持有期的信号显著更强。其余情绪指标没有通过多重检验后的完整门槛；60 日层面也没有稳定一致方向。因此情绪目前只能作为条件变量继续验证，不能直接当择时开关。

## 边界

- This is a conditional return audit, not a portfolio backtest.
- Stock returns are cross-sectionally rank-scored and equal weighted within the eligible universe.
- Emotion terciles are computed from expanding history only.
- Current HS300 membership and current industry classification retain survivorship bias.
- No leverage and no production activation.
