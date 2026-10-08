// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { FactorBacktest } from './FactorBacktest'

const fixtures = vi.hoisted(() => ({
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
    hs300Current: async () => ({
      as_of: '2026-09-30',
      source: 'csindex',
      fetched_at: '2026-10-08T12:00:00+08:00',
      count: 2,
      members: [
        { symbol: '600001.SH', name: 'A' },
        { symbol: '000002.SZ', name: 'B' },
      ],
    }),
  },
}))

let host: HTMLDivElement
let root: Root
let client: QueryClient

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
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

async function waitForElement<T extends Element>(selector: string): Promise<T> {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const element = host.querySelector<T>(selector)
    if (element) return element
    await act(async () => new Promise(resolve => setTimeout(resolve, 0)))
  }
  throw new Error('element not found: ' + selector)
}

it('uses the official current HS300 filter without injecting a fixed symbols pool', async () => {
  await act(async () => root.render(
    <QueryClientProvider client={client}>
      <FactorBacktest initialFactorName="turnover_rate" />
    </QueryClientProvider>,
  ))

  const checkbox = await waitForElement<HTMLInputElement>('[data-testid="hs300-filter-checkbox"]')
  await waitFor(() => expect(checkbox.disabled).toBe(false))
  await act(async () => checkbox.click())

  const runButton = Array.from(host.querySelectorAll('button'))
    .find(button => button.textContent?.includes('开始因子分析')) as HTMLButtonElement
  expect(runButton).toBeDefined()
  await waitFor(() => expect(runButton.disabled).toBe(false))
  await act(async () => runButton.click())
  await waitFor(() => expect(fixtures.factorRuns).toHaveLength(1))

  expect(fixtures.factorRuns[0]).toEqual(expect.objectContaining({
    hs300: true,
    symbols: null,
  }))
})
