# R384 财务营运效率改善因子研究报告

- Research ID：`sha256:5335ecd07782d763d96827527bd0b6ef15ad5e122b5fe3dc67e1e6b13069b48a`
- 生成时间：`2026-11-20T21:39:24Z`
- P7 财务 PIT：`sha256:c4b0b1e460c58f2f7facc0b2237dd834ab7bc494ba6225dc0194ba74b2e2bd32`
- P6 准入：`sha256:1513dbc1e614f6ab31552bdb18771bdecf3162ad3b343e2681bda9b2e9cba298`
- 样本：250 只非金融 HS300
- 主期限：60 交易日
- 决策：`no_portfolio_candidate`
- `active_version` 保持 null；不生成纸面目标或订单。

## 单因子 IC

| 因子 | 期限 | raw | industry | size | industry+size |
| --- | ---: | ---: | ---: | ---: | ---: |
| `inventory_turnover_improvement_yoy` | 5 | 0.022796 | 0.015165 | 0.024245 | 0.017598 |
| `inventory_turnover_improvement_yoy` | 20 | 0.041330 | 0.024321 | 0.044009 | 0.029819 |
| `inventory_turnover_improvement_yoy` | 60 | 0.073762 | 0.047133 | 0.079368 | 0.057040 |
| `receivable_turnover_improvement_yoy` | 5 | 0.012132 | 0.004566 | 0.012841 | 0.005244 |
| `receivable_turnover_improvement_yoy` | 20 | 0.018402 | 0.000082 | 0.022886 | 0.004306 |
| `receivable_turnover_improvement_yoy` | 60 | 0.031150 | 0.003853 | 0.037836 | 0.010678 |
| `receivable_growth_minus_revenue_growth` | 5 | 0.007941 | 0.001208 | 0.011450 | 0.003961 |
| `receivable_growth_minus_revenue_growth` | 20 | 0.013400 | 0.001570 | 0.022615 | 0.009298 |
| `receivable_growth_minus_revenue_growth` | 60 | 0.013121 | 0.001890 | 0.024971 | 0.013193 |
| `inventory_growth_minus_cogs_growth` | 5 | 0.010418 | 0.007616 | 0.010880 | 0.007448 |
| `inventory_growth_minus_cogs_growth` | 20 | 0.024484 | 0.015962 | 0.026679 | 0.015955 |
| `inventory_growth_minus_cogs_growth` | 60 | 0.051053 | 0.032744 | 0.060444 | 0.036572 |

## 增量门槛

| 因子 | 中性化确认 | 60日边际IC | NW p | BH q | 最大相关 | 结论 |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| `inventory_turnover_improvement_yoy` | industry, size, industry_size | 0.032239 | 0.002182 | 0.008728 | 0.148143 | incremental_pass |
| `receivable_turnover_improvement_yoy` | size | 0.009480 | 0.432268 | 0.576357 | 0.143308 | horizon_rejected |
| `receivable_growth_minus_revenue_growth` | - | 0.000059 | 0.995292 | 0.995292 | 0.155190 | horizon_rejected |
| `inventory_growth_minus_cogs_growth` | industry, size, industry_size | 0.024611 | 0.004764 | 0.009529 | 0.128098 | incremental_pass |

## 组合门槛

| 因子 | 组合状态 | 全样本2x年化 | 全样本3x年化 | 近一年2x收益 | 弱市2x收益 | 未通过检查 |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| `inventory_turnover_improvement_yoy` | portfolio_rejected | 0.010700 | 0.003000 | 0.049600 | -0.025000 | full_annual_within_tolerance, full_sharpe_within_tolerance, full_3x_annual_within_tolerance |
| `receivable_turnover_improvement_yoy` | - | - | - | - | - | - |
| `receivable_growth_minus_revenue_growth` | - | - | - | - | - | - |
| `inventory_growth_minus_cogs_growth` | portfolio_rejected | 0.060100 | 0.052600 | 0.033200 | -0.005500 | recent_drawdown_within_tolerance |

## 结论

- 单因子期限门槛通过：2。
- 增量门槛通过：2。
- 组合门槛通过：0。
- 增量通过候选：['inventory_turnover_improvement_yoy', 'inventory_growth_minus_cogs_growth']。
- 最终因子：无。
- 未通过任一门槛的候选只保留为负知识，不进入策略或生产版本。

## 负知识

- `r384_inventory_turnover_improvement_yoy`：portfolio_check_failed:full_3x_annual_within_tolerance, portfolio_check_failed:full_annual_within_tolerance, portfolio_check_failed:full_sharpe_within_tolerance, portfolio_rejected；组合状态 `portfolio_rejected`
- `r384_receivable_turnover_improvement_yoy`：fewer_than_two_neutralized_confirmations, horizon_rejected, marginal_ic_p_above_0_10, marginal_ic_q_above_0_10
- `r384_receivable_growth_minus_revenue_growth`：fewer_than_two_neutralized_confirmations, horizon_rejected, marginal_ic_p_above_0_10, marginal_ic_q_above_0_10
- `r384_inventory_growth_minus_cogs_growth`：portfolio_check_failed:recent_drawdown_within_tolerance, portfolio_rejected；组合状态 `portfolio_rejected`

## 边界

- 当前 HS300 名单回填历史存在幸存者偏差。
- 行业分类使用当前快照，不是历史 PIT。
- 银行和非银金融被排除，避免营运资本口径错配。
- 2x 成本为正常硬门槛，3x 为压力门槛，5x 只记录 warning。
