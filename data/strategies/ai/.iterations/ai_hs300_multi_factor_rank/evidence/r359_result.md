# R359 · C 袖三 cohort 错峰集成

## 规则

固定 3 个相同 C 子袖，分别在每个测试相位的第 0、20、40 个交易日启动。每个子袖独立使用原 C 策略、50 仓、60 日持有、T+1 开盘成交和对应成本；未启动资金持有现金；各子袖初始资本固定各占 1/3，不做 cohort 间再平衡。A/B、风险平价、C 10% 上限、15% 波动目标、黄金防守桶均沿用 R344。

## 结果

- 2×：年化 11.96%，Sharpe 1.2862，MDD -9.09%；2022 -0.15%，近一年 5.20%。
- 5×：年化 10.53%，Sharpe 1.1462，MDD -9.52%；top1/top3/top5 38.48%/84.53%/117.08%，股票数 331。
- 6 个 fresh starts 正收益 6/6；通过门槛 15/20，最终 `selected=None`。
- 无杠杆，无纸面目标，无订单，生产未启用。

## 证据

- Backtest: `r359_cohort_ensemble_backtest.json` SHA-256 `256568e9f98fdbc604e83a533b40463a70c8ba01ec7909bddb97ca054c961a23`
- Preregistration: `r359_cohort_ensemble_preregistration.json` SHA-256 `4c63a23980254eed5fbf40608f166e498a7a4987d3fe4035ef8fc382c708645a`
- Preflight: `r359_cohort_preflight_v14.json` SHA-256 `5379be60b0575ca203273166dec9e56c459db26290a38f2c4d2fdd0a537b481d`
