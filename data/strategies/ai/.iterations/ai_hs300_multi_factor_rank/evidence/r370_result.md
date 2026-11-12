# R370 · 因子与策略参数蒙特卡洛审计

结论：**并非所有参数都已合格**。组合层的风险平价、目标波动和 C 卫星权重形成了稳定平台；R360 的行业相对强弱窗口、五日衰减、评分权重等核心 alpha 参数仍没有邻域实验，不能称为合格。

## R360 重建与路径风险

- 冻结 2x 净值重建：年化 12.61%、Sharpe 1.330、MDD -9.04%；与冻结结果一致：True。
- v2 点估计与 bootstrap 门槛：True。
- 原 R360 预注册门槛：False，未通过项为 G4_2022_segment_nonnegative。

| 循环块长度 | 252日正收益概率 | 收益P05 | 收益中位数 | MDD P05 |
| ---: | ---: | ---: | ---: | ---: |
| 5 | 89.90% | -2.70% | 11.98% | -11.40% |
| 10 | 92.20% | -1.52% | 12.09% | -10.46% |
| 20 | 92.35% | -1.69% | 11.74% | -10.11% |
| 60 | 92.50% | -1.28% | 11.95% | -9.12% |
- 20日块正收益概率的多种子范围：91.80% 至 93.00%。
- PSR（Sharpe>0 的非正态调整概率）：0.9969。这不是 DSR。

## 组合参数拉丁超立方扰动

- 96 组参数中，v2 点估计通过率：100.00%。
- 年化 P05/中位数/P95：11.59% / 12.24% / 13.03%。
- Sharpe P05/中位数/P95：1.300 / 1.356 / 1.406。
- MDD P05/中位数/最差：-10.09% / -8.80% / -10.40%。

## 参数状态

| 参数 | 状态 | 结论 |
| --- | --- | --- |
| `stock_rel_industry_lookback_days` | `not_monte_carlo_tested` | Only one frozen value was backtested; no adjacent lookback grid exists. |
| `industry_fade_change_days` | `not_monte_carlo_tested` | Only one frozen five-session change was backtested. |
| `scoring_weights` | `not_monte_carlo_tested` | The current weights were inherited, not randomly perturbed in this audit. |
| `basic_filter_thresholds` | `not_monte_carlo_tested` | No adjacent threshold sensitivity experiment was retained. |
| `max_hold_days` | `historical_scan_only` | Prior holding studies exist, but no Monte Carlo path-plus-parameter audit around the final R360 portfolio. |
| `stop_loss` | `historical_scan_only` | {'artifact': 'data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r102d_stop_loss_scan.json', 'artifact_sha256': '116d61d16426eff730dd426622d872c86dee9e6d1e1ba543e9363dd2d023a9c8', 'scan_points': 5, 'rows': {'none': {'stop_loss': None, 'annual': 0.0958, 'sharpe': 0.46, 'mdd': -0.5059}, 'sl_10': {'stop_loss': -0.1, 'annual': -0.0096, 'sharpe': 0.12, 'mdd': -0.568}, 'sl_15': {'stop_loss': -0.15, 'annual': 0.0498, 'sharpe': 0.31, 'mdd': -0.5148}, 'sl_20': {'stop_loss': -0.2, 'annual': 0.0942, 'sharpe': 0.45, 'mdd': -0.4085}, 'sl_25': {'stop_loss': -0.25, 'annual': 0.0791, 'sharpe': 0.41, 'mdd': -0.5147}}} |
| `gold_trend_lookback_days` | `not_monte_carlo_tested` | A single 200-day rule was retained; its neighbouring grid was not tested. |
| `regime_gate_states` | `not_monte_carlo_tested` | The state mapping was frozen before this audit; threshold uncertainty was not sampled. |
| `risk_parity_lookback_days` | `qualified_plateau` | 40/60/90-day cells pass v2 point and bootstrap checks in R344, R350 and R354. |
| `overlay_target_vol` | `qualified_plateau` | All 12/15/18% overlay cells pass across the three frozen lineages. |
| `joint_equity_target_vol` | `qualified_plateau` | The three joint target-vol cells pass v2 re-evaluation in every lineage. |
| `c_satellite_cap` | `qualified_plateau` | The four cap cells pass v2 re-evaluation in every lineage; legacy 12% gates had rejected them. |
| `max_positions` | `historical_scan_only` | R368 compared 10 versus 20 positions, but did not run a path-bootstrap parameter audit. |

## 方法边界

- 蒙特卡洛不能替代样本外检验；块 bootstrap 只重排历史路径，不能制造新的市场状态。
- 现有 3×3 和 cap 网格中的单元格高度相关，不能当作独立试验。完整试验台账缺失，因此本轮不声称 PBO 或 DSR 已通过。
- R360 的行业分类是当前快照回填，不是 point-in-time 数据；历史成分与 ETF 复权限制仍然存在。
- 本轮不选最佳参数、不加杠杆、不生成订单或生产目标。

## 参考

- Bailey & Lopez de Prado (The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality). https://doi.org/10.2139/ssrn.2460551
- Bailey, Borwein, Lopez de Prado & Zhu (The Probability of Backtest Overfitting). https://doi.org/10.2139/ssrn.2326253
- Politis & Romano (The Stationary Bootstrap). https://doi.org/10.1080/01621459.1994.10476870
- White (A Reality Check for Data Snooping). https://doi.org/10.1111/1468-0262.00152
- Hansen (A Test for Superior Predictive Ability). https://doi.org/10.1198/073500105000000063
- McKay, Beckman & Conover (A comparison of Latin hypercube and simple random sampling). https://doi.org/10.1080/00401757.1979.10489755
- Sobol (Global sensitivity indices for nonlinear mathematical models and their Monte Carlo estimates). https://doi.org/10.1016/S0378-4754(00)00270-6
- Arlot & Celisse (A survey of cross-validation procedures for model selection). https://doi.org/10.1214/09-SS054
