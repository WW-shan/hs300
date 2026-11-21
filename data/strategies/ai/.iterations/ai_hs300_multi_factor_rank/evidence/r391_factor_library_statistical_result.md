# R391 · 因子库统计增量筛查

R391 只计算候选与 core_v1 的相关性和 80/20 边际 IC，不做组合回测。

- 候选数：114
- statistical_pass：11
- redundant：0
- statistical_rejected：103
- data_unavailable：0

## 结论边界

- statistical_pass 只代表可以进入下一阶段组合 MC，不代表策略有效。
- 不生成订单、纸面目标或生产配置。
- 5× 仅 warning，不硬否决。
