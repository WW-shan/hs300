// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { api } from './api'

afterEach(() => vi.unstubAllGlobals())

it('uses the working TickFlow provider explicitly for HS300 sync', async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ ok: true }),
  })
  vi.stubGlobal('fetch', fetchMock)

  await api.hs300Sync()

  const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
  expect(url).toBe('/api/hs300/sync')
  expect(JSON.parse(init.body as string)).toEqual({
    start: null,
    end: null,
    provider: 'tickflow',
  })
})
