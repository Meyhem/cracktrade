/**
 * Reading the stored chart series, and the two ways they may legitimately be combined.
 *
 * A captured series is written once, while the run executes, and never recomputed — the
 * provider retroactively adjusts prices, so a curve rebuilt later would describe different
 * data than the metrics printed beside it (spec §8.1). Everything here therefore *reads*.
 * Nothing recomputes an equity curve, a drawdown or a monthly return from something else.
 *
 * The interesting part is what refuses to combine. A walk-forward re-optimizes per fold, so
 * the folds are different strategies, and joining them is only ever valid where the join can
 * be shown not to mix two of them (§12.9). `stitchMonthly` is that case and marks its own
 * seams; the twelve-month and calendar-year aggregates are not, and say so instead of
 * producing a number.
 */

import type { SeriesPoints } from '../../api/types'
import type { Trade } from '../../lib/result'

export type Point = { date: string; value: number }

/** One calendar month. `inMarket` false renders as "no trades", never as 0%. */
export type MonthlyCell = {
  month: string
  year: number
  monthIndex: number
  value: number
  inMarket: boolean
  /** Which fold contributed it, for a stitched view. `null` for a single-window run. */
  fold: number | null
  /** True when more than one fold contributed to this month. See `stitchMonthly`. */
  seam: boolean
}

/** The names the worker stores. Kept here so a typo is a type error, not an empty chart. */
export const SERIES = {
  equity: 'equity',
  benchmarkEquity: 'benchmark_equity',
  drawdown: 'drawdown',
  close: 'close',
  monthly: 'monthly_returns',
  rolling12m: 'rolling_12m_return',
  filled: 'filled',
} as const

function column(payload: SeriesPoints | undefined, key: string): unknown[] {
  const value = payload?.points[key]
  return Array.isArray(value) ? value : []
}

function strings(payload: SeriesPoints | undefined, key: string): string[] {
  return column(payload, key).filter((item): item is string => typeof item === 'string')
}

/**
 * A dated series.
 *
 * A date with no finite value is dropped rather than plotted at zero: `serialize` writes a
 * non-finite float as null (spec §11), and zero is a measurement.
 */
export function pointsOf(payload: SeriesPoints | undefined): Point[] {
  const dates = column(payload, 'dates')
  const values = column(payload, 'values')
  const points: Point[] = []
  for (let index = 0; index < dates.length; index += 1) {
    const date = dates[index]
    const value = values[index]
    if (typeof date === 'string' && typeof value === 'number' && Number.isFinite(value)) {
      points.push({ date, value })
    }
  }
  return points
}

/** The dates of bars that were forward-filled rather than observed (spec §8.1). */
export function filledDatesOf(payload: SeriesPoints | undefined): string[] {
  return strings(payload, 'dates')
}

export function monthlyOf(payload: SeriesPoints | undefined, fold: number | null): MonthlyCell[] {
  const months = column(payload, 'months')
  const values = column(payload, 'values')
  const inMarket = column(payload, 'in_market')
  const cells: MonthlyCell[] = []

  for (let index = 0; index < months.length; index += 1) {
    const month = months[index]
    const value = values[index]
    if (typeof month !== 'string') continue
    const [year, monthNumber] = month.split('-')
    cells.push({
      month,
      year: Number(year),
      monthIndex: Number(monthNumber),
      value: typeof value === 'number' && Number.isFinite(value) ? value : 0,
      // Only an explicit `true` counts. A month whose flag did not arrive is not evidence
      // that the strategy held a position through it.
      inMarket: inMarket[index] === true,
      fold,
      seam: false,
    })
  }
  return cells
}

/**
 * The stretches of time a position was open, for shading the price chart.
 *
 * An open trade has no right edge — `to` stays null — because the position has not ended and
 * drawing one would put a date on an event that has not happened.
 */
export function inPositionSpans(trades: Trade[]): { from: string; to: string | null }[] {
  return trades
    .filter((trade): trade is Trade & { entryDate: string } => trade.entryDate !== null)
    .map((trade) => ({ from: trade.entryDate, to: trade.isOpen ? null : trade.exitDate }))
}

/**
 * Fold monthly returns laid end to end.
 *
 * The fold test windows are contiguous, non-overlapping and strictly ordered (spec §12.1), so
 * a month belongs to exactly one fold — except at a boundary, where a fold ends mid-month and
 * the next begins. Those months have a partial return from each of two different parameter
 * vectors, and compounding them would state a monthly return for a strategy that was never
 * run in that month.
 *
 * They are marked `seam` and rendered as their own state rather than as a number. There are at
 * most as many as there are boundaries, and the alternative is a handful of cells that are
 * quietly wrong in a chart whose entire job is to show consistency.
 */
export function stitchMonthly(perFold: { fold: number; cells: MonthlyCell[] }[]): MonthlyCell[] {
  const byMonth = new Map<string, MonthlyCell>()

  for (const { fold, cells } of perFold) {
    for (const cell of cells) {
      const existing = byMonth.get(cell.month)
      if (existing === undefined) {
        byMonth.set(cell.month, { ...cell, fold })
        continue
      }
      byMonth.set(cell.month, {
        ...existing,
        seam: true,
        // A seam month reports no return at all. Keeping either fold's half would be picking
        // one of two answers, and neither of them is the month's return.
        value: 0,
        inMarket: existing.inMarket || cell.inMarket,
      })
    }
  }

  return [...byMonth.values()].sort((left, right) => left.month.localeCompare(right.month))
}

/** Calendar years present in a set of monthly cells, ascending. */
export function yearsOf(cells: MonthlyCell[]): number[] {
  return [...new Set(cells.map((cell) => cell.year))].sort((left, right) => left - right)
}
