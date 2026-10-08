// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { HS300FilterToggle } from './HS300FilterToggle'

let host: HTMLDivElement
let root: Root

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

it('展示官方当前名单并提供开关/刷新回调', async () => {
  const onChange = vi.fn()
  const onRefresh = vi.fn()
  await act(async () => {
    root.render(
      <HS300FilterToggle
        enabled={false}
        onChange={onChange}
        memberCount={300}
        asOf="2026-09-30"
        source="csindex"
        onRefresh={onRefresh}
      />,
    )
  })

  expect(host.querySelector('[data-testid="hs300-filter-status"]')?.textContent).toContain('300 只')
  expect(host.querySelector('[data-testid="hs300-filter-status"]')?.textContent).toContain('2026-09-30')
  expect(host.querySelector('[data-testid="hs300-filter-status"]')?.textContent).toContain('中证指数')

  await act(async () => host.querySelector<HTMLInputElement>('[data-testid="hs300-filter-checkbox"]')!.click())
  expect(onChange).toHaveBeenCalledWith(true)
  await act(async () => host.querySelector<HTMLButtonElement>('[data-testid="hs300-refresh"]')!.click())
  expect(onRefresh).toHaveBeenCalledTimes(1)
  expect(host.querySelector('[data-testid="hs300-sync"]')).toBeNull()
})

it('官方名单不可用时展示错误且不提供快照回退', async () => {
  await act(async () => {
    root.render(
      <HS300FilterToggle
        enabled
        onChange={() => {}}
        memberCount={0}
        error="HS300 当前成分不可用"
      />,
    )
  })
  expect(host.querySelector('[data-testid="hs300-error"]')?.textContent).toContain('HS300 当前成分不可用')
  expect(host.textContent).not.toContain('快照日期')
})
