import { describe, expect, it } from 'vitest'
import { filledDatesOf, inPositionSpans, monthlyOf, pointsOf, stitchMonthly } from './series'
import { chartableRuns, defaultRun, foldLabel, seriesFold, whyThisRun } from './selection'
import { run as runFixture } from '../../test/fixtures'
import type { SeriesPoints } from '../../api/types'
import type { Trade } from '../../lib/result'

function series(points: Record<string, unknown>): SeriesPoints {
  return { name: 'equity', fold: 0, points }
}

describe('reading a stored series', () => {
  it('drops a bar whose value the engine could not compute', () => {
    // `serialize` writes a non-finite float as null (spec §11). Plotting it at zero would put
    // a measurement where the engine explicitly declined to make one.
    const points = pointsOf(
      series({ dates: ['2020-01-01', '2020-01-02', '2020-01-03'], values: [10, null, 12] }),
    )
    expect(points).toEqual([
      { date: '2020-01-01', value: 10 },
      { date: '2020-01-03', value: 12 },
    ])
  })

  it('reads an absent series as no points rather than throwing', () => {
    expect(pointsOf(undefined)).toEqual([])
    expect(filledDatesOf(undefined)).toEqual([])
  })

  it('keeps a month with no position distinct from a month that ended flat', () => {
    const cells = monthlyOf(
      series({ months: ['2020-01', '2020-02'], values: [0, 0], in_market: [true, false] }),
      0,
    )
    expect(cells.map((cell) => cell.inMarket)).toEqual([true, false])
    expect(cells[0]?.year).toBe(2020)
    expect(cells[1]?.monthIndex).toBe(2)
  })

  it('does not treat a missing in-market flag as evidence of a position', () => {
    const cells = monthlyOf(series({ months: ['2020-01'], values: [3] }), 0)
    expect(cells[0]?.inMarket).toBe(false)
  })
})

describe('in-position spans', () => {
  const open: Trade = {
    entryDate: '2021-06-01',
    exitDate: null,
    entryPrice: 100,
    exitPrice: null,
    size: 1,
    pnl: 0,
    returnPct: 0,
    fees: 0,
    holdingDays: 10,
    isOpen: true,
    isWinner: false,
  }

  it('leaves an open position with no right edge', () => {
    // A closing date on a position that has not closed is a date for an event that has not
    // happened.
    expect(inPositionSpans([open])).toEqual([{ from: '2021-06-01', to: null }])
  })
})

describe('stitching fold months', () => {
  const cells = (fold: number, months: string[], values: number[]) =>
    monthlyOf(series({ months, values, in_market: months.map(() => true) }), fold)

  it('lays contiguous folds end to end', () => {
    const stitched = stitchMonthly([
      { fold: 1, cells: cells(1, ['2020-01', '2020-02'], [3, 4]) },
      { fold: 2, cells: cells(2, ['2020-03', '2020-04'], [5, 6]) },
    ])
    expect(stitched.map((cell) => cell.month)).toEqual(['2020-01', '2020-02', '2020-03', '2020-04'])
    expect(stitched.map((cell) => cell.value)).toEqual([3, 4, 5, 6])
    expect(stitched.some((cell) => cell.seam)).toBe(false)
  })

  it('refuses to compound a month two folds each half-ran', () => {
    // The folds used different parameters. Combining their partial months would state a
    // monthly return for a strategy that was never traded in that month (spec §12.9).
    const stitched = stitchMonthly([
      { fold: 1, cells: cells(1, ['2020-01', '2020-02'], [3, 2]) },
      { fold: 2, cells: cells(2, ['2020-02', '2020-03'], [1, 6]) },
    ])
    const boundary = stitched.find((cell) => cell.month === '2020-02')
    expect(boundary?.seam).toBe(true)
    expect(boundary?.value).toBe(0)
    expect(stitched.filter((cell) => cell.seam)).toHaveLength(1)
  })
})

describe('which run the charts describe', () => {
  const succeeded = (overrides: Parameters<typeof runFixture>[0]) =>
    runFixture({ status: 'succeeded', ...overrides })

  it('prefers a walk-forward over a newer optimization', () => {
    // Seriousness before recency: a walk-forward is the only run that was asked to survive
    // data it never saw, and defaulting to whatever finished last would quietly demote it.
    const runs = [
      succeeded({ id: 'opt', kind: 'optimize', finished_at: '2026-08-17T10:00:00Z' }),
      succeeded({ id: 'wf', kind: 'walk_forward', finished_at: '2026-08-01T10:00:00Z' }),
    ]
    expect(defaultRun(runs)?.id).toBe('wf')
  })

  it('takes the most recent within a kind', () => {
    const runs = [
      succeeded({ id: 'old', kind: 'backtest', finished_at: '2026-08-01T10:00:00Z' }),
      succeeded({ id: 'new', kind: 'backtest', finished_at: '2026-08-16T10:00:00Z' }),
    ]
    expect(defaultRun(runs)?.id).toBe('new')
  })

  it('never offers a run that did not succeed', () => {
    // A failed or cancelled run has no result, and a chart drawn from one would be a chart of
    // nothing presented as a chart of something.
    const runs = [
      runFixture({ id: 'failed', status: 'failed' }),
      runFixture({ id: 'running', status: 'running' }),
      succeeded({ id: 'ok' }),
    ]
    expect(chartableRuns(runs).map((run) => run.id)).toEqual(['ok'])
  })

  it('says in words why this run is the one on screen', () => {
    const only = [succeeded({ id: 'ok' })]
    expect(whyThisRun(only[0]!, only)).toBe('the only run that has finished')

    const both = [
      succeeded({ id: 'wf', kind: 'walk_forward' }),
      succeeded({ id: 'bt', kind: 'backtest' }),
    ]
    expect(whyThisRun(both[1]!, both)).toMatch(/more serious runs are available/)
  })
})

describe('fold numbering', () => {
  it('translates a zero-based fold index into the one-based series fold', () => {
    // `FoldResult.index` counts from zero; series are stored per fold from one, and fold 0
    // means "the whole run". Confusing the two draws one fold's curve under another's label.
    expect(seriesFold('walk_forward', 0)).toBe(1)
    expect(seriesFold('walk_forward', 2)).toBe(3)
    expect(seriesFold('walk_forward', null)).toBeNull()
    expect(seriesFold('backtest', null)).toBe(0)
    expect(foldLabel(0)).toBe('Fold 1')
    expect(foldLabel(null)).toBe('Combined (all folds)')
  })
})
