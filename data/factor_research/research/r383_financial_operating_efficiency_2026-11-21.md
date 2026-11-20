# R383 财务营运效率因子研究报告

- Research ID：`sha256:6f889972896655cad70984e44984f7d968f37f8b7715ef2e672f9dcab496a0c6`
- 生成时间：`2026-11-20T20:59:40Z`
- P7 财务 PIT：`sha256:9295cd76fcdc44d07aec403ec63d65cc54bb330acefcdd1e2054bee57f02968a`
- 样本：250 只非金融 HS300
- 主期限：60 交易日
- 决策：`no_incremental_candidate`
- `active_version` 保持 null；不生成纸面目标或订单。

## 单因子 IC

| 因子 | 期限 | raw | industry | size | industry+size |
| --- | ---: | ---: | ---: | ---: | ---: |
| `inventory_turnover_ttm` | 5 | 0.016542 | 0.007273 | 0.012152 | 0.005314 |
| `inventory_turnover_ttm` | 20 | 0.017982 | 0.005435 | 0.015711 | 0.009939 |
| `inventory_turnover_ttm` | 60 | 0.026837 | 0.009769 | 0.029815 | 0.022428 |
| `receivable_turnover_ttm` | 5 | -0.005276 | -0.005024 | 0.013409 | 0.009889 |
| `receivable_turnover_ttm` | 20 | -0.010300 | -0.009734 | 0.042353 | 0.027448 |
| `receivable_turnover_ttm` | 60 | -0.023502 | -0.016047 | 0.081645 | 0.051757 |

## 增量门槛

| 因子 | 中性化确认 | 60日边际IC | NW p | BH q | 最大相关 | 结论 |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| `inventory_turnover_ttm` | - | -0.000238 | 0.966857 | 0.966857 | 0.296915 | horizon_rejected |
| `receivable_turnover_ttm` | size, industry_size | -0.023358 | 0.015865 | 0.031731 | 0.258509 | incremental_rejected |

## 结论

- 单因子期限门槛通过：1。
- 增量门槛通过：0。
- 组合门槛通过：0。
- 最终因子：无。
- 未通过增量门槛的因子只保留为负知识，不进入策略。

## 负知识

- `r383_inventory_turnover_ttm`：fewer_than_two_neutralized_confirmations, horizon_rejected, marginal_ic_not_positive, marginal_ic_p_above_0_10, marginal_ic_q_above_0_10
- `r383_receivable_turnover_ttm`：incremental_rejected, marginal_ic_not_positive

## 边界

- 当前 HS300 名单回填历史存在幸存者偏差。
- 行业分类使用当前快照，不是历史 PIT。
- 银行和非银金融被排除，避免营运资本口径错配。
- 2x 成本为正常硬门槛，3x 为压力门槛，5x 只记录 warning。
