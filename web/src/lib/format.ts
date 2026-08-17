import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'

dayjs.extend(relativeTime)

/**
 * Number and date formatting.
 *
 * Percentages are signed wherever the sign carries meaning, because "3.1pp better" and
 * "3.1pp worse" must not be told apart by colour alone (UI brief section 5.7).
 */

/** A percentage the engine reported, at one decimal. `null` means non-finite. */
export function percent(value: number | null, options: { signed?: boolean } = {}): string {
  if (value === null) return 'n/a'
  const sign = options.signed && value > 0 ? '+' : ''
  return `${sign}${value.toFixed(1)}%`
}

/** Percentage points, for a difference between two percentages. Always signed. */
export function points(value: number | null): string {
  if (value === null) return 'n/a'
  return `${value > 0 ? '+' : ''}${value.toFixed(1)}pp`
}

export function ratio(value: number | null, digits = 2): string {
  if (value === null) return 'n/a'
  return value.toFixed(digits)
}

export function integer(value: number | null): string {
  if (value === null) return 'n/a'
  return value.toLocaleString()
}

export function money(value: number | null, digits = 2): string {
  if (value === null) return 'n/a'
  return value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

/** "3 minutes ago". Absolute time is always available alongside, never only on hover. */
export function relative(iso: string | null): string {
  if (!iso) return '—'
  return dayjs(iso).fromNow()
}

export function absolute(iso: string | null): string {
  if (!iso) return '—'
  return dayjs(iso).format('YYYY-MM-DD HH:mm')
}

export function dateOnly(iso: string | null): string {
  if (!iso) return '—'
  return dayjs(iso).format('YYYY-MM-DD')
}

/** Elapsed run time. Runs range from a second to tens of minutes. */
export function duration(seconds: number | null): string {
  if (seconds === null) return '—'
  if (seconds < 1) return '<1s'
  if (seconds < 60) return `${Math.round(seconds)}s`
  const minutes = Math.floor(seconds / 60)
  const rest = Math.round(seconds % 60)
  if (minutes < 60) return rest === 0 ? `${minutes}m` : `${minutes}m ${rest}s`
  const hours = Math.floor(minutes / 60)
  return `${hours}h ${minutes % 60}m`
}

const RUN_KIND_LABEL: Record<string, string> = {
  backtest: 'Backtest',
  optimize: 'Optimization',
  walk_forward: 'Walk-forward',
}

export function runKindLabel(kind: string): string {
  return RUN_KIND_LABEL[kind] ?? kind
}

/** The compact form used in list cells: `3 opt · 1 bt · 1 wf`. */
export function runCounts(counts: {
  optimize: number
  backtest: number
  walk_forward: number
}): string {
  const parts: string[] = []
  if (counts.optimize > 0) parts.push(`${counts.optimize} opt`)
  if (counts.backtest > 0) parts.push(`${counts.backtest} bt`)
  if (counts.walk_forward > 0) parts.push(`${counts.walk_forward} wf`)
  return parts.length > 0 ? parts.join(' · ') : 'none'
}
