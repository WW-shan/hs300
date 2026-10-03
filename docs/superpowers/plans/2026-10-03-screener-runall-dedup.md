# Screener Run-All De-duplication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Prevent duplicate daily `run_all` requests when adding a strategy while preserving immediate scans and retry eligibility when another scan is already active.

**Architecture:** Add a small, pure scheduler helper for a date plus the resulting daily strategy pool. Use the same key reservation for the pool dialog, AI builder, composite builder, and the page auto-run effect. The helper returns whether a request was accepted; only accepted requests reserve a key, and an accepted request without a known date reserves the resolved key on success.

**Tech Stack:** React, TypeScript, Vitest, Vite, FastAPI local backend for acceptance verification.

---

### Task 1: Specify run planning and key reservation with tests

**Files:**
- Create: `frontend/src/lib/screenerRunScheduler.test.ts`
- Create later: `frontend/src/lib/screenerRunScheduler.ts`

- [x] **Step 1: Write failing tests for the confirmed behavior**

Test that adding an uncached daily strategy produces a plan for the complete resulting pool; the explicit pool action reserves its date/pool key, and a later auto-effect attempt does not schedule the same work. Also test that a busy guard leaves the key unclaimed so the auto effect can schedule once the active request settles.

```ts
it('deduplicates pool confirmation and the later auto-run effect', () => {
  const plan = planScreenerRunAll({
    asOf: '2026-09-30',
    dailyPoolIds: ['bullish_alignment', 'boll_breakout'],
    cachedAsOfById: { bullish_alignment: '2026-09-30' },
    forcedIds: ['boll_breakout'],
  })!
  const key = { current: null as string | null }
  const pending = { current: false }
  const scheduledKeys: string[] = []
  const schedule = () => {
    if (tryReserveScreenerRunAll(key, pending, false, plan.runKey!)) {
      scheduledKeys.push(plan.runKey!)
    }
  }

  expect(plan.strategyIds).toEqual(['boll_breakout'])
  schedule()
  pending.current = false
  schedule()
  expect(scheduledKeys).toEqual(['2026-09-30|bullish_alignment,boll_breakout'])
})

it('does not reserve a pool key while another run is active', () => {
  const key = { current: null as string | null }
  const pending = { current: true }
  const runKey = '2026-09-30|bullish_alignment,boll_breakout'

  expect(tryReserveScreenerRunAll(key, pending, false, runKey)).toBe(false)
  expect(key.current).toBeNull()
  pending.current = false
  expect(tryReserveScreenerRunAll(key, pending, false, runKey)).toBe(true)
})
```

- [x] **Step 2: Run the focused test and confirm RED**

Run: `pnpm vitest run src/lib/screenerRunScheduler.test.ts`

Expected: FAIL because `screenerRunScheduler.ts` has not been implemented.

- [x] **Step 3: Implement only the pure planning and reservation helpers**

Export `planScreenerRunAll({ asOf, dailyPoolIds, cachedAsOfById, forcedIds })`, which returns the deduplicated union of forced IDs and uncached IDs, the full pool, and a nullable `${asOf}|${dailyPoolIds.join(',')}` key. Export `tryReserveScreenerRunAll(keyRef, pendingRef, mutationPending, key)`, which returns `false` without changing refs when the key is already reserved or a run is pending; otherwise it sets both refs and returns `true`.

- [x] **Step 4: Run the focused test and confirm GREEN**

Run: `pnpm vitest run src/lib/screenerRunScheduler.test.ts`

Expected: 5 tests pass, including stale selected strategies and the no-date-known case.

### Task 2: Share scheduling across all daily-pool entry points

**Files:**
- Modify: `frontend/src/pages/Screener.tsx`
- Test: `frontend/src/lib/screenerRunScheduler.test.ts`

- [x] **Step 1: Route accepted keyed requests through the tested reservation helper**

Make `requestRunAll` return a boolean. If a schedule key is supplied, use `tryReserveScreenerRunAll` before calling the existing mutation; if no key is supplied, preserve the current pending guard. Keep the existing `onSettled` reset behavior.

- [x] **Step 2: Use the same resulting-pool plan at each add path**

For the Strategy Pool dialog, AI builder, and Composite builder, derive the resulting daily pool, current cached dates, and any newly forced strategy IDs. Call a shared `scheduleDailyPoolRun(poolIds, forcedIds)` helper that runs `planScreenerRunAll`, then calls `requestRunAll({ date: asOf || undefined, strategyIds: plan.strategyIds, poolIdsForKey: plan.dailyPoolIds }, undefined, plan.runKey ?? undefined)`. If `asOf` is empty, mutation `onSuccess(data, vars)` reserves `buildScreenerRunAllKey(data.as_of, vars.poolIdsForKey)` before the pending flag clears.

- [x] **Step 3: Use keyed scheduling from the auto-run effect**

Keep cache coverage and `screenerAutoRun` checks unchanged. Replace the effect's unconditional `runAllDateRef.current = runKey` plus unkeyed request with `scheduleDailyPoolRun(dailyPoolIds)`; a busy rejection must leave the key free for the effect to retry after settlement.

- [x] **Step 4: Run focused tests and type/build checks**

Run: `pnpm vitest run src/lib/screenerRunScheduler.test.ts`

Run: `pnpm build`

Expected: focused tests pass and the TypeScript/Vite build exits successfully.

### Task 3: Verify the running local page with real stored market data

**Files:**
- Verify only: local Vite page at `http://127.0.0.1:3020`, proxied to backend port `8001`.

- [x] **Step 1: Start the worktree Vite server with the existing local backend**

Run: `BACKEND_PORT=8001 pnpm dev --host 127.0.0.1 --port 3020 --strictPort`

- [x] **Step 2: Add a daily built-in strategy in the page**

Use the authenticated temporary browser profile, add one built-in daily strategy that is not already in the result cache, and observe its real API request.

- [x] **Step 3: Verify one successful request and visible result**

Expected: exactly one `POST /api/screener/run_all` for the resulting date/pool key, HTTP 200, and a result card populated from the local enriched market-data table.

### Task 4: Final checks

**Files:**
- Verify: complete frontend test suite and worktree diff, including the approved design update.

- [x] **Step 1: Run the frontend test suite**

Run: `pnpm vitest run`

- [x] **Step 2: Check whitespace and scope**

Run: `git diff --check`

Expected: all frontend tests pass and the diff contains only the scheduler helper, its regression test, and `Screener.tsx` integration (the design note update, and this plan document).

- [x] **Step 3: Commit the focused fix**

Run: `git add frontend/src/lib/screenerRunScheduler.ts frontend/src/lib/screenerRunScheduler.test.ts frontend/src/pages/Screener.tsx docs/superpowers/specs/2026-10-03-screener-runall-dedup-design.md docs/superpowers/plans/2026-10-03-screener-runall-dedup.md && git commit -m "fix: deduplicate screener pool scans"`
