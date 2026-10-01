// @vitest-environment jsdom
import { expect, it } from 'vitest'

type VitestJSDOMGlobal = typeof globalThis & {
  jsdom?: { window?: Window }
}

it('uses jsdom-scoped browser storage globals', () => {
  const jsdomWindow = (globalThis as VitestJSDOMGlobal).jsdom?.window

  expect(localStorage).toBe(jsdomWindow?.localStorage)
  expect(sessionStorage).toBe(jsdomWindow?.sessionStorage)
})
