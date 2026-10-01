import { Loader2, Play, RefreshCw } from 'lucide-react'
import { cn } from '@/lib/cn'

export interface UniverseSelectorProps {
  /** 可选快照生效日 (YYYY-MM-01), 升序 */
  snapshots: string[]
  /** 当前选中的快照生效日 */
  selectedDate: string | null
  onDateChange: (date: string) => void
  /** 选中快照的成分股数量 */
  memberCount: number
  /** 快照起点提示 (快照覆盖 2023-07 起) */
  earliest?: string | null
  isLoading?: boolean
  error?: string | null
  /** 点击「应用 HS300」时回调 */
  onApply: () => void
  /** 点击「同步全部快照区间日K」时回调 */
  onSync?: () => void
  isSyncing?: boolean
  syncHint?: string | null
  /** 应用后的提示文案 (如「已应用 300 只」) */
  appliedHint?: string | null
  disabled?: boolean
  className?: string
}

/**
 * HS300 宇宙选择器 — 快照日期下拉 + 成分数 + 应用按钮。
 *
 * 仅负责选择与回调; 各页面自行决定把成分用于 pool (筛选) 还是 symbols (因子/回测)。
 */
export function UniverseSelector({
  snapshots,
  selectedDate,
  onDateChange,
  memberCount,
  earliest,
  isLoading = false,
  error = null,
  onApply,
  onSync,
  isSyncing = false,
  syncHint = null,
  appliedHint = null,
  disabled = false,
  className = '',
}: UniverseSelectorProps) {
  const busy = isLoading
  return (
    <div
      className={cn(
        'flex flex-wrap items-center gap-2 rounded-md border border-border px-2 py-1 text-xs',
        className,
      )}
      data-testid="hs300-universe-selector"
    >
      <span className="font-medium text-muted">HS300</span>
      <select
        aria-label="HS300 快照日期"
        className="rounded border border-border bg-transparent px-1 py-0.5 text-xs"
        value={selectedDate ?? ''}
        disabled={disabled || busy || snapshots.length === 0}
        onChange={event => onDateChange(event.target.value)}
      >
        {snapshots.length === 0 && <option value="">无可用快照</option>}
        {snapshots.map(date => (
          <option key={date} value={date}>{date}</option>
        ))}
      </select>
      <span className="text-muted">
        {busy ? <Loader2 className="inline h-3 w-3 animate-spin" /> : `${memberCount} 只`}
      </span>
      <button
        type="button"
        className="inline-flex items-center gap-1 rounded border border-border px-1.5 py-0.5 hover:border-accent hover:text-accent disabled:cursor-not-allowed disabled:opacity-50"
        disabled={disabled || busy || !selectedDate || memberCount === 0}
        onClick={onApply}
        data-testid="hs300-apply"
      >
        <Play className="h-3 w-3" />
        应用 HS300
      </button>
      {onSync && (
        <button
          type="button"
          className="inline-flex items-center gap-1 rounded border border-border px-1.5 py-0.5 hover:border-accent hover:text-accent disabled:cursor-not-allowed disabled:opacity-50"
          title="同步区间截至最新快照生效日；最新行情仍可通过常规日线同步更新。"
          disabled={disabled || isSyncing || snapshots.length === 0}
          onClick={onSync}
          data-testid="hs300-sync"
        >
          <RefreshCw className={cn('h-3 w-3', isSyncing && 'animate-spin')} />
          同步全部快照区间日K
        </button>
      )}
      {appliedHint && <span className="text-accent" data-testid="hs300-applied">{appliedHint}</span>}
      {syncHint && <span className="text-accent" data-testid="hs300-sync-hint">{syncHint}</span>}
      {error && <span className="text-loss" data-testid="hs300-error">{error}</span>}
      <span className="text-muted" data-testid="hs300-snapshot-limit">
        成分快照从 2023-07-01 开始，之前日期不支持。
      </span>
      {!error && snapshots.length === 0 && !busy && (
        <span className="text-muted">
          暂无快照{earliest ? `（快照起点 ${earliest}）` : ''}
        </span>
      )}
    </div>
  )
}
