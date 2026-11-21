# R448 防御 ETF 配置族

- 旧 R446/R447 的时序数值作废；本报告使用修正后的“先结算旧持仓、再按月末信号开盘换仓”。

| 规则 | 训练年化 | 验证年化 | 检验年化 | 检验Sharpe | 检验MDD | 近一年年化 | 3×检验年化 | 检验p05 | 近一年p05 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fixed_equal_6 | 5.71% | 4.00% | - | - | - | - | - | - | - |
| fixed_equity60_bond25_gold15 | 5.77% | 3.36% | - | - | - | - | - | - | - |
| fixed_core40_dividend20_bond30_gold10 | 4.43% | 3.71% | - | - | - | - | - | - | - |
| risk_parity_vol20 | 5.54% | 5.90% | - | - | - | - | - | - | - |
| risk_parity_vol60（选中） | 5.88% | 6.25% | 3.58% | 0.807 | -4.91% | -0.69% | 3.42% | -4.48% | -8.24% |
| risk_parity_vol120 | 5.37% | 7.34% | - | - | - | - | - | - | - |
| min_variance_60 | 3.92% | 3.65% | - | - | - | - | - | - | - |
| min_variance_120 | 3.65% | 4.07% | - | - | - | - | - | - | - |
| trend_gate_risk_parity_60 | 4.15% | 1.98% | - | - | - | - | - | - | - |
| trend_gate_fixed60 | 6.18% | -1.64% | - | - | - | - | - | - | - |

- 选中：`risk_parity_vol60`
- 判定：**拒绝**

## 门槛

- fully_allocated_no_cash_drag: 通过
- has_eligible_rule: 通过
- recent_annual_min: 未过
- recent_total_p05_positive: 未过
- stress_3x_positive: 通过
- test_annual_min: 通过
- test_mdd_min: 通过
- test_sharpe_min: 通过
- test_total_p05_positive: 未过

## 增量复核

- 相对六资产等权的收益差：训练和检验为负，验证和近一年为正；四个窗口的 95% bootstrap 下界全部为负。
- 结论：风险平价没有形成稳定的增量证据，点估计的低回撤不能替代增量检验。

## 数据泄漏审计

- 风险模型只使用信号日及以前的收益；主评价从训练起点加载价格，不额外使用训练前或验证前收益。

## 成本诊断

- 5× 成本仅 warning：检验年化 2.94%，未用于硬拒绝。
- 主口径 2×、硬压力 3×；不把 5× 当杠杆或额外否决条件。

## 数据复现

- 研究依赖被 Git 忽略的本地 `data/kline_etf_enriched`；脚本缺失时会 fail-closed，不会静默回测短历史。

## 边界

- 脚本现已从训练起点加载价格；冻结结果生成于该“仅数据装载范围”收紧前，但信号端始终只使用信号日及以前的数据。

- ETF forward-adjusted prices are local data and are not committed to Git.
- Only six long-history ETFs; results do not generalise to unavailable asset classes.
- No leverage; weights are long-only and sum to one on every rebalance.
- Cash earns zero only during the first month before a signal becomes executable.
- The test and recent windows have been observed in earlier research.
- 5x transaction cost is warning-only, never a hard rejection.
- ETF tracking error, spreads, and market impact beyond the bps grid are not modeled.
