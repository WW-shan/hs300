# R524 跨代策略证据统一审计

- R385/R386：修复后的逐日 HS300 名单 + 申万行业生效日历史。
- R499/R506：当前官方 HS300 名单回填历史。
- 直接比较结论：**不可排名**，两代证据的数据血缘不同。
- R385/R386 状态：candidate_for_paper_trading_review（研究候选，未上线）。
- R499/R506/R522 状态：rejected（严格门槛未过）。

## 门槛

- all_sources_exist: 通过
- all_sidecars_match: 通过
- r385_r386_candidate_status_recorded: 通过
- r499_r506_rejected_status_recorded: 通过
- no_execution_artifacts: 通过

## 下一轮唯一合法比较

- 必须使用同一份 PIT 名单、同一份行业 vintage、同一组成本口径重新跑两代候选。
- 在重跑完成前，不得用 R385/R386 的历史成绩宣称优于 R506。

## 边界

- This is a source audit, not a new backtest.
- R385/R386 and R499/R506 cannot be ranked by their published headline returns because their data vintages differ.
- Both candidates remain research-only.
- No leverage and no production activation.
