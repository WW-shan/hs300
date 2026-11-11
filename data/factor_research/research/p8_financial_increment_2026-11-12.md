# P8 财务因子增量准入报告

- Research ID：`sha256:448cbd37b4faefc94025243be86b496779fcf649273d2e15b1a1835a42874038`
- 生成时间：`2026-11-11T16:42:13Z`
- P7 财务 PIT：`sha256:4d98dbe7ecb185cbb3e9cf14538df6916b19f0b67f184fce9f7a0ec5c501a7d6`
- P5 基线：`sha256:641f0df5849264ea133ea0a8ccb58b057816afd47d916014f0d5baabb93cb6c7`
- 候选数：11
- 统计通过：0
- 组合通过：0
- 决策：`no_statistical_candidate`
- 结论只允许形成 watch，`active_version` 保持 null。

## 候选增量

| 候选 | 方向 | 覆盖率 | 边际 IC | NW p | q | 最大相关 | 统计门槛 | 组合门槛 | 结论 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| `accrual` | low | 0.602012 | -0.001676 | 0.469182 | 0.469182 | 0.470723 | reject | - | statistical_rejected |
| `core_earnings_ratio` | high | 0.559973 | -0.002167 | 0.377692 | 0.469182 | 0.072099 | reject | - | statistical_rejected |
| `debt_ratio_latest` | low | 0.728315 | -0.004226 | 0.353332 | 0.469182 | 0.323070 | reject | - | statistical_rejected |
| `gross_margin_latest` | high | 0.611666 | -0.007183 | 0.085520 | 0.313572 | 0.482955 | reject | - | statistical_rejected |
| `net_income_yoy_latest` | high | 0.727491 | -0.003076 | 0.433174 | 0.469182 | 0.213289 | reject | - | statistical_rejected |
| `net_margin_latest` | high | 0.727504 | -0.004832 | 0.060126 | 0.313572 | 0.566510 | reject | - | statistical_rejected |
| `ocf_debt` | high | 0.601811 | -0.003669 | 0.344669 | 0.469182 | 0.513311 | reject | - | statistical_rejected |
| `ocf_ni` | high | 0.562149 | -0.003003 | 0.170509 | 0.468899 | 0.595308 | reject | - | statistical_rejected |
| `pb_latest` | low | 0.726189 | 0.002113 | 0.397751 | 0.469182 | 0.618943 | reject | - | statistical_rejected |
| `revenue_yoy_latest` | high | 0.727491 | -0.003458 | 0.392298 | 0.469182 | 0.298640 | reject | - | statistical_rejected |
| `roe_latest` | high | 0.726501 | -0.006063 | 0.046654 | 0.313572 | 0.275409 | reject | - | statistical_rejected |

## 组合基线

| 场景 | 总收益 | 年化 | Sharpe | 最大回撤 | 交易数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| full_2x | 0.927300 | 0.133900 | 1.050000 | -0.116400 | 704.000000 |
| full_3x | 0.835300 | 0.123300 | 0.970000 | -0.125300 | 704.000000 |
| recent_2x | 0.084400 | 0.082300 | 0.680000 | -0.108400 | 177.000000 |
| weak_2x | -0.041100 | -0.042700 | -0.310000 | -0.104600 | 183.000000 |

## 最终结论

- 状态：`no_statistical_candidate`
- 最终因子：无
- 新 watch 版本：无
- `active_version`：None
- 纸面目标/订单/生产启用：否

## 负知识

- `p8_accrual`：marginal_ic_not_positive, marginal_ic_p_above_0_10, marginal_ic_q_above_0_10
- `p8_core_earnings_ratio`：marginal_ic_not_positive, marginal_ic_p_above_0_10, marginal_ic_q_above_0_10
- `p8_debt_ratio_latest`：marginal_ic_not_positive, marginal_ic_p_above_0_10, marginal_ic_q_above_0_10
- `p8_gross_margin_latest`：marginal_ic_not_positive, marginal_ic_q_above_0_10
- `p8_net_income_yoy_latest`：marginal_ic_not_positive, marginal_ic_p_above_0_10, marginal_ic_q_above_0_10
- `p8_net_margin_latest`：marginal_ic_not_positive, marginal_ic_q_above_0_10
- `p8_ocf_debt`：marginal_ic_not_positive, marginal_ic_p_above_0_10, marginal_ic_q_above_0_10
- `p8_ocf_ni`：marginal_ic_not_positive, marginal_ic_p_above_0_10, marginal_ic_q_above_0_10
- `p8_pb_latest`：marginal_ic_p_above_0_10, marginal_ic_q_above_0_10
- `p8_revenue_yoy_latest`：marginal_ic_not_positive, marginal_ic_p_above_0_10, marginal_ic_q_above_0_10
- `p8_roe_latest`：marginal_ic_not_positive, marginal_ic_q_above_0_10

## 门槛说明

- 财务因子必须先由 P7 的 `verified_factor_ids` 证明 PIT 合法，并且 P6 标记为 portfolio-ready。
- 边际 IC：基准组合 80% + 候选 20%，NW lag=5，p≤0.10 且 BH-FDR q≤0.10。
- 相关性：候选与现有成员的最大平均绝对日截面相关≤0.90。
- 组合：20 仓等权、open_t+1、无杠杆；2x 基准、3x 硬压力；5x 只作 warning。
- 滚动一年：正收益窗口比例不得低于基线，中位收益不得比基线差超过 1pp。
- 当前 HS300 名单回填历史存在幸存者偏差。
