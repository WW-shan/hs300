import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './api'
import { QK } from './queryKeys'

/** 成分快照按月更新, 一次会话内无需反复校验。 */
const SNAPSHOT_STALE_MS = 6 * 60 * 60 * 1000

/**
 * HS300 成分宇宙 — 快照列表 + 选定快照的 PIT 成员。
 *
 * 查询键集中在 QK, 多个页面共享同一份缓存, 切页不会重复请求。
 * `selectedDate` 缺省取最新快照 (快照覆盖 2023-07 起)。
 */
export function useHS300Universe() {
  const queryClient = useQueryClient()
  const snapshotsQuery = useQuery({
    queryKey: QK.hs300Snapshots,
    queryFn: api.hs300Snapshots,
    staleTime: SNAPSHOT_STALE_MS,
  })
  const latest = snapshotsQuery.data?.latest ?? null
  const [selectedDate, setSelectedDate] = useState<string | null>(null)

  useEffect(() => {
    if (!selectedDate && latest) setSelectedDate(latest)
  }, [selectedDate, latest])

  const membersQuery = useQuery({
    queryKey: QK.hs300Members(selectedDate ?? ''),
    queryFn: () => api.hs300Members(selectedDate ?? ''),
    enabled: !!selectedDate,
    staleTime: SNAPSHOT_STALE_MS,
  })

  const members = useMemo(() => membersQuery.data?.members ?? [], [membersQuery.data])
  const symbols = useMemo(() => members.map(member => member.symbol), [members])
  const syncMutation = useMutation({
    mutationFn: () => api.hs300Sync(),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['kline-batch'] })
      void queryClient.invalidateQueries({ queryKey: QK.screenerKlineBatchPrefix })
      void queryClient.invalidateQueries({ queryKey: QK.dataStatus })
    },
  })
  const syncHint = syncMutation.data
    ? `已同步 ${syncMutation.data.rows} 行${syncMutation.data.zero_row_symbols.length > 0
      ? `，${syncMutation.data.zero_row_symbols.length} 只无数据`
      : ''}`
    : null

  return {
    snapshots: snapshotsQuery.data?.snapshots ?? [],
    earliest: snapshotsQuery.data?.earliest ?? null,
    latest,
    selectedDate,
    setSelectedDate,
    snapshotDate: membersQuery.data?.snapshot_date ?? null,
    members,
    symbols,
    isLoading: snapshotsQuery.isLoading || (!!selectedDate && membersQuery.isLoading),
    error: (snapshotsQuery.error ?? membersQuery.error ?? null) as Error | null,
    syncDaily: () => syncMutation.mutate(),
    isSyncing: syncMutation.isPending,
    syncHint,
    syncError: syncMutation.error as Error | null,
  }
}

/**
 * 区间并集 — 回测加载数据时覆盖区间内全部曾入选标的。
 * PIT 过滤仍由后端按日执行 (StrategyBacktestConfig.hs300), 前端只负责并集。
 */
export function useHS300MembersBetween(
  start: string | null,
  end: string | null,
  enabled = true,
) {
  const query = useQuery({
    queryKey: QK.hs300MembersBetween(start ?? '', end ?? ''),
    queryFn: () => api.hs300MembersBetween(start ?? '', end ?? ''),
    enabled: enabled && !!start && !!end,
    staleTime: SNAPSHOT_STALE_MS,
  })
  const members = useMemo(() => query.data?.members ?? [], [query.data])
  const symbols = useMemo(() => members.map(member => member.symbol), [members])
  return {
    symbols,
    snapshotDates: query.data?.snapshot_dates ?? [],
    isLoading: query.isLoading,
    error: (query.error ?? null) as Error | null,
  }
}
