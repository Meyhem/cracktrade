import { describe, expect, it } from 'vitest'
import {
  availableKinds,
  buildRows,
  defaultKind,
  figuresOfRun,
  speakingRun,
  tradesOfRun,
} from './table'
import type { Run, RunDetailNarrowed } from '../../api/types'

function run(overrides: Partial<Run> = {}): Run {
  return {
    id: 'run-1',
    number: 1,
    kind: 'backtest',
    status: 'succeeded',
    strategy: { id: 's', name: 'momentum' },
    version: 1,
    stale: false,
    params: {},
    seed: 0,
    queued_at: '2026-01-01T00:00:00Z',
    started_at: null,
    finished_at: null,
    elapsed_seconds: null,
    progress: null,
    failure_category: null,
    headline: null,
    promotable: false,
    cancel_requested: false,
    ...overrides,
  } as Run
}

describe('speakingRun', () => {
  it('picks the latest run, never the best', () => {
    // Deliberate: optimization and walk-forward are seeded searches, so taking the maximum over
    // repeated runs of one config is picking the best of N trials — uncounted by the deflated
    // Sharpe, and rewarded on the very screen that warns against it.
    const runs = [
      run({ id: 'old', queued_at: '2026-01-01T00:00:00Z' }),
      run({ id: 'new', queued_at: '2026-03-01T00:00:00Z' }),
    ]
    expect(speakingRun(runs, 1, 'backtest')?.id).toBe('new')
  })

  it('ignores runs that did not succeed', () => {
    const runs = [
      run({ id: 'failed', status: 'failed', queued_at: '2026-05-01T00:00:00Z' }),
      run({ id: 'ok', queued_at: '2026-01-01T00:00:00Z' }),
    ]
    expect(speakingRun(runs, 1, 'backtest')?.id).toBe('ok')
  })

  it('ignores runs against other versions and other kinds', () => {
    const runs = [
      run({ id: 'other-version', version: 2 }),
      run({ id: 'other-kind', kind: 'optimize' }),
    ]
    expect(speakingRun(runs, 1, 'backtest')).toBeNull()
  })
})

describe('figuresOfRun', () => {
  it('reads a backtest through the benchmark comparison rather than subtracting', () => {
    const figures = figuresOfRun('backtest', {
      metrics: {
        total_return_pct: 42,
        max_drawdown_pct: -18,
        sharpe_ratio: 1.1,
        total_trades: 30,
        has_enough_trades_to_judge: true,
      },
      benchmark: { excess_return_pct: -40, benchmark: { total_return_pct: 82 } },
    })

    expect(figures).toEqual({
      returnPct: 42,
      excessPp: -40,
      maxDrawdownPct: -18,
      sharpe: 1.1,
      foldWinRate: null,
    })
  })

  it('withholds every figure for a backtest below the trade floor', () => {
    const figures = figuresOfRun('backtest', {
      metrics: { total_return_pct: 42, total_trades: 6, has_enough_trades_to_judge: false },
    })
    expect(figures).toBeNull()
  })

  it('leaves a walk-forward without a combined drawdown, Sharpe or excess', () => {
    // Not an omission. A ValidationReport has per-fold metrics and a benchmark and nothing
    // else, because each fold re-optimizes — so there is no single curve to take a drawdown of.
    const figures = figuresOfRun('walk_forward', {
      folds: [
        {
          metrics: { total_return_pct: 5, has_enough_trades_to_judge: true },
          was_profitable: true,
        },
        {
          metrics: { total_return_pct: -2, has_enough_trades_to_judge: true },
          was_profitable: false,
        },
      ],
      combined_return_pct: 2.9,
      fold_win_rate: 0.5,
      benchmark: { total_return_pct: 10 },
    })

    expect(figures?.returnPct).toBe(2.9)
    expect(figures?.foldWinRate).toBe(0.5)
    expect(figures?.maxDrawdownPct).toBeNull()
    expect(figures?.sharpe).toBeNull()
    expect(figures?.excessPp).toBeNull()
  })

  it('withholds a walk-forward whose every fold is below the floor', () => {
    const figures = figuresOfRun('walk_forward', {
      folds: [
        { metrics: { total_return_pct: 5, has_enough_trades_to_judge: false } },
        { metrics: { total_return_pct: 1, has_enough_trades_to_judge: false } },
      ],
      combined_return_pct: 6,
      total_trades: 4,
    })
    expect(figures).toBeNull()
  })

  it('reads an optimization from its test metrics, never its train metrics', () => {
    const figures = figuresOfRun('optimize', {
      train_metrics: { total_return_pct: 900, has_enough_trades_to_judge: true },
      test_metrics: {
        total_return_pct: 7.8,
        max_drawdown_pct: -22,
        sharpe_ratio: 0.4,
        has_enough_trades_to_judge: true,
      },
      benchmark: { excess_return_pct: -74.8 },
    })

    expect(figures?.returnPct).toBe(7.8)
    expect(figures?.excessPp).toBe(-74.8)
  })

  it('leaves an optimization recorded before D-15 without an excess figure', () => {
    const figures = figuresOfRun('optimize', {
      test_metrics: { total_return_pct: 7.8, has_enough_trades_to_judge: true },
    })
    expect(figures?.returnPct).toBe(7.8)
    expect(figures?.excessPp).toBeNull()
  })
})

