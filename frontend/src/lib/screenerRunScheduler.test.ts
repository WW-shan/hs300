import { describe, expect, it } from 'vitest'
import { planScreenerRunAll, tryReserveScreenerRunAll } from './screenerRunScheduler'

describe('screener run-all scheduling', () => {
  it('plans uncached daily strategies using the full resulting pool key', () => {
    const plan = planScreenerRunAll({
      asOf: '2026-09-30',
      dailyPoolIds: ['bullish_alignment', 'boll_breakout'],
      cachedAsOfById: { bullish_alignment: '2026-09-30' },
      forcedIds: ['boll_breakout'],
    })

    expect(plan).toEqual({
      strategyIds: ['boll_breakout'],
      dailyPoolIds: ['bullish_alignment', 'boll_breakout'],
      runKey: '2026-09-30|bullish_alignment,boll_breakout',
    })
  })

  it('plans the full pool before the latest date is known', () => {
    const plan = planScreenerRunAll({
      asOf: undefined,
      dailyPoolIds: ['bullish_alignment', 'boll_breakout'],
      cachedAsOfById: { bullish_alignment: '2026-09-30' },
      forcedIds: ['boll_breakout'],
    })

    expect(plan).toEqual({
      strategyIds: ['bullish_alignment', 'boll_breakout'],
      dailyPoolIds: ['bullish_alignment', 'boll_breakout'],
      runKey: null,
    })
  })

  it('includes already-selected strategies whose cache is stale', () => {
    const plan = planScreenerRunAll({
      asOf: '2026-09-30',
      dailyPoolIds: ['bullish_alignment', 'boll_breakout', 'volume_price_surge'],
      cachedAsOfById: {
        bullish_alignment: '2026-09-30',
        volume_price_surge: '2026-09-29',
      },
      forcedIds: ['boll_breakout'],
    })

    expect(plan?.strategyIds).toEqual(['boll_breakout', 'volume_price_surge'])
  })

  it('does not repeat a run when the auto effect observes the reserved pool after settlement', () => {
    const plan = planScreenerRunAll({
      asOf: '2026-09-30',
      dailyPoolIds: ['bullish_alignment', 'boll_breakout'],
      cachedAsOfById: { bullish_alignment: '2026-09-30' },
      forcedIds: ['boll_breakout'],
    })!
    const keyRef = { current: null as string | null }
    const pendingRef = { current: false }
    const scheduledKeys: string[] = []
    const schedule = () => {
      if (tryReserveScreenerRunAll(keyRef, pendingRef, false, plan.runKey!)) {
        scheduledKeys.push(plan.runKey!)
      }
    }

    schedule()
    pendingRef.current = false
    schedule()

    expect(scheduledKeys).toEqual(['2026-09-30|bullish_alignment,boll_breakout'])
  })

  it('leaves the key free when a different run is active', () => {
    const keyRef = { current: null as string | null }
    const pendingRef = { current: true }
    const key = '2026-09-30|bullish_alignment,boll_breakout'

    expect(tryReserveScreenerRunAll(keyRef, pendingRef, false, key)).toBe(false)
    expect(keyRef.current).toBeNull()

    pendingRef.current = false
    expect(tryReserveScreenerRunAll(keyRef, pendingRef, true, key)).toBe(false)
    expect(keyRef.current).toBeNull()

    expect(tryReserveScreenerRunAll(keyRef, pendingRef, false, key)).toBe(true)
    expect(keyRef.current).toBe(key)
  })
})
