/**
 * Turning versions and runs into the comparison table's rows.
 *
 * Two decisions live here, and both are about what *not* to put in a cell.
 */

import {
  metricsOf,
  benchmarkOf,
  optimizationResult,
  validationResult,
  vintageOf,
  backtestResult,
  type Json,
} from '../../lib/result'
import type { Run, RunDetailNarrowed } from '../../api/types'
import type { ComparableKind, Figures, VersionRow, VersionRun } from './delta'

/**
 * Which run of a kind speaks for a version.
 *
 * **The latest, not the best**, which is a deliberate departure from the brief's "the best run
 * against this version". Optimization and walk-forward runs are seeded searches: running one
 * three times against an unchanged config gives three different answers, and taking the maximum
 * over them is picking the best of N trials — the precise thing the deflated Sharpe exists to
 * punish, and one the trial count would not know about. A table built that way would reward a
 * version for having been run more often, and it would do so on the same screen that warns the
 * user against exactly that.
 *
 * The latest run is chosen by nothing except recency, reads the freshest price data, and is the
 * answer the user last saw. Where a version has more than one, the count is shown beside it.
 */
export function speakingRun(runs: Run[], version: number, kind: ComparableKind): Run | null {
  const candidates = runs.filter(
    (run) => run.version === version && run.kind === kind && run.status === 'succeeded',
  )
  if (candidates.length === 0) return null
  return (
    candidates.reduce((latest, run) =>
      Date.parse(run.queued_at) >= Date.parse(latest.queued_at) ? run : latest,
    ) ?? null
  )
}

export function runCount(runs: Run[], version: number, kind: ComparableKind): number {
  return runs.filter(
    (run) => run.version === version && run.kind === kind && run.status === 'succeeded',
  ).length
}

/**
 * What a run reports, per kind.
 *
 * A walk-forward genuinely has **no combined drawdown and no combined Sharpe**, and this
 * returns null for both rather than assembling one. `ValidationReport` carries per-fold metrics
 * and a benchmark, and nothing else: the engine refuses a combined equity curve on the grounds
 * that each fold re-optimizes, so splicing them draws a strategy that was never traded. There
 * is no honest way to fill those cells from what exists, and averaging six folds' Sharpes into
 * one would produce a number that looks exactly like the backtest column beside it.
 *
 * `deflated.observed` is not a substitute: it is a *per-period* Sharpe, while `Metrics.sharpe_ratio`
 * is annualised. They differ by a factor of roughly sixteen, and putting them in one column
 * under one heading would be the worst kind of wrong — plausible, and off by an order of
 * magnitude.
 *
 * Excess over buy-and-hold is null for a walk-forward for the same family of reason: the engine
 * publishes a `BenchmarkComparison` with a real excess figure for backtests and optimizations,
 * and for a walk-forward only the benchmark's own metrics. Subtracting one from the other here
 * would be this side inventing a statistic the engine declined to publish (spec §15.1).
 */
export function figuresOfRun(kind: ComparableKind, result: Json | null): Figures | null {
  if (kind === 'walk_forward') {
    const report = validationResult(result)
    if (report.totalTrades !== null && report.folds.length > 0 && !hasEnoughTrades(report.folds)) {
      return null
    }
    return {
      returnPct: report.combinedReturnPct,
      excessPp: null,
      maxDrawdownPct: null,
      sharpe: null,
      foldWinRate: report.foldWinRate,
    }
  }

  if (kind === 'optimize') {
    const report = optimizationResult(result)
    const metrics = report.testMetrics
    if (!metrics || metrics.hasEnoughTradesToJudge === false) return null
    return {
      returnPct: metrics.totalReturnPct,
      excessPp: report.benchmark?.excessReturnPct ?? null,
      maxDrawdownPct: metrics.maxDrawdownPct,
      sharpe: metrics.sharpeRatio,
      foldWinRate: null,
    }
  }

  const report = backtestResult(result)
  const metrics = report.metrics
  if (!metrics || metrics.hasEnoughTradesToJudge === false) return null
  return {
    returnPct: metrics.totalReturnPct,
    excessPp: report.benchmark?.excessReturnPct ?? null,
    maxDrawdownPct: metrics.maxDrawdownPct,
    sharpe: metrics.sharpeRatio,
    foldWinRate: null,
  }
}

/**
 * Whether a walk-forward cleared the floor.
 *
 * A walk-forward's trade count is the sum across folds, and the engine flags the floor per
 * fold rather than for the run as a whole. A run whose every fold is below the floor has no
 * fold whose figures may be shown, so its combined return is built entirely from numbers the
 * rest of the application refuses to print.
 */
