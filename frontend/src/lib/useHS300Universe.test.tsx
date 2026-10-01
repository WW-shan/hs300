// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { useHS300MembersBetween, useHS300Universe } from './useHS300Universe'

const fixtures = vi.hoisted(() => ({
  snapshotCalls: 0,
  memberCalls: [] as string[],
  betweenCalls: [] as string[],
  syncCalls: 0,
}))

vi.mock('@/lib/api', () => ({
  api: {
    hs300Snapshots: async () => {
      fixtures.snapshotCalls += 1
      return { snapshots: ['2023-07-01', '2026-09-01'], earliest: '2023-07-01', latest: '2026-09-01' }
    },
    hs300Members: async (asOf: string) => {
      fixtures.memberCalls.push(asOf)
      return {
        as_of: asOf,
        snapshot_date: asOf,
        members: [{ symbol: '600519.SH', name: '贵州茅台' }],
      }
    },
    hs300MembersBetween: async (start: string, end: string) => {
      fixtures.betweenCalls.push(`${start}..${end}`)
      return {
        start,
        end,
        snapshot_dates: ['2023-07-01', '2023-08-01'],
        members: [
          { symbol: '600519.SH', name: '贵州茅台' },
          { symbol: '300750.SZ', name: '宁德时代' },
        ],
      }
    },
    hs300Sync: async () => {
      fixtures.syncCalls += 1
      return {
        ok: true,
        provider: 'tickflow',
        symbols: 300,
        rows: 123,
        zero_row_symbols: [],
        start: '2023-07-01',
        end: '2026-09-01',
      }
    },
  },
}))

type Universe = ReturnType<typeof useHS300Universe>
type Between = ReturnType<typeof useHS300MembersBetween>

let host: HTMLDivElement
let root: Root
let queryClient: QueryClient

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  fixtures.snapshotCalls = 0
  fixtures.memberCalls = []
  fixtures.betweenCalls = []
  fixtures.syncCalls = 0
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => root.unmount())
  queryClient.clear()
  host.remove()
})

async function waitFor(assert: () => void) {
  // 每轮都在 act 内让出事件循环, 查询状态更新不会触发 act 警告。
  for (let attempt = 0; attempt < 100; attempt += 1) {
    await act(async () => {
      await new Promise(resolve => setTimeout(resolve, 0))
    })
    try {
      assert()
      return
    } catch {
      // 继续等待
    }
  }
  assert()
}

it('默认选中最新快照并加载 PIT 成员', async () => {
  let latest: Universe | null = null
  function Harness() {
    latest = useHS300Universe()
    return <div data-symbols={latest.symbols.join(',')} />
  }
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <Harness />
      </QueryClientProvider>,
    )
  })

  await waitFor(() => expect(latest?.symbols).toEqual(['600519.SH']))
  expect(latest!.selectedDate).toBe('2026-09-01')
  expect(latest!.snapshotDate).toBe('2026-09-01')
  expect(fixtures.memberCalls).toEqual(['2026-09-01'])
  expect(host.querySelector('[data-symbols]')?.getAttribute('data-symbols')).toBe('600519.SH')
})

it('切换快照只按新日期请求成员, 快照列表共享缓存', async () => {
  let latest: Universe | null = null
  function Harness() {
    latest = useHS300Universe()
    return null
  }
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <Harness />
      </QueryClientProvider>,
    )
  })
  await waitFor(() => expect(latest?.symbols).toEqual(['600519.SH']))

  await act(async () => latest!.setSelectedDate('2023-07-01'))
  await waitFor(() => expect(fixtures.memberCalls).toEqual(['2026-09-01', '2023-07-01']))

  expect(latest!.selectedDate).toBe('2023-07-01')
  expect(latest!.members).toEqual([{ symbol: '600519.SH', name: '贵州茅台' }])
  expect(fixtures.snapshotCalls).toBe(1)
})

it('区间并集 hook 在缺少端点参数时不请求', async () => {
  let between: Between | null = null
  function Harness() {
    between = useHS300MembersBetween(null, null)
    return null
  }
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <Harness />
      </QueryClientProvider>,
    )
  })

  expect(between!.symbols).toEqual([])
  expect(fixtures.betweenCalls).toEqual([])
})

it('区间并集 hook 拉取覆盖快照的并集', async () => {
  let between: Between | null = null
  function Harness() {
    between = useHS300MembersBetween('2023-07-01', '2023-08-31')
    return null
  }
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <Harness />
      </QueryClientProvider>,
    )
  })

  await waitFor(() => expect(between?.symbols).toEqual(['600519.SH', '300750.SZ']))
  expect(between!.snapshotDates).toEqual(['2023-07-01', '2023-08-01'])
  expect(fixtures.betweenCalls).toEqual(['2023-07-01..2023-08-31'])
})

it('触发一次 AData 同步并返回落盘结果', async () => {
  const invalidateQueries = vi.spyOn(queryClient, 'invalidateQueries')
  let universe: Universe | null = null
  function Harness() {
    universe = useHS300Universe()
    return null
  }
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <Harness />
      </QueryClientProvider>,
    )
  })
  await waitFor(() => expect(universe?.symbols).toEqual(['600519.SH']))

  await act(async () => universe!.syncDaily())
  expect(fixtures.syncCalls).toBe(1)
  await waitFor(() => expect(universe?.syncHint).toBe('已同步 123 行'))

  expect(universe!.syncError).toBeNull()
  expect(invalidateQueries).toHaveBeenCalledWith(expect.objectContaining({
    queryKey: ['screener-kline-batch'],
  }))
})
