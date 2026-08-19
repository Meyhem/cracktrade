import dayjs from 'dayjs'
import type { BarInterval, IntervalOption } from '../api/types'

/**
 * Bar-interval choices: what to call them, what to warn about, and what date range to offer.
 *
 * The *facts* here come from `/meta` — which intervals exist and how far back each one reaches
 * — because restating them in TypeScript is how a client ends up offering a value the engine
 * refuses. What lives here is presentation and arithmetic on top of those facts.
 *
 * Engine spec §3.3 and §4.2.
 */

/** Years of daily history a new strategy starts with, unchanged from before intervals existed. */
const DAILY_DEFAULT_YEARS = 3

/**
 * Days shaved off an interval's reach when defaulting the range.
 *
 * The provider measures its window from the moment of the request, so a default sitting exactly
 * on the limit races the clock: a form filled in at 09:00 and submitted at 09:30 would be asking
 * for a range that has since fallen out of reach.
 */
const REACH_MARGIN_DAYS = 2

export const INTERVAL_LABELS: Record<BarInterval, string> = {
  '15m': '15 minutes',
  '30m': '30 minutes',
  '1h': '1 hour',
  '1d': 'Daily',
}

/** One sentence under each choice: the constraint that decides whether it is usable. */
export function intervalHint(option: IntervalOption): string {
  if (!option.intraday)
    return 'No history limit. Positions may be held for as long as the rules say.'
  const reach =
    option.max_lookback_days === null
      ? ''
      : ` Data reaches back about ${option.max_lookback_days} days and no further.`
  return `Positions are closed at each session's end — nothing is held overnight.${reach}`
}

/**
 * The date range a new strategy of this interval should start with.
 *
 * Intraday ends **yesterday**, not today. Today's session is still open, so its last bar has not
 * closed; asking for it fetches a partial bar the engine then drops, which makes the range the
 * user sees and the range the run used differ for no reason they could have predicted.
 *
 * The start is the interval's own reach rather than a fixed span, because a default wider than
 * the reach would make every new intraday strategy invalid the moment it was created.
 */
export function defaultRange(option: Pick<IntervalOption, 'intraday' | 'max_lookback_days'>): {
  start_date: string
  end_date: string
} {
  if (!option.intraday) {
    return {
      start_date: dayjs().subtract(DAILY_DEFAULT_YEARS, 'year').format('YYYY-MM-DD'),
      end_date: dayjs().format('YYYY-MM-DD'),
    }
  }
  const end = dayjs().subtract(1, 'day')
  const span = (option.max_lookback_days ?? 0) - REACH_MARGIN_DAYS
  return {
    start_date: end.subtract(Math.max(span, 1), 'day').format('YYYY-MM-DD'),
    end_date: end.format('YYYY-MM-DD'),
  }
}

/**
 * Whether a requested range is wider than the interval can serve, as a message or null.
 *
 * A mirror of the engine's parse-time check, and deliberately the *width* of the range rather
 * than how old its start is. Age is not decidable here: it changes with the calendar, so a range
 * that is fine today is stale in two months, and a client that refused on age would start
 * rejecting saved strategies it had accepted. Width is a property of the range itself and never
 * changes, which is why it is the half of the rule that can be checked in a form.
 *
 * The server remains the authority — it refuses at fetch time with the actual dates. This only
 * saves the round trip, so it must never be *stricter* than the server.
 */
export function rangeTooWide(option: IntervalOption, start: string, end: string): string | null {
  if (option.max_lookback_days === null) return null
  const days = dayjs(end).diff(dayjs(start), 'day')
  if (days <= option.max_lookback_days) return null
  return (
    `${INTERVAL_LABELS[option.value]} bars are only served for about ` +
    `${option.max_lookback_days} days, and this range spans ${days}. ` +
    `Start on ${dayjs(end).subtract(option.max_lookback_days, 'day').format('YYYY-MM-DD')} ` +
    `or later, or choose a wider bar interval.`
  )
}
