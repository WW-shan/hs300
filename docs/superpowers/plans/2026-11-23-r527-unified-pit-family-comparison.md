# R527 Unified PIT Family Comparison Plan

**Goal:** Re-run the R385/R386 hold-30 family and the R499/R506 five-leg family on one committed PIT membership/industry vintage and the current engine, then compare them under the same windows and costs.

**Architecture:** Research-only confirmation runner. Load R526 bundle, patch membership and industry loading, build R506-like rank composite, run the frozen R385 family construction, and compare. No parameter search or promotion.

## Constraints

- R526 bundle must match its manifest and the source R385 input hashes.
- Both families run on the current engine SHA.
- Same membership, industry vintage, windows and cost labels.
- No leverage, orders, paper targets or production activation.
- Both families are frozen; no parameter selection from this comparison.

## Tasks

1. Freeze inputs, windows, family definitions and gates; add failing tests.
2. Build a single bundled-input engine context and verify source hashes.
3. Run both families for train/validation/test/recent and 2x/3x/5x where applicable.
4. Compare absolute performance and block-bootstrap tails under identical windows.
5. Write JSON/Markdown/sidecars, verify, commit, push and check CI.
