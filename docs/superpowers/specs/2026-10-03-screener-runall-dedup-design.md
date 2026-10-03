# Strategy Pool Run-All De-duplication

## Status

Approved by the user; implemented and verified in the local strategy page.

## Context

On the local authenticated `/screener` page, adding the built-in `boll_breakout`
strategy produced two identical `POST /api/screener/run_all` requests for
`2026-09-30` and `strategy_ids=["boll_breakout"]`. Both returned HTTP 200. The
second request began 2 ms after the first request finished. Its result was the
same as the first, so this is duplicate work, not a second strategy result.

The strategy-pool confirmation handler and the AI/composite strategy save
handlers immediately schedule newly added daily strategies. The page's auto-run
effect also schedules uncached strategies when the pool changes. The synchronous
pending guard prevents overlapping requests, but these explicit handlers do not
reserve the date/pool key used by the effect. After the first mutation settles,
the effect can therefore submit the same work again while the cached summary is
still stale.

## Goal and Scope

- Preserve the existing behavior: adding a daily strategy starts a scan
  immediately, including when automatic page scans are disabled.
- Ensure explicit strategy-pool, AI-builder, and composite-builder additions
  share one date/pool scheduling key with the auto-run effect, preventing a
  duplicate scan after the first request settles.
- If an explicit request is rejected because another scan is still active, do
  not reserve the key; the effect must remain able to schedule the missing work
  after the active scan settles.
- Schedule all daily strategies in the resulting pool whose cache does not
  cover the current date, so de-duplication does not suppress work for an
  already-selected but stale strategy.
- Keep AkShare/provider, backend API, stored market data, and strategy semantics
  unchanged.

## Implementation Shape

Use the existing `runAllDateRef` and synchronous pending guard rather than
changing API contracts. Add a small shared daily-pool planning helper used by
the explicit pool-confirm, AI-builder, and composite-builder paths as well as
the auto-run effect. Make the request helper report whether it accepted a run.
When a path successfully schedules missing strategies for the resulting daily
pool, reserve the same `asOf|dailyPoolIds` key that the effect uses. If the helper
declines because a run is active, leave the key unreserved so the effect can
retry after settlement.

## Regression and Acceptance Checks

- Add a regression test for immediate pool scheduling followed by the
  auto-effect check: the same date/pool must schedule only one run.
- Cover the busy-guard case: a declined pool-confirm request must not consume
  the key and must remain eligible for later scheduling.
- On the local page, add a daily strategy and verify exactly one
  `POST /api/screener/run_all`, a successful result, and the result card/table
  remains usable.
- Run focused frontend tests, `pnpm build`, and `git diff --check`.

## Risks

The scheduling key must match the effect's key format and include the full
resulting daily pool. Otherwise stale selected strategies could be skipped or a
later valid scan could be suppressed. No database migration or data rewrite is
expected.
