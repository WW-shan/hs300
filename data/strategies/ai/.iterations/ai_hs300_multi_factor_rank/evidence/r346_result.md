# R346 结果：A/B Top20 缩小了集中度但仍未通过

R346 在 R345 基础上只把 B 袖也从 Top10 扩到 Top20；A/C、黄金规则、C 帽、overlay、成本、窗口和门槛全部不变。

## 结果

| 指标 | 2× | 5× |
|---|---:|---:|
| 完整路径收益 | +59.58% | +51.19% |
| 年化 | +11.39% | +10.01% |
| Sharpe | 1.2347 | 1.0975 |
| MDD | -9.11% | -9.69% |
| 近一年收益 | +4.88% | +3.38% |

- 2022 扩展段：+0.78%，MDD -6.42%。
- 扩展前段：+9.16%，Sharpe 1.2466，MDD -6.42%。
- overlay 9/9、joint 3/3、资产替换 3/3。
- 2× top1/top3/top5：10.36% / 29.62% / 46.34%。

## 失败门槛

5× top1/top3/top5 为 **18.67% / 52.14% / 80.67%**；C2、C3 通过，C4（top5 <=75%）仍失败，cap plateau 仍为 **0/4**。

fresh-start 仅 **4/6** 为正：2025-11-05 为 -1.95%，2026-01-05 为 -0.31%，因此 F1 也失败。R346 比 R344 的 5× top5 从 115.12% 改善到 80.67%，但代价是 A/B 双腿被稀释、近端稳定性下降，仍不能作为候选。

## 执行勘误

首轮 R346 在进入回测前因预注册缺 `strategy_sha256` 失败，未产生任何收益曲线或交易。失败记录保留在 `evidence/r346_execution_attempt1_failure.json`；A1 只补充已有策略哈希，不改变窗口、构造或门槛，见 `evidence/r346_preregistration_amendment_A1.json`。

`selected=null`，状态 `no_candidate_passed`。证据：`evidence/r346_ab_top20_extended.json`、`evidence/r346_ab_top20_preregistration.json`。
