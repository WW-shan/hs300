import { Loader2, RefreshCw } from 'lucide-react'
import { cn } from '@/lib/cn'

export interface HS300FilterToggleProps {
  enabled: boolean
  onChange: (enabled: boolean) => void
  memberCount: number
  asOf?: string | null
  source?: string | null
  isLoading?: boolean
  error?: string | null
  onRefresh?: () => void
  isRefreshing?: boolean
  onSync?: () => void
  isSyncing?: boolean
  syncHint?: string | null
  disabled?: boolean
  className?: string
}

const SOURCE_LABELS: Record<string, string> = {
  csindex: '中证指数',
  em: '东方财富',
  cache: '缓存',
}

/**
 * HS300 动态筛选开关 — 只展示官方当前成分与该名单的新鲜度。
 *
 * 不提供快照日期下拉、不维护固定 300 池; 页面把 enabled 传给后端, 由后端按
 * 官方当前名单统一完成回测/因子/选股过滤。
 */
export function HS300FilterToggle({
  enabled,
  onChange,
  memberCount,
  asOf = null,
  source = null,
  isLoading = false,
  error = null,
  onRefresh,
  isRefreshing = false,
  onSync,
  isSyncing = false,
  syncHint = null,
  disabled = false,
  className = '',
}: HS300FilterToggleProps) {
  return (
    <div
      className={cn(
        'flex flex-wrap items-center gap-2 rounded-md border border-border px-2 py-1 text-xs',
        className,
      )}
      data-testid="hs300-filter-toggle"
    >
      <label className="inline-flex items-center gap-1 font-medium text-secondary">
        <input
          type="checkbox"
          checked={enabled}
          disabled={disabled || isLoading}
          onChange={event => onChange(event.target.checked)}
          data-testid="hs300-filter-checkbox"
        />
        只看沪深300
      </label>
      <span className="text-muted" data-testid="hs300-filter-status">
        {isLoading ? <Loader2 className="inline h-3 w-3 animate-spin" /> : `${memberCount} 只`}
        {!isLoading && asOf ? ` · ${asOf}` : ''}
        {!isLoading && source ? ` · ${SOURCE_LABELS[source] ?? source}` : ''}
      </span>
      {onRefresh && (
        <button
          type="button"
          className="inline-flex items-center gap-1 rounded border border-border px-1.5 py-0.5 hover:border-accent hover:text-accent disabled:cursor-not-allowed disabled:opacity-50"
          title="强制刷新官方当前成分"
          disabled={disabled || isRefreshing}
          onClick={onRefresh}
          data-testid="hs300-refresh"
        >
          <RefreshCw className={cn('h-3 w-3', isRefreshing && 'animate-spin')} />
          刷新名单
        </button>
      )}
      {onSync && (
        <button
          type="button"
          className="inline-flex items-center gap-1 rounded border border-border px-1.5 py-0.5 hover:border-accent hover:text-accent disabled:cursor-not-allowed disabled:opacity-50"
          title="按官方当前名单同步日K"
          disabled={disabled || isSyncing}
          onClick={onSync}
          data-testid="hs300-sync"
        >
          <RefreshCw className={cn('h-3 w-3', isSyncing && 'animate-spin')} />
          同步日K
        </button>
      )}
      {syncHint && <span className="text-accent" data-testid="hs300-sync-hint">{syncHint}</span>}
      {error && <span className="text-loss" data-testid="hs300-error">{error}</span>}
      <span className="text-muted" data-testid="hs300-filter-note">
        官方动态名单, 不读快照归档; 历史回测使用当前成分存在幸存者偏差。
      </span>
    </div>
  )
}
