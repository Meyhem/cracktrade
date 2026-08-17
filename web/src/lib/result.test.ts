import { describe, expect, it } from 'vitest'
import {
  asBoolean,
  asNumber,
  backtestResult,
  closedTrades,
  figuresOf,
  metricsOf,
  optimizationResult,
  tradesOf,
  validationResult,
} from './result'

/**
 * The guards, and specifically the three places a wrong answer would look right.
 *
 * Everything here is about refusing to invent a value. The engine's blob is not covered by the
 * OpenAPI schema, so nothing above this module can be typechecked against the server — these
 * tests are the only thing standing between a payload change and a screen confidently rendering
 * a number that is not there.
 */

describe('asBoolean', () => {
  it('refuses the string "False", which is truthy', () => {
    // The regression that motivated the guard: Trade.is_open arrived as a numpy scalar that
    // the serializer rendered with str(). Boolean('False') === true, so a coercing reader
    // marks every closed trade open and drops the entire sample out of every aggregate.
    expect(asBoolean('False')).toBeNull()
    expect(asBoolean('True')).toBeNull()
    expect(asBoolean(0)).toBeNull()
    expect(asBoolean(1)).toBeNull()
    expect(asBoolean(false)).toBe(false)
    expect(asBoolean(true)).toBe(true)
  })
})

describe('asNumber', () => {
  it('treats a non-finite value as no value', () => {
    expect(asNumber(Infinity)).toBeNull()
    expect(asNumber(NaN)).toBeNull()
    expect(asNumber('12')).toBeNull()
    expect(asNumber(0)).toBe(0)
  })
})

describe('figuresOf', () => {
  const metrics = (overrides: Record<string, unknown>) =>
    metricsOf({ total_trades: 6, total_return_pct: 149, ...overrides })

  it('withholds every figure below the trade floor', () => {
    const figures = figuresOf(metrics({ has_enough_trades_to_judge: false }))
    expect(figures.shown).toBe(false)
    expect(figures.metrics).toBeNull()
  })

  it('still reports the trade count, which is the evidence for withholding', () => {
    const figures = figuresOf(metrics({ has_enough_trades_to_judge: false }))
    expect(figures.shown === false && figures.trades).toBe(6)
  })

  it('shows figures once the engine says there are enough trades', () => {
    const figures = figuresOf(metrics({ has_enough_trades_to_judge: true }))
    expect(figures.shown).toBe(true)
    expect(figures.shown === true && figures.metrics.totalReturnPct).toBe(149)
  })

  it('does not withhold when the engine did not say either way', () => {
    // Absence of the flag is not a claim that the sample is too small. Withholding here would
    // suppress figures the engine was willing to publish, which is its own kind of dishonesty.
    expect(figuresOf(metrics({})).shown).toBe(true)
  })
})

describe('trades', () => {
  const payload = {
    trades: [
      { entry_date: '2020-01-01', pnl: 10, is_open: false, is_winner: true, size: 1 },
      { entry_date: '2020-02-01', pnl: 5, is_open: true, is_winner: true, size: 1 },
      { entry_date: '2020-03-01', pnl: -3, is_open: false, is_winner: false, size: 1 },
    ],
  }

  it('keeps open trades out of the closed set', () => {
    expect(closedTrades(tradesOf(payload)).map((trade) => trade.pnl)).toEqual([10, -3])
  })

  it('reads an unreadable open flag as closed rather than dropping the trade', () => {
    const broken = tradesOf({ trades: [{ entry_date: '2020-01-01', is_open: 'False' }] })
    expect(broken[0]?.isOpen).toBe(false)
    expect(closedTrades(broken)).toHaveLength(1)
  })
})

describe('the optimization result', () => {
  it('names the parameters that finished on a bound', () => {
    // `parameters_at_bound` is a list of ParameterChange objects, not of names. Read as
    // strings it filtered down to nothing every time, which left the "widen the range and run
    // again" warning permanently hidden while the per-row badge kept rendering — a missing
    // warning that looked exactly like a run with nothing to warn about.
    const result = optimizationResult({
      parameters_at_bound: [
        { path: 'indicators.sma_long.window', old_value: 200, new_value: 300, at_bound: true },
      ],
    })
    expect(result.parametersAtBound).toEqual(['indicators.sma_long.window'])
  })

  it('reports no benchmark for a run recorded before the engine computed one', () => {
    // Older runs are not backfilled (D-16). Null has to stay null: `improvement_pct` answers
    // whether the search did anything, and passing it off as the benchmark would answer a
    // different question under the same label.
    expect(optimizationResult({ improvement_pct: 4 }).benchmark).toBeNull()
  })
})

describe('the folds of a walk-forward', () => {
  it('keeps each fold’s trades with the fold that took them', () => {
    const report = validationResult({
      folds: [
        { index: 0, trades: [{ entry_date: '2021-03-02', pnl: 12, is_open: false }] },
        { index: 1, trades: [{ entry_date: '2022-06-08', pnl: -4, is_open: false }] },
      ],
    })
    expect(report.folds[0]?.trades.map((trade) => trade.pnl)).toEqual([12])
    expect(report.folds[1]?.trades.map((trade) => trade.pnl)).toEqual([-4])
  })

  it('reads a fold recorded before trades were kept as an empty list', () => {
    const report = validationResult({ folds: [{ index: 0, metrics: { total_trades: 9 } }] })
    expect(report.folds[0]?.trades).toEqual([])
  })
})

describe('reading a whole result', () => {
  it('survives a payload with nothing in it', () => {
    // A run that landed succeeded with a shape nobody expected must not take the screen down.
    const empty = backtestResult(null)
    expect(empty.trades).toEqual([])
    expect(empty.metrics).toBeNull()
    expect(empty.shadowedStops).toEqual([])
  })

  it('reads the verdict from the engine rather than recomputing it', () => {
    const report = validationResult({
      is_credible: false,
      failures: ['only 16 out-of-sample trades in total'],
      folds: [{ index: 0, metrics: { total_return_pct: 4 }, parameters: { 'a.b': 3 } }],
      profitable_folds: 1,
      fold_win_rate: 1,
    })
    expect(report.isCredible).toBe(false)
    expect(report.failures).toEqual(['only 16 out-of-sample trades in total'])
    expect(report.folds[0]?.parameters).toEqual({ 'a.b': 3 })
  })

  it('does not infer credibility when the engine did not state it', () => {
    expect(validationResult({}).isCredible).toBeNull()
  })
})
