import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './api'
import { QK } from './queryKeys'

/** 官方当前成分 6 小时内不重复拉取; 手动刷新可强制打官方接口。 */
const HS300_STALE_MS = 6 * 60 * 60 * 1000

/**
 * HS300 动态筛选器 — 只读官方当前成分, 不读任何月度快照归档。
 *
 * 返回的 count/symbols 是当前官方名单; 页面只负责开关与展示, 具体过滤由后端
 * 按同一份名单完成 (回测/因子用 symbols 作为数据加载边界, 选股用 pool 过滤)。
 * 行情数据同步不在本 hook 内: 日K由系统管线统一同步全市场, 名单与数据解耦。
 */
export function useHS300Current() {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: QK.hs300Current,
    queryFn: () => api.hs300Current(),
    staleTime: HS300_STALE_MS,
    retry: false,
  })

  const refresh = useMutation({
    mutationFn: () => api.hs300Current(true),
    onSuccess: data => {
      queryClient.setQueryData(QK.hs300Current, data)
    },
  })

  const members = query.data?.members ?? []

  return {
    members,
    symbols: members.map(member => member.symbol),
    count: query.data?.count ?? members.length,
    asOf: query.data?.as_of ?? null,
    source: query.data?.source ?? null,
    fetchedAt: query.data?.fetched_at ?? null,
    isLoading: query.isLoading,
    error: (query.error ?? refresh.error ?? null) as Error | null,
    refresh: () => refresh.mutate(),
    isRefreshing: refresh.isPending,
  }
}
