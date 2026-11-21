# R393 财务费用结构与现金质量因子研究报告

- P7 财务 PIT：`sha256:c6d20e2f1c402c41f3baa020ad58775dd8c39712eac4abfae29d617fb13d38cf`
- P6 准入：`sha256:27168a49c05d64e163c22ca0bb6d7cec00f763f8a0613cfc8bd048c6fe86e50c`
- 样本：300 只 HS300 股票
- 候选数：6
- 统计通过：0
- 决策：`stats_only_no_promotion`
- `active_version` 保持 null；不生成纸面目标或订单。

## 统计结果

| 因子 | 覆盖率 | 边际IC | NW p | BH q | 最大相关 | 结论 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `sales_expense_ratio_ttm_low` | 49.33% | -0.000404 | 0.9262 | 0.9262 | 0.191 | statistical_rejected |
| `management_expense_ratio_ttm_low` | 59.17% | -0.006011 | 0.0941 | 0.1411 | 0.235 | statistical_rejected |
| `ocf_to_revenue_ttm` | 60.18% | -0.005206 | 0.0338 | 0.1232 | 0.996 | statistical_rejected |
| `fcf_proxy_to_revenue_ttm` | 60.18% | -0.004084 | 0.0631 | 0.1261 | 0.777 | statistical_rejected |
| `cash_to_assets_latest` | 72.84% | -0.008035 | 0.0411 | 0.1232 | 0.277 | statistical_rejected |
| `payables_to_cogs_ttm` | 50.61% | 0.001008 | 0.7082 | 0.8498 | 0.162 | statistical_rejected |

## 结论
- 6 个新财报因子统计通过 0 个，不进入组合层。
- 费用率、现金转换、现金资产比和应付款率均没有转化为当前组合的增量信息。
- 这是负知识，不进入策略或生产版本。

## 边界
- 投资现金流代理自由现金流，并非真实 capex 口径。
- 当前 HS300 名单回填历史存在幸存者偏差。
- 2× 为主成本，3× 为压力，5× 仅 warning。
