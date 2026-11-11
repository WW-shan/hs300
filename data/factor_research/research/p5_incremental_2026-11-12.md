# P5 全库增量筛选报告

- Research ID：`sha256:641f0df5849264ea133ea0a8ccb58b057816afd47d916014f0d5baabb93cb6c7`
- 生成时间：`2026-11-11T16:11:06Z`
- 基准集合：`multi_dimension_v1` v1
- 基准内容 SHA：`sha256:9eb577a43d549e1b9218ae61d125fa393dd65263988f0d5b221067a23c61822b`
- 候选数：13；去重后：12
- 所有结论只允许形成 watch 版本，`active_version` 保持 null。

## 候选增量

| 候选 | 经济含义 | 角色 | 边际 IC | NW p | q | 最大相关 | 统计门槛 | 组合门槛 | 结论 |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- |
| `p1_cs_spread_p60` | cs_spread | new | -0.000165 | 0.931705 | 0.953840 | 0.782223 | reject | - | incremental_rejected |
| `p1_idio_vol_p20` | idio_vol | replacement | 0.002969 | 0.235753 | 0.682467 | 0.514884 | reject | - | incremental_rejected |
| `p1_vol_of_vol_p20` | vol_of_vol | already_in_set | - | - | - | - | reject | - | already_in_current_set |
| `p2_amihud_p120` | amihud | new | 0.004641 | 0.130958 | 0.523833 | 0.298190 | reject | - | incremental_rejected |
| `p2_amount_p5` | amount_level | new | 0.007185 | 0.003838 | 0.046053 | 0.394173 | pass | reject | portfolio_rejected |
| `p2_annual_vol_p10` | annual_volatility | new | -0.000461 | 0.792562 | 0.953840 | 0.770666 | reject | - | incremental_rejected |
| `p2_atr_p5` | atr_risk | new | 0.000303 | 0.874837 | 0.953840 | 0.804282 | reject | - | incremental_rejected |
| `p2_boll_width_p10` | bollinger_width | new | -0.001963 | 0.298882 | 0.682467 | 0.655852 | reject | - | incremental_rejected |
| `p2_max_return_p60` | max_return | new | 0.000094 | 0.953840 | 0.953840 | 0.783578 | reject | - | incremental_rejected |
| `p2_turnover_p5` | turnover_level | new | -0.000653 | 0.747078 | 0.953840 | 0.669024 | reject | - | incremental_rejected |
| `p3_repurchase_decay_p20` | repurchase_event | new | -0.002449 | 0.029007 | 0.174039 | 0.060222 | reject | - | incremental_rejected |
| `p4_dividend_abs_0015` | dividend_yield | replacement | 0.002754 | 0.341234 | 0.682467 | 0.447709 | reject | - | incremental_rejected |
| `p4_margin_q050` | margin_change | replacement | -0.000379 | 0.860577 | 0.953840 | 0.138472 | reject | - | incremental_rejected |

## 组合基线

| 场景 | 总收益 | 年化 | Sharpe | 最大回撤 | 交易数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| full_2x | 0.927300 | 0.133900 | 1.050000 | -0.116400 | 704.000000 |
| full_3x | 0.835300 | 0.123300 | 0.970000 | -0.125300 | 704.000000 |
| recent_2x | 0.084400 | 0.082300 | 0.680000 | -0.108400 | 177.000000 |
| weak_2x | -0.041100 | -0.042700 | -0.310000 | -0.104600 | 183.000000 |

## 最终结论

- 决策：`no_new_watch_version`
- 最终因子数：0
- 新 watch 版本：-
- `active_version`：None

## 拒绝与负知识

- `p1_cs_spread_p60`：marginal_ic_not_positive, marginal_ic_p_above_0_10, marginal_ic_q_above_0_10
- `p1_idio_vol_p20`：marginal_ic_p_above_0_10, marginal_ic_q_above_0_10
- `p1_vol_of_vol_p20`：already_in_current_set
- `p2_amihud_p120`：marginal_ic_p_above_0_10, marginal_ic_q_above_0_10
- `p2_amount_p5`：portfolio_gate_failed:full_sharpe_within_tolerance
- `p2_annual_vol_p10`：marginal_ic_not_positive, marginal_ic_p_above_0_10, marginal_ic_q_above_0_10
- `p2_atr_p5`：marginal_ic_p_above_0_10, marginal_ic_q_above_0_10
- `p2_boll_width_p10`：marginal_ic_not_positive, marginal_ic_p_above_0_10, marginal_ic_q_above_0_10
- `p2_max_return_p60`：marginal_ic_p_above_0_10, marginal_ic_q_above_0_10
- `p2_turnover_p5`：marginal_ic_not_positive, marginal_ic_p_above_0_10, marginal_ic_q_above_0_10
- `p3_repurchase_decay_p20`：marginal_ic_not_positive, marginal_ic_q_above_0_10, registration_required
- `p4_dividend_abs_0015`：marginal_ic_p_above_0_10, marginal_ic_q_above_0_10
- `p4_margin_q050`：marginal_ic_not_positive, marginal_ic_p_above_0_10, marginal_ic_q_above_0_10

## 门槛说明

- 边际 IC：基准组合 80% + 候选 20%，NW lag=5，p≤0.10 且 BH-FDR q≤0.10。
- 相关性：候选与不替换的现有成员最大平均绝对日截面相关≤0.90。
- 组合：20 仓等权、open_t+1、无杠杆；2x 基准、3x 压力；收益/Sharpe 容差 1pp/0.05，回撤容差 2pp，且全周期和近一年不得同时恶化。
- 滚动一年：正收益窗口比例不得低于基线，中位收益不得比基线差超过 1pp。
- 拆解：新增成员在全周期或近一年至少一个窗口有正边际贡献，且两个窗口都不能出现超过 0.5pp 的实质拖累。
- 当前 HS300 名单回填历史存在幸存者偏差；财务和扩展字段 PIT 仍主要是声明级证据。
