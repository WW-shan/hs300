# R525 名单口径泛化审计

## 两套证据

| 版本 | 状态 | 全窗/检验年化 | Sharpe | MDD | 名单口径 |
| --- | --- | ---: | ---: | ---: | --- |
| R385/R386 | candidate_for_paper_trading_review | 13.75% | 1.441 | -8.02% | 逐日历史 HS300 |
| R506 | rejected | 12.84% | 1.030 | -10.27% | 当前名单回填历史 |
| R522 fresh-start | rejected | 15.42% | — | -8.89% | 当前名单回填历史 |

## 判定

- 直接赢家：**无**。
- 原因：两代策略的名单 vintage、行业 vintage 和窗口都不相同。
- R506 fresh-start 正收益窗口：5/5；最差 p05：-10.70%。

## 不能推出的结论

- 不能说 R385/R386 的 13.75% 年化一定优于 R506。
- 不能说 R506 的近一年表现一定更稳健。
- 不能把“当前名单回填”后的亏损归因于因子失效，除非在同一份 PIT 输入上重跑。

## 下一轮合法动作

- 先将 R381 的逐日名单、行业历史和扩展帧封装成可提交的冻结输入 bundle。
- 再用同一个 runner 重跑 R385/R386 与 R499/R506 两代候选。

## 边界

- This is a lineage and metric audit, not a new backtest.
- R385/R386 and R506 are not directly rankable on headline returns.
- Membership vintage is one possible cause of differences, not a proved causal effect.
- No leverage and no production activation.
