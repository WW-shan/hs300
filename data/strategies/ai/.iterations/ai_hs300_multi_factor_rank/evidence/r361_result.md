# R361 · 弱市条件下的日内反转质量腿

规则冻结：弱市条件读取 T-1 state；T 日按 B 策略评分，T+1 开盘成交。两组持仓数、筛选、20 日持有、-20%止损一致。
2x 候选年化 29.13%、Sharpe 0.947、MDD -32.07%；对照年化 38.14%、Sharpe 1.192、MDD -23.44%。
候选近期一年 15.20%；门槛通过 4/8；selected=null。
非独立样本，弱市 regime 与情绪变量仍有机械依赖；无杠杆、无纸面目标、无订单、生产未启用。

证据：`r361_intraday_reversal_preflight.json`、`r361_intraday_reversal_backtest.json`、`r361_final_status.json`。
