# R363 · 弱市日内反转与收盘位置复合

规则冻结：弱市条件读取 T-1 state；T 日按 B 策略评分，T+1 开盘成交。两组持仓数、筛选、5 日持有、-20%止损一致；候选与对照只相差固定 20pp 质量价值→日内/收盘位置反转等权复合替换。
2x 候选年化 12.30%、Sharpe 0.579、MDD -23.33%；对照年化 8.72%、Sharpe 0.457、MDD -25.89%。
相对 R362 5 日日内反转单因子候选，复合候选 2x 年化/Sharpe/MDD 变化 +4.35%/+0.156/+2.02%。
候选近期一年 -7.37%；门槛通过 8/9；selected=null。
非独立样本，弱市 regime 与情绪变量仍有机械依赖；无杠杆、无纸面目标、无订单、生产未启用。

证据：`r363_weak_reversal_blend_preflight.json`、`r363_weak_reversal_blend_backtest.json`、`r363_final_status.json`。
