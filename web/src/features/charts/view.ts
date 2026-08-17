/**
 * What one selected run offers the charts, and what it refuses them.
 *
 * Non-component exports live here rather than beside the tab so that `ChartsTab.tsx` exports
 * components only — the lint rule enforcing that exists because a module exporting both breaks
 * fast refresh, and a stale component tree during development is how a chart gets debugged
 * against data it is no longer being given.
 */

import { READY, type ChartState } from './state'
import { SERIES } from './series'
import {
  backtestResult,
  optimizationResult,
  validationResult,
  type Metrics,
  type Trade,
  type YearReturn,
} from '../../lib/result'

export const GROUPS = [
  { id: 'group-a', label: 'What it did', question: 'What did the strategy actually do?' },
  {
    id: 'group-b',
    label: 'Where the money came from',
    question: 'Is the profit a system, or is it one lucky trade?',
  },
  { id: 'group-c', label: 'Did it hold up', question: 'Did it hold up over time?' },
  {
    id: 'group-d',
    label: 'Contact with reality',
    question: 'Would it survive contact with reality?',
  },
] as const

export const ALL_SERIES = [
  SERIES.equity,
  SERIES.benchmarkEquity,
  SERIES.drawdown,
  SERIES.close,
  SERIES.monthly,
  SERIES.rolling12m,
  SERIES.filled,
] as const

/** §5.1's wording, kept in one place so the test asserts the sentence the user reads. */
export const POOLING_MESSAGE =
  'Select a fold to see its trades and price chart. Every fold re-optimizes from scratch, so ' +
  'combining trades from folds that used different parameters would chart results that are ' +
  'not one configuration.'

const POOLING: ChartState = { kind: 'needs-a-fold', what: POOLING_MESSAGE }

/**
 * What the trade-level charts may draw for this run.
 *
 * `unavailable` is the point of this shape. A walk-forward in Combined view *has* trades and
 * *has* metrics, and both belong to several different parameter vectors — so the charts that
 * would pool them get a state that explains, rather than data that misleads (spec §12.9).
 */
export type ChartView = {
  metrics: Metrics | null
  trades: Trade[]
  benchmarkYearly: YearReturn[] | null
  perFoldChartsAvailable: boolean
  unavailable: ChartState
}

export function viewOf(
  kind: string,
  result: Record<string, unknown>,
  fold: number | null,
): ChartView {
  const available = { perFoldChartsAvailable: true, unavailable: READY }

  if (kind === 'backtest') {
    const backtest = backtestResult(result)
    return {
      ...available,
      metrics: backtest.metrics,
      trades: backtest.trades,
      benchmarkYearly: backtest.benchmark?.metrics?.yearlyReturns ?? null,
    }
  }

  if (kind === 'optimize') {
    const optimization = optimizationResult(result)
    return {
      ...available,
      metrics: optimization.testMetrics,
      trades: optimization.trades,
      // Null for a run recorded before the engine computed one (D-15, no backfill). The chart
      // then draws the strategy alone and says why, rather than borrowing
      // `baseline_test_metrics` — that measures the user's own configuration, which is a
      // different question wearing the same shape.
      benchmarkYearly: optimization.benchmark?.metrics?.yearlyReturns ?? null,
    }
  }

  if (fold === null) {
    return {
      metrics: null,
      trades: [],
      benchmarkYearly: null,
      perFoldChartsAvailable: false,
      unavailable: POOLING,
    }
  }

  const selected = validationResult(result).folds[fold]
  return {
    ...available,
    metrics: selected?.metrics ?? null,
    trades: selected?.trades ?? [],
    // A fold has no benchmark of its own. The report's covers the whole out-of-sample span,
    // and drawing that against one fold's year would put two different windows on one axis.
    benchmarkYearly: null,
  }
}
