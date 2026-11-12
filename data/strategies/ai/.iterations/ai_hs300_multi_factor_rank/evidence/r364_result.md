# R364 · 弱市收盘位置反转消融

规则冻结：弱市条件读取 T-1 state；T 日按 B 策略评分，T+1 开盘成交。两组持仓数、筛选、5 日持有、-20%止损一致；候选与对照只相差固定 20pp 质量价值→收盘位置反转单因子替换。
2x 候选年化 10.53%、Sharpe 0.529、MDD -26.48%；对照年化 8.72%、Sharpe 0.457、MDD -25.89%。
相对 R363 5 日双因子复合候选，单独收盘位置因子的 2x 年化/Sharpe/MDD 变化 -1.76%/-0.050/-3.15%。
候选近期一年 -8.88%；门槛通过 6/9；selected=null。
非独立样本，弱市 regime 与情绪变量仍有机械依赖；无杠杆、无纸面目标、无订单、生产未启用。

证据：`r364_weak_close_reversal_preflight.json`、`r364_weak_close_reversal_backtest.json`、`r364_final_status.json`。
