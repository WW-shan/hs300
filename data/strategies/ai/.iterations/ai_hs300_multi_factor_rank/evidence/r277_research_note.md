# R277 Research Note (2026-10-28)

## Result

The R275 term-gate-removal candidate returned +90.46% versus +63.34% for R266; terminal relative wealth was +16.61%. Its paired daily arithmetic active-return mean annualized by multiplying by 252 was +3.00%, a diagnostic rather than a compounded excess return. The one-sided 35-day HAC p-value was 0.271; Holm adjustment across the three R275 candidates raised it to 0.813. The 60-day circular block-bootstrap 95% confidence interval for annualized arithmetic active mean was [-7.47%, +12.75%], which includes zero. This is post-hoc analysis of a repeatedly examined historical window, not independent alpha confirmation.

Full statistics: `r277_r275_active_return_statistics_v2.json`, SHA-256 `5d5dc75d349dfc858788a9439488c5c85b8cfa304e95c02f6ed8c152682d8f91`. Inputs, HAC values and fixed-seed block bootstrap were independently recomputed and matched.

## Timing And Readiness

R276 did not establish point-in-time availability of macro inputs. The read-only probe `r277_macro_freshness_probe.json` (SHA-256 `44be89ffced3e7d9dd218b88c4e48010ffd083f19bbe6ec52f00f303a0aeb80f`) records macro partitions through 2026-10-19, but source publication, collection and revision history are absent. Live quote polling is disabled and the panel has no fresh quote age; prospective paper observation has not started. No paper account or automatic-follow rule was created.

Panel reload proof: `r277_panel_registration.json`, SHA-256 `0eb90d7f3bb95cea40735ade1c03e83e7b059fa872c5b872d646a5535a080a6a`; 223 strategies, 229 factors, zero load errors, all three R275 candidates registered as research-only. R275 originally used `StrategyBacktestService -> BacktestEngine`; R277 only reanalyzed saved curves and did not run a new backtest.

## Verification And Decision

- Focused R272/R273 contract tests: 9 passed.
- Full backend suite: 3025 passed, 6 skipped, 1 xfailed, 2 failed. One failure is a custom-signal prompt field-extraction assertion that parses example tokens `score` and `0` as fields. The other is a screener turnover test blocked by DuckDB failing to allocate 8 MiB. These failures are unrelated to R277 statistics.
- Ruff and `git diff --check` passed.
- Production remains `pause_production`; the R275 candidate remains research-only.

Next work should not search thresholds in the reused historical window. Begin forward paper observation only after fresh market data and timestamped macro-arrival logs are available; do not place real orders.
