// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { FactorBacktest } from './FactorBacktest'

const fixtures = vi.hoisted(() => ({
  resolveBetween: null as ((value: {
    start: string
    end: string
    snapshot_dates: string[]
    members: { symbol: string; name: string }[]
  }) => void) | null,
  factorRuns: [] as Record<string, unknown>[],
}))

vi.mock('@/lib/api', () => ({
  api: {
    factorColumns: async () => ({ columns: [{
      id: 'turnover_rate', label: '换手率', group: '成交', description: '',
    }] }),
    factorRun: async (payload: Record<string, unknown>) => {
      fixtures.factorRuns.push(payload)
      return { error: 'fixture result', config: payload }
    },
    researchCandidateCreate: async () => ({ ok: true }),
    hs300Snapshots: async () => ({
      snapshots: ['2023-07-01', '2024-02-01'],
      earliest: '2023-07-01',
      latest: '2024-02-01',
    }),
    hs300Members: async (asOf: string) => ({
      as_of: asOf,
      snapshot_date: asOf,
      members: [{ symbol: '000002.SZ', name: 'B' }],
    }),
    hs300MembersBetween: async () => new Promise(resolve => {
      fixtures.resolveBetween = resolve
    }),
    hs300Sync: async () => ({
      ok: true,
      provider: 'adata',
      symbols: 2,
      rows: 10,
      zero_row_symbols: [],
      start: '2023-07-01',
      end: '2024-02-01',
    }),
  },
}))

let host: HTMLDivElement
let root: Root
let client: QueryClient

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  fixtures.resolveBetween = null
  fixtures.factorRuns = []
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => root.unmount())
  client.clear()
  host.remove()
})

async function waitFor(assertion: () => void) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    await act(async () => new Promise(resolve => setTimeout(resolve, 0)))
    try {
      assertion()
      return
    } catch {
      // Keep waiting for the query/mutation state to settle.
    }
  }
  assertion()
}

it('waits for the PIT union and submits it with hs300 enabled', async () => {
  await act(async () => root.render(
    <QueryClientProvider client={client}>
      <FactorBacktest initialFactorName="turnover_rate" />
    </QueryClientProvider>,
  ))

  await waitFor(() => expect(
    host.querySelector<HTMLButtonElement>('[data-testid="hs300-apply"]')?.disabled,
  ).toBe(false))
  await act(async () => host.querySelector<HTMLButtonElement>('[data-testid="hs300-apply"]')!.click())

  const runButton = Array.from(host.querySelectorAll('button'))
    .find(button => button.textContent?.includes('开始因子分析')) as HTMLButtonElement
  expect(runButton).toBeDefined()
  expect(runButton.disabled).toBe(true)

  await act(async () => fixtures.resolveBetween?.({
    start: '2023-11-28',
    end: '2024-02-28',
    snapshot_dates: ['2023-11-01', '2024-02-01'],
    members: [
      { symbol: '600001.SH', name: 'A' },
      { symbol: '000002.SZ', name: 'B' },
    ],
  }))
  await waitFor(() => expect(runButton.disabled).toBe(false))
  await act(async () => runButton.click())
  await waitFor(() => expect(fixtures.factorRuns).toHaveLength(1))

  expect(fixtures.factorRuns[0]).toEqual(expect.objectContaining({
    symbols: ['600001.SH', '000002.SZ'],
    hs300: true,
  }))
})
