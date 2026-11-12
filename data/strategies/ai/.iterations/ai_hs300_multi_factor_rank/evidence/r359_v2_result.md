# R359 · C 袖三 cohort 错峰集成

## 规则

固定 3 个相同 C 子袖，分别在每个测试相位的第 0、20、40 个交易日启动。每个子袖独立使用原 C 策略、50 仓、60 日持有、T+1 开盘成交和对应成本；未启动资金持有现金；各子袖初始资本固定各占 1/3，不做 cohort 间再平衡。A/B、风险平价、C 10% 上限、15% 波动目标、黄金防守桶均沿用 R344。

## 结果

- 2×：年化 11.67%，Sharpe 1.2585，MDD -9.10%；2022 -0.16%，近一年 4.13%。
- 5×：年化 10.22%，Sharpe 1.1170，MDD -9.61%；top1/top3/top5 44.46%/96.40%/132.50%，股票数 304。
- 6 个 fresh starts 正收益 4/6；通过门槛 14/20，最终 `selected=None`。
- 无杠杆，无纸面目标，无订单，生产未启用。

## 证据

- Backtest: `r359_v2_cohort_ensemble_backtest.json` SHA-256 `2dc6c3c9171291ff94b573b8533d84d33e7a6dfd612297d02883256f982f5ef5`
- Preregistration: `r359_v2_preregistration.json` SHA-256 `9396adfabe68a8b243399bf85b9d215bdc02a38f49fc917b091723f3999ec7b9`
- Preflight: `r359_v2_preflight.json` SHA-256 `8de9a37dbc85fca53c2151266880bc62cfc0a09f30efe90868e3f52a1e8b7519`
