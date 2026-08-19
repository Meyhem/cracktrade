import type { EngineMeta, Run, StrategyDetail, StrategyRow } from '../api/types'

/**
 * Fixtures shaped like the real payloads.
 *
 * These are trimmed from responses captured off a running server rather than invented, so a
 * test that passes here is testing against what the API actually sends. Where a field is
 * only ever absent below the trade floor, it is absent here too — that absence is the thing
 * several of these tests exist to check.
 */

export const testMeta: EngineMeta = {
  engine_version: '0.1.0',
  trade_floor: 20,
  significance: 0.95,
  instability_threshold: 0.5,
  objectives: ['calmar', 'sortino', 'sharpe', 'legacy_pnl'],
  fold_schemes: ['anchored', 'rolling'],
  evolution_warmup_bars: 200,
  defaults: {
    backtest: {
      objective: 'calmar',
      epochs: 0,
      folds: null,
      scheme: null,
      cache: null,
      min_trades: null,
      min_trades_per_year: null,
    },
    optimize: {
      objective: 'calmar',
      epochs: 10,
      folds: null,
      scheme: null,
      cache: true,
      min_trades: 20,
      min_trades_per_year: 4,
    },
    evolve: {
      objective: 'calmar',
      epochs: 0,
      folds: null,
      scheme: null,
      cache: true,
      min_trades: 20,
      min_trades_per_year: 4,
      population: 40,
      generations: 25,
      segments: 4,
      holdout_fraction: 0.2,
    },
    walk_forward: {
      objective: 'calmar',
      epochs: 10,
      folds: 6,
      scheme: 'anchored',
      cache: null,
      min_trades: 20,
      min_trades_per_year: 4,
    },
  },
  indicators: [
    {
      type: 'sma',
      description: 'Simple moving average',
      parameters: [{ name: 'window', default: 20 }],
      outputs: [],
      inputs: ['source'],
      uses_source: true,
    },
    {
      type: 'rsi',
      description: 'Relative strength index',
      parameters: [{ name: 'window', default: 14 }],
      outputs: [],
      inputs: ['source'],
      uses_source: true,
    },
    {
      type: 'macd',
      description: 'Moving average convergence divergence',
      parameters: [
        { name: 'fast', default: 12 },
        { name: 'slow', default: 26 },
        { name: 'signal', default: 9 },
      ],
      outputs: ['macd', 'macds', 'macdh'],
      inputs: ['source'],
      uses_source: true,
    },
  ],
  exit_fields: [
    { name: 'signal', stop_priority: null, daily_only: false },
    { name: 'atr_stop_multiplier', stop_priority: 1, daily_only: false },
    { name: 'trailing_stop_pct', stop_priority: 2, daily_only: false },
    { name: 'stop_loss_pct', stop_priority: 3, daily_only: false },
    { name: 'take_profit_pct', stop_priority: null, daily_only: false },
    { name: 'min_holding_days', stop_priority: null, daily_only: true },
    { name: 'max_holding_days', stop_priority: null, daily_only: true },
    { name: 'min_holding_bars', stop_priority: null, daily_only: false },
    { name: 'max_holding_bars', stop_priority: null, daily_only: false },
  ],
  intervals: [
    { value: '15m', intraday: true, max_lookback_days: 55 },
    { value: '30m', intraday: true, max_lookback_days: 55 },
    { value: '1h', intraday: true, max_lookback_days: 700 },
    { value: '1d', intraday: false, max_lookback_days: null },
  ],
  limits: [
    'Ticker selection is hindsight - you chose the symbol knowing its history.',
    'Prices are retroactively adjusted; the same backtest run months apart uses different data.',
    'Numbers written inside a signal expression are not optimizable.',
    'One ticker per strategy. Long only.',
  ],
}

export function strategyRow(overrides: Partial<StrategyRow> = {}): StrategyRow {
  return {
    id: '11111111-1111-1111-1111-111111111111',
    name: 'rsi_pullback',
    ticker: 'NVDA',
    start_date: '2023-01-01',
    end_date: '2025-12-31',
    interval: '1d',
    head_version: 3,
    edited_at: '2026-08-17T09:00:00Z',
    created_at: '2026-08-01T09:00:00Z',
    lineage: { origin: 'authored' },
    origin_not_credible: false,
    verdict: 'unvalidated',
    verdict_run_id: null,
    optimize_runs: 2,
    backtest_runs: 1,
    walk_forward_runs: 0,
    evolve_runs: 0,
    versions: 3,
    last_run_id: '22222222-2222-2222-2222-222222222222',
    last_run_kind: 'optimize',
    last_run_status: 'succeeded',
    last_run_at: '2026-08-17T08:00:00Z',
    ...overrides,
  }
}

export function strategyDetail(overrides: Partial<StrategyDetail> = {}): StrategyDetail {
  return {
    id: '11111111-1111-1111-1111-111111111111',
    name: 'rsi_pullback',
    created_at: '2026-08-01T09:00:00Z',
    lineage: { origin: 'authored' },
    origin_not_credible: false,
    head: {
      version: 3,
      origin: 'edited',
      restored_from: null,
      note: null,
      created_at: '2026-08-17T09:00:00Z',
      config: {
        strategy: { name: 'rsi_pullback' },
        universe: { ticker: 'NVDA', start_date: '2023-01-01', end_date: '2025-12-31' },
      },
      yaml: 'strategy:\n  name: rsi_pullback\n',
    },
    counts: { versions: 3, backtest: 1, optimize: 2, walk_forward: 0 },
    verdict: { state: 'unvalidated', failures: [], checks: [] },
    verdict_run_id: null,
    promoted_warning: null,
    ...overrides,
  }
}

export function run(overrides: Partial<Run> = {}): Run {
  return {
    id: '22222222-2222-2222-2222-222222222222',
    number: 4,
    kind: 'backtest',
    status: 'succeeded',
    strategy: { id: '11111111-1111-1111-1111-111111111111', name: 'rsi_pullback' },
    version: 3,
    interval: '1d',
    stale: false,
    params: {},
    seed: 12345,
    queued_at: '2026-08-17T08:00:00Z',
    started_at: '2026-08-17T08:00:01Z',
    finished_at: '2026-08-17T08:00:09Z',
    elapsed_seconds: 8.2,
    progress: null,
    failure_category: null,
    headline: {
      trades: 48,
      entry_defined_pct: 88.4,
      suppressed: false,
      trade_floor: 20,
      return_pct: 31.4,
      benchmark_return_pct: 149.0,
      excess_pp: -117.6,
      max_drawdown_pct: -22.1,
    },
    promotable: false,
    cancel_requested: false,
    ...overrides,
  }
}
