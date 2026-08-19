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

/**
 * A bar's timestamp, at the precision the bar actually has.
 *
 * On a daily strategy the time is always midnight and printing it would be four characters of
 * noise on every row. On an intraday one it is the entire point: two trades on the same date
 * are indistinguishable without it, and "held 3 bars" cannot be checked against a table that
 * only shows days.
 *
 * Intraday timestamps are exchange-local wall clock with no zone attached (engine spec §4.3), so
 * they are formatted as written rather than converted — a Xetra bar reading 09:30 must not
 * become 08:30 because the reader is in London.
 */
export function barTime(iso: string | null, intraday: boolean): string {
  if (!iso) return '—'
  return dayjs(iso).format(intraday ? 'YYYY-MM-DD HH:mm' : 'YYYY-MM-DD')
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
  evolve: 'Evolution',
}

export function runKindLabel(kind: string): string {
  return RUN_KIND_LABEL[kind] ?? kind
}

/** The compact form used in list cells: `3 opt · 1 bt · 1 wf · 2 evo`. */
export function runCounts(counts: {
  optimize: number
  backtest: number
  walk_forward: number
  evolve?: number
}): string {
  const parts: string[] = []
  if (counts.optimize > 0) parts.push(`${counts.optimize} opt`)
  if (counts.backtest > 0) parts.push(`${counts.backtest} bt`)
  if (counts.walk_forward > 0) parts.push(`${counts.walk_forward} wf`)
  if (counts.evolve && counts.evolve > 0) parts.push(`${counts.evolve} evo`)
  return parts.length > 0 ? parts.join(' · ') : 'none'
}