function hasEnoughTrades(folds: { metrics: { hasEnoughTradesToJudge: boolean | null } | null }[]) {
  return folds.some((fold) => fold.metrics?.hasEnoughTradesToJudge !== false)
}

export function tradesOfRun(kind: ComparableKind, result: Json | null): number | null {
  if (kind === 'walk_forward') return validationResult(result).totalTrades
  if (kind === 'optimize') return optimizationResult(result).testMetrics?.totalTrades ?? null
  return metricsOf(recordOf(result, 'metrics'))?.totalTrades ?? null
}

function recordOf(source: Json | null, key: string): Json | null {
  const value = source?.[key]
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Json)
    : null
}

/** The verdict a walk-forward issued, or the honest absence of one. */
export function verdictOfRun(kind: ComparableKind, result: Json | null): boolean | null {
  return kind === 'walk_forward' ? validationResult(result).isCredible : null
}

/** The run's launch parameters, which are what make two runs comparable or not. */
export function comparabilityOf(
  run: Run,
  result: Json | null,
): Pick<VersionRun, 'objective' | 'folds' | 'scheme' | 'frameDigest'> {
  const params = run.params
  return {
    objective: asText(params.objective) ?? objectiveFromResult(run.kind, result),
    folds: run.kind === 'walk_forward' ? (asCount(params.folds) ?? foldCount(result)) : null,
    scheme: run.kind === 'walk_forward' ? asText(params.scheme) : null,
    frameDigest: vintageOf(result)?.frameDigest ?? null,
  }
}

/**
 * The objective, preferring the result over the request.
 *
 * A run's stored `params` is what the client asked for, and a launch that omitted the objective
 * took the server's default — so the request can be silent about a choice that was made. The
 * result records what the search actually maximised.
 */
function objectiveFromResult(kind: Run['kind'], result: Json | null): string | null {
  if (kind === 'walk_forward') return validationResult(result).objective
  if (kind === 'optimize') return optimizationResult(result).objective
  return null
}

function foldCount(result: Json | null): number | null {
  const folds = validationResult(result).folds.length
  return folds > 0 ? folds : null
}

function asText(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

function asCount(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

export type BuildInput = {
  versions: { version: number }[]
  runs: Run[]
  kind: ComparableKind
  /** Run details, keyed by run id. Rows whose detail has not arrived are reported as pending. */
  details: Map<string, RunDetailNarrowed>
}

export type BuiltRow = VersionRow & {
  /** How many succeeded runs of this kind exist against the version. */
  runs: number
  /** True while the selected run's detail is still loading. */
  pending: boolean
  verdict: boolean | null
}

/** The table's rows, oldest first, so the strategy reads as a progression. */
export function buildRows(input: BuildInput): BuiltRow[] {
  const ordered = [...input.versions].sort((a, b) => a.version - b.version)

  return ordered.map(({ version }) => {
    const chosen = speakingRun(input.runs, version, input.kind)
    const count = runCount(input.runs, version, input.kind)
    if (!chosen) {
      return { version, run: null, runs: 0, pending: false, verdict: null }
    }

    const detail = input.details.get(chosen.id)
    if (!detail) {
      return { version, run: null, runs: count, pending: true, verdict: null }
    }

    const result = (detail.result ?? null) as Json | null
    return {
      version,
      runs: count,
      pending: false,
      verdict: verdictOfRun(input.kind, result),
      run: {
        version,
        runId: chosen.id,
        kind: input.kind,
        trades: tradesOfRun(input.kind, result),
        figures: figuresOfRun(input.kind, result),
        ...comparabilityOf(chosen, result),
      },
    }
  })
}

/** Which run kinds this strategy actually has succeeded runs for. */
export function availableKinds(runs: Run[]): ComparableKind[] {
  const kinds = new Set(runs.filter((run) => run.status === 'succeeded').map((run) => run.kind))
  return (['walk_forward', 'optimize', 'backtest'] as const).filter((kind) => kinds.has(kind))
}

/**
 * The kind the table opens on.
 *
 * Walk-forward first when one exists — it is the only kind that issues a verdict, and it is the
 * only one that pushes back on the hindsight the rest of this screen is exposed to. Backtest
 * next, then optimization.
 */
export function defaultKind(runs: Run[]): ComparableKind | null {
  const available = availableKinds(runs)
  return (
    available.find((kind) => kind === 'walk_forward') ??
    available.find((kind) => kind === 'backtest') ??
    available[0] ??
    null
  )
}

export function benchmarkOfRun(kind: ComparableKind, result: Json | null): number | null {
  if (kind === 'walk_forward') return validationResult(result).benchmark?.totalReturnPct ?? null
  const comparison = benchmarkOf(result)
  return comparison?.metrics?.totalReturnPct ?? null
}
