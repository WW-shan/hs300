# R362 · 弱市条件下的 5 日持有与日内反转

规则冻结：弱市条件读取 T-1 state；T 日按 B 策略评分，T+1 开盘成交。两组持仓数、筛选、5 日持有、-20%止损一致；候选与对照只相差固定 20pp 质量价值→日内反转替换。
2x 候选年化 7.95%、Sharpe 0.423、MDD -25.35%；对照年化 8.72%、Sharpe 0.457、MDD -25.89%。
相对 R361 同策略 20 日上限，候选 2x 年化/Sharpe/MDD 变化 -21.18%/-0.524/+6.72%。
候选近期一年 -13.09%；门槛通过 7/9；selected=null。
非独立样本，弱市 regime 与情绪变量仍有机械依赖；无杠杆、无纸面目标、无订单、生产未启用。

证据：`r362_weak_regime_intraday_5d_preflight.json`、`r362_weak_regime_intraday_5d_backtest.json`、`r362_final_status.json`。
