# R369：研究门禁审计与重判

本轮只新增门禁政策 v2 与事后复核，不改写任何原预注册结果或原始回测证据。

## 口径修正

- 5× 是交易成本压力，不是杠杆；策略始终多头、满仓上限 100%、不使用融资。
- 2× 作为现实成本硬门槛，3× 作为严重成本压力；5× 只做诊断和人工复核。
- 净收益集中度只报告；集中度硬门槛使用毛利润贡献、股票宽度、行业暴露和最大单票亏损。
- 年化 12%、Sharpe 1.25、2022 必须非负、6 个起始相位至少 5 个为正不再作为统一硬门槛。

## 结果

| 轮次 | 候选 | 旧结论 | 新结论 | 恢复 |
| --- | --- | --- | --- | --- |
| R334 | `r334_top10_three_asset_equity_cap33_posthoc` | 未通过 | 进入纸面复核 | 是 |
| R344 | `r344_extended_window_center` | 未通过 | 进入纸面复核 | 是 |
| R345 | `r345_a_top20_extended_posthoc` | 未通过 | 进入纸面复核 | 是 |
| R346 | `r346_ab_top20_extended_posthoc` | 未通过 | 进入纸面复核 | 是 |
| R347 | `r347_solvency_c_sleeve_cap10_gold_trend_proxy` | 未通过 | 进入纸面复核 | 是 |
| R348 | `r348_solvency_additive_c_sleeve_cap10_gold_trend_proxy` | 未通过 | 进入纸面复核 | 是 |
| R349 | `r349_defensive_dual_momentum_top2_60d` | 未通过 | 不推进 | 否 |
| R350 | `r350_dual_universe_ab_ensemble` | 未通过 | 进入纸面复核 | 是 |
| R351 | `r351_merton_dd_c_sleeve_cap10_gold_trend_proxy` | 未通过 | 进入纸面复核 | 是 |
| R352 | `r352_intraday_risk_c_sleeve_cap10_gold_trend_proxy` | 未通过 | 进入纸面复核 | 是 |
| R353 | `r353_factor_agreement_additive_c_sleeve_cap10_gold_trend_proxy` | 未通过 | 进入纸面复核 | 是 |
| R354 | `r354_factor_agreement_gate_c_sleeve_cap10_gold_trend_proxy` | 未通过 | 进入纸面复核 | 是 |
| R356 | `r356_core_earnings_A_gate` | 未通过 | 不推进 | 否 |
| R357 | `r357_gold_commodity_dual_trend_alt_slot` | 未通过 | 进入纸面复核 | 是 |
| R358 | `r358_b_core_earnings_gate` | 未通过 | 不推进 | 否 |
| R359 | `r359_c_three_cohort_staggered` | 未通过 | 进入纸面复核 | 是 |
| R360 | `r360_industry_fade_quality_h20_sl20` | 未通过 | 进入纸面复核 | 是 |
| R361 | `r361_intraday_reversal_candidate` | 未通过 | 不推进 | 否 |
| R362 | `r362_weak_regime_intraday_5d` | 未通过 | 不推进 | 否 |
| R363 | `r363_weak_reversal_blend` | 未通过 | 不推进 | 否 |
| R364 | `r364_weak_close_reversal` | 未通过 | 不推进 | 否 |
| R365 | `r365_20d_weak_reversal` | 未通过 | 不推进 | 否 |
| R366 | `r366_industry_size_neutralized_candidate` | 未通过 | 不推进 | 否 |
| R367 | `r367_size_neutralized_candidate` | 未通过 | 不推进 | 否 |
| R368 | `r368_capacity_cap10` | 未通过 | 不推进 | 否 |
| R368 | `r368_capacity_cap20` | 未通过 | 不推进 | 否 |

- 共复核 26 个候选/容量点。
- v2 下进入纸面复核 14 个，其中 14 个是旧门禁未通过后恢复。
- 仍不推进 12 个。
- 多资产核心线的多个变体高度重叠，恢复只代表满足正常门槛，不代表独立发现。

## 重点恢复

- R334：旧 5× 净利润集中度否决；5× 毛利润 top1/top3/top5 仅 5.0%/12.0%/17.3%，新政策恢复为纸面复核。
- R357：旧年化 12% 与 2022 必须非负导致失败；2× 年化 11.18%、Sharpe 1.26、MDD -7.40%、2022 -0.91%，新政策恢复。
- R359：旧 2022 非负、5/6 fresh starts、5× 净集中度共同否决；2× 年化 11.67%、Sharpe 1.26、MDD -9.10%、4/6 起始相位为正，新政策恢复。
- R360：旧唯一失败项为 2022 必须非负（-0.80%），2× 年化 12.61%、Sharpe 1.33，新政策恢复并作为首选复核候选。

## 仍不推进

- R356/R358：绝对 Sharpe/MDD 或相对对照不满足，不能归因于 5× 门槛。
- R361-R365：反转/中性化线的 Sharpe、MDD 或相对质量对照明显不达标。
- R368：10/20 仓容量点都没有同时解决回撤和收益/Sharpe 问题。

## 限制

- 这是事后门禁纠偏，不是旧预注册通过。
- 候选恢复只允许进入纸面复核，不启用生产、不生成订单。
- 行业分类仍有当前快照回填限制，历史样本重叠也不能当作独立样本外证明。

证据：`r369_gate_policy_reconciliation.json`、`gate_policy_v2.json`、`audit_gate_policy_v2.py`。
