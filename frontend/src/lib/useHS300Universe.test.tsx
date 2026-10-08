// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { useHS300Current } from './useHS300Universe'

const fixtures = vi.hoisted(() => ({
  currentCalls: [] as boolean[],
  syncCalls: 0,
}))

vi.mock('@/lib/api', () => ({
  api: {
    hs300Current: async (refresh = false) => {
      fixtures.currentCalls.push(refresh)
      return {
        as_of: '2026-09-30',
        source: 'csindex',
        fetched_at: '2026-10-08T12:00:00+08:00',
        count: 2,
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
        provider: 'akshare',
        symbols: 2,
        rows: 123,
        zero_row_symbols: [],
        start: '2026-01-01',
        end: '2026-09-30',
        as_of: '2026-09-30',
        source: 'csindex',
      }
    },
  },
}))

type Current = ReturnType<typeof useHS300Current>

let host: HTMLDivElement
let root: Root
let queryClient: QueryClient

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  fixtures.currentCalls = []
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
  for (let attempt = 0; attempt < 100; attempt += 1) {
    await act(async () => new Promise(resolve => setTimeout(resolve, 0)))
    try {
      assert()
      return
    } catch {
      // keep waiting
    }
  }
  assert()
}

it('只拉取官方当前成分, 不再请求快照接口', async () => {
  let current: Current | null = null
  function Harness() {
    current = useHS300Current()
    return null
  }
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <Harness />
      </QueryClientProvider>,
    )
  })

  await waitFor(() => expect(current?.symbols).toEqual(['600519.SH', '300750.SZ']))
  expect(current!.count).toBe(2)
  expect(current!.asOf).toBe('2026-09-30')
  expect(current!.source).toBe('csindex')
  expect(fixtures.currentCalls).toEqual([false])
})

it('刷新时强制走后端 refresh=true', async () => {
  let current: Current | null = null
  function Harness() {
    current = useHS300Current()
    return null
  }
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <Harness />
      </QueryClientProvider>,
    )
  })
  await waitFor(() => expect(current?.count).toBe(2))

  await act(async () => current!.refresh())
  await waitFor(() => expect(fixtures.currentCalls).toEqual([false, true]))
  await waitFor(() => expect(current!.isRefreshing).toBe(false))
})

it('触发一次按当前名单的 AkShare 日K同步', async () => {
  const invalidateQueries = vi.spyOn(queryClient, 'invalidateQueries')
  let current: Current | null = null
  function Harness() {
    current = useHS300Current()
    return null
  }
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <Harness />
      </QueryClientProvider>,
    )
  })
  await waitFor(() => expect(current?.count).toBe(2))

  await act(async () => current!.syncDaily())
  expect(fixtures.syncCalls).toBe(1)
  await waitFor(() => expect(current?.syncHint).toBe('已同步 123 行'))
  expect(current!.syncError).toBeNull()
  expect(invalidateQueries).toHaveBeenCalledWith(expect.objectContaining({
    queryKey: ['screener-kline-batch'],
  }))
})
