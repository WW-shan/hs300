# R457 value_quality 权重稳定性

- 变体数：231
- 中心权重：{'debt_ratio_latest': 0.1, 'ocf_debt': 0.4, 'pb_latest': 0.5}
- 中心 60 日中性 IC：0.088653
- 邻域（L1≤0.15）中位 IC：0.088122
- 邻域 5% 分位 IC：0.086528
- 中心 moving-block bootstrap 95% 下界：0.058642

## 门槛

- center_ic_positive: 通过
- center_newey_west_p_le: 通过
- neighbor_median_ic_positive: 通过
- neighbor_5pct_ic_positive: 通过
- bootstrap_ci_low_positive: 通过
- all_positive_weight_variants_positive: 通过

## 结论

在 0.05 的权重网格上，中心附近的 6 个方向都保持正的中性信号，中心 60 日 IC 的 moving-block 95% 下界约为 0.0586。这个结论说明权重不是只靠一个偶然点，但不代表组合策略已经可用；组合层仍需通过收益、回撤和近一年尾部门禁。

## 边界

- Weight grids are correlated, not independent experiments.
- The audit uses neutralized IC, not a portfolio return.
- No new financial fields are introduced.
- Current HS300 membership and current industry classification retain survivorship bias.
- No leverage; no production activation.
