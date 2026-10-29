# R295 R257 Concentration Metric Audit

## Scope

Post-hoc arithmetic audit of the four archived 3x R257 candidate/control trade logs. R257 outcomes and preliminary calculations were inspected before this round was frozen, so this is not blind or independent validation. The original preregistration is preserved unchanged and its timing correction is separately registered. No alpha claim follows.

## Candidate results

| Sample | Trades | Net P&L | Gross wins | Gross losses | Legacy C2/C3/C4 | Positive-symbol-net shares C2/C3/C4 | Static P&L after top 1/3/5 removal |
| --- | ---: | ---: | ---: | ---: | --- | --- | --- |
| Discovery | 209 | 100,571.50 | 742,601.15 | 642,029.65 | fail/fail/fail | 11.0%/28.9%/41.3% | 49,612/-32,873/-90,472 |
| Validation | 223 | 367,392.42 | 1,493,030.87 | 1,125,638.45 | fail/fail/fail | 12.0%/33.5%/50.0% | 263,585/76,892/-66,135 |
| Combined additive | 432 | 467,963.92 | 2,235,632.02 | 1,767,668.10 | fail/fail/fail | 11.0%/25.3%/37.2% | 337,184/168,093/26,087 |

## Matched control

| Sample | Trades | Net P&L | Gross wins | Gross losses | Positive-symbol-net shares C2/C3/C4 |
| --- | ---: | ---: | ---: | ---: | --- |
| Discovery | 209 | -99,493.01 | 689,169.52 | 788,662.53 | 13.1%/34.9%/52.2% |
| Validation | 223 | 86,727.77 | 1,131,534.08 | 1,044,806.31 | 36.7%/70.1%/83.0% |
| Combined additive | 432 | -12,765.24 | 1,820,703.60 | 1,833,468.84 | 21.6%/48.9%/60.8% |

## Interpretation

R257's top-symbol net contribution divided by total net P&L is denominator-sensitive and can exceed 100%. In this archived candidate, the frozen C2-C4 checks fail under that denominator, while dividing by positive symbol-level net contributions gives bounded descriptive shares and a different classification. This proves metric sensitivity only; it does not prove the alternate limits are appropriate. Positive-contributor share excludes negative symbols, and gross-positive-P&L share is another distinct measure.

Static top-symbol removal subtracts archived trade contributions only. It is not a rerun, counterfactual equity curve, or executable portfolio result.

R257's original preregistration and decision remain unchanged. This repeated historical sample is not independent; no system backtest or provider was called, no market/factor/strategy data was written, and production remains pause_production.

R257 reconciliation: {"full_distinct_symbols": true, "full_gross_loss": true, "full_gross_profit": true, "full_legacy_shares": true, "full_total_pnl": true, "full_trade_count": true, "validation_gross_loss": true, "validation_gross_profit": true, "validation_legacy_shares": true, "validation_total_pnl": true, "validation_trade_count": true}
Calculation checks: {"all_four_logs_nonempty": true, "frozen_result_reconciliation_pass": true, "positive_symbol_net_shares_bounded_monotone": true}

Input manifest SHA-256: 567c67f8bdc2424e9f89c4345371948db61bb56d48b6a08571e15d610b2eea90
Scope amendment SHA-256: 504a0bc81c3e459d6e5e5d89cd1650b3025c9ec172c5bb79096ec449fa06d599
Panel registration/readback SHA-256: 852fc0b2bc302e00fc6ced4a0cd274fa42b046f9a30ad98fc129e367f13a2319