describe('tradesOfRun', () => {
  it('reports the trade count even when the figures are withheld', () => {
    // The count is the evidence for the suppression, not a claim about performance.
    const result = {
      metrics: { total_trades: 6, has_enough_trades_to_judge: false, total_return_pct: 42 },
    }
    expect(figuresOfRun('backtest', result)).toBeNull()
    expect(tradesOfRun('backtest', result)).toBe(6)
  })
})

describe('buildRows', () => {
  const detail = (result: Record<string, unknown>): RunDetailNarrowed =>
    ({
      run: run(),
      result,
      error: null,
      checks: [],
      config_diff: [],
    }) as unknown as RunDetailNarrowed

  it('orders oldest first, so the strategy reads as a progression', () => {
    const rows = buildRows({
      versions: [{ version: 3 }, { version: 1 }, { version: 2 }],
      runs: [],
      kind: 'backtest',
      details: new Map(),
    })
    expect(rows.map((row) => row.version)).toEqual([1, 2, 3])
  })

  it('gives a version with no run of the selected kind an empty row, not a zeroed one', () => {
    const rows = buildRows({
      versions: [{ version: 1 }],
      runs: [run({ kind: 'optimize' })],
      kind: 'backtest',
      details: new Map(),
    })
    expect(rows[0]?.run).toBeNull()
    expect(rows[0]?.runs).toBe(0)
    expect(rows[0]?.pending).toBe(false)
  })

  it('counts every succeeded run against a version, not only the one it reports', () => {
    const runs = [
      run({ id: 'a', queued_at: '2026-01-01T00:00:00Z' }),
      run({ id: 'b', queued_at: '2026-02-01T00:00:00Z' }),
    ]
    const rows = buildRows({
      versions: [{ version: 1 }],
      runs,
      kind: 'backtest',
      details: new Map([['b', detail({ metrics: { has_enough_trades_to_judge: true } })]]),
    })
    expect(rows[0]?.runs).toBe(2)
    expect(rows[0]?.run?.runId).toBe('b')
  })

  it('reports a row as pending rather than empty while its detail is loading', () => {
    const rows = buildRows({
      versions: [{ version: 1 }],
      runs: [run()],
      kind: 'backtest',
      details: new Map(),
    })
    expect(rows[0]?.pending).toBe(true)
    expect(rows[0]?.run).toBeNull()
  })

  it('prefers the objective the result recorded over the one the request omitted', () => {
    const rows = buildRows({
      versions: [{ version: 1 }],
      runs: [run({ id: 'w', kind: 'walk_forward', params: {} })],
      kind: 'walk_forward',
      details: new Map([
        ['w', detail({ objective: 'sharpe_ratio', scheme: 'rolling', folds: [{ metrics: {} }] })],
      ]),
    })
    expect(rows[0]?.run?.objective).toBe('sharpe_ratio')
    expect(rows[0]?.run?.folds).toBe(1)
  })
})

describe('kind selection', () => {
  it('lists only kinds with a succeeded run', () => {
    const runs = [run({ kind: 'backtest' }), run({ kind: 'optimize', status: 'failed' })]
    expect(availableKinds(runs)).toEqual(['backtest'])
  })

  it('defaults to walk-forward, the only kind that issues a verdict', () => {
    const runs = [run({ kind: 'backtest' }), run({ kind: 'walk_forward' })]
    expect(defaultKind(runs)).toBe('walk_forward')
  })

  it('falls back to backtest before optimization', () => {
    const runs = [run({ kind: 'optimize' }), run({ kind: 'backtest' })]
    expect(defaultKind(runs)).toBe('backtest')
  })

  it('has no kind at all when nothing has succeeded', () => {
    expect(defaultKind([run({ status: 'failed' })])).toBeNull()
  })
})
