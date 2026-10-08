// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { api } from './api'

afterEach(() => vi.unstubAllGlobals())

it('requests the official current membership and supports force refresh', async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ as_of: '2026-09-30', source: 'csindex', count: 300, members: [] }),
  })
  vi.stubGlobal('fetch', fetchMock)

  await api.hs300Current()
  expect(fetchMock.mock.calls[0][0]).toBe('/api/hs300/current')

  await api.hs300Current(true)
  expect(fetchMock.mock.calls[1][0]).toBe('/api/hs300/current?refresh=true')
})
