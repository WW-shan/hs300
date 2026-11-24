# R534B Confirmation Endpoint Errata

R534B 的机器可读结果 `metrics.regime_end` 为 `2026-10-14`。

原 `known_limits` 中“regime history ends on 2026-10-21”来自 R534A 的截止日，在改为 R534B 时未同步更新。正确表述应为：

- `regime_history` 原始最后一条为 2026-10-21；
- 2026-10-15 缺失；
- R534B 最后一条完整 T-1 链为 2026-10-14；
- 全部确认收益截止 2026-10-14，不延伸到 2026-11-05。

该文字勘误不修改任何回测数值、结论、预注册或冻结 result；机器口径以 `metrics.regime_end=2026-10-14` 为准。
