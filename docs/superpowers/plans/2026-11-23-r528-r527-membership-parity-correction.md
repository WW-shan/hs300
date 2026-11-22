# R528 R527 Membership Parity Correction Plan

**Goal:** Correct the R527 five-leg execution path so both families execute under the same frozen R526 daily HS300 membership, then rerun the frozen comparison without changing factor definitions or selection.

## Root cause

R527's hold-30 path wraps `StrategyBacktestService.run()` with a patched
`_hs300_membership_frame`; the five-leg path sets `hs300=True` but does not patch
that function, so it loads the official current HS300 list at runtime. Therefore
R527's `same_membership_for_both=True` claim is invalid.

## Constraints

- Preserve R527 outputs as historical evidence; do not overwrite them.
- R528 must use the same R526 manifest, bundle hashes, windows, costs, engine,
  factor weights and execution settings as R527.
- No parameter reselection and no promotion.
- Both family runs must request HS300 membership through the framework and use
  exactly the bundled daily membership frame.
- No leverage, orders, paper targets or production activation.
- Record preregistration, runner, JSON/Markdown outputs, SHA-256 sidecars,
  tests, and the invalidated R527 claim.

## Tasks

1. Add a failing test proving the corrected runner pins the framework's HS300
   membership loader to the R526 bundle and restores it afterward.
2. Implement R528 with a membership-pinned five-leg run and reuse the frozen
   hold-30 family construction.
3. Rerun both families under identical windows, costs and the current engine.
4. Compare performance and block-bootstrap tails; retain all failed or
   non-verifiable outcomes as negative knowledge.
5. Verify hashes and tests, then commit and push.
