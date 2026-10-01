// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { UniverseSelector } from './UniverseSelector'

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

const SNAPSHOTS = ['2023-07-01', '2026-09-01']

function render(props: Partial<Parameters<typeof UniverseSelector>[0]> = {}) {
  return act(async () => root.render(
    <UniverseSelector
      snapshots={SNAPSHOTS}
      selectedDate="2026-09-01"
      onDateChange={() => {}}
      memberCount={300}
      onApply={() => {}}
      {...props}
    />,
  ))
}

it('展示快照选项与成分数', async () => {
  await render()

  const select = host.querySelector<HTMLSelectElement>('select[aria-label="HS300 快照日期"]')!
  expect(Array.from(select.options).map(option => option.value)).toEqual(SNAPSHOTS)
  expect(select.value).toBe('2026-09-01')
  expect(host.textContent).toContain('300 只')
  expect(host.textContent).toContain('成分快照从 2023-07-01 开始，之前日期不支持')
})

it('切换快照与点击应用都回调', async () => {
  const onDateChange = vi.fn()
  const onApply = vi.fn()
  await render({ onDateChange, onApply })

  const select = host.querySelector<HTMLSelectElement>('select')!
  await act(async () => {
    select.value = '2023-07-01'
    select.dispatchEvent(new Event('change', { bubbles: true }))
  })
  await act(async () => host.querySelector<HTMLButtonElement>('[data-testid="hs300-apply"]')!.click())

  expect(onDateChange).toHaveBeenCalledWith('2023-07-01')
  expect(onApply).toHaveBeenCalledTimes(1)
})

it('无成分时禁用应用并展示错误', async () => {
  await render({ memberCount: 0, error: '快照不可用' })

  expect(host.querySelector<HTMLButtonElement>('[data-testid="hs300-apply"]')!.disabled).toBe(true)
  expect(host.querySelector('[data-testid="hs300-error"]')?.textContent).toBe('快照不可用')
})

it('提供可触发的日K同步入口并展示同步结果', async () => {
  const onSync = vi.fn()
  await render({ onSync, syncHint: '已同步 123 行' })

  const button = host.querySelector<HTMLButtonElement>('[data-testid="hs300-sync"]')!
  expect(button.textContent).toContain('同步全部快照区间日K')
  expect(button.title).toContain('截至最新快照生效日')
  await act(async () => button.click())

  expect(onSync).toHaveBeenCalledTimes(1)
  expect(host.textContent).toContain('已同步 123 行')
})

it('同步期间禁用同步操作', async () => {
  await render({ onSync: vi.fn(), isSyncing: true })

  expect(host.querySelector<HTMLButtonElement>('[data-testid="hs300-sync"]')!.disabled).toBe(true)
})
