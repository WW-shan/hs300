export interface ScreenerRunPlanInput {
  asOf: string | null | undefined
  dailyPoolIds: readonly string[]
  cachedAsOfById: Readonly<Record<string, string | null | undefined>>
  forcedIds?: readonly string[]
}

export interface ScreenerRunPlan {
  strategyIds: string[]
  dailyPoolIds: string[]
  runKey: string | null
}

export interface MutableRef<T> {
  current: T
}

export function buildScreenerRunAllKey(asOf: string, dailyPoolIds: readonly string[]): string {
  return `${asOf}|${dailyPoolIds.join(',')}`
}

export function planScreenerRunAll(input: ScreenerRunPlanInput): ScreenerRunPlan | null {
  const dailyPoolIds = [...new Set(input.dailyPoolIds)]
  const forced = new Set(input.forcedIds ?? [])
  const strategyIds = dailyPoolIds.filter(
    id => forced.has(id) || !input.asOf || input.cachedAsOfById[id] !== input.asOf,
  )

  if (strategyIds.length === 0) return null
  return {
    strategyIds,
    dailyPoolIds,
    runKey: input.asOf ? buildScreenerRunAllKey(input.asOf, dailyPoolIds) : null,
  }
}

export function tryReserveScreenerRunAll(
  keyRef: MutableRef<string | null>,
  pendingRef: MutableRef<boolean>,
  mutationPending: boolean,
  key: string,
): boolean {
  if (keyRef.current === key || pendingRef.current || mutationPending) return false
  keyRef.current = key
  pendingRef.current = true
  return true
}
