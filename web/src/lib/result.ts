/**
 * Reading the engine's own serialisation.
 *
 * `RunDetail.result` is whatever `cracktrade.serialize.to_dict` produced, passed through the
 * API untouched (spec section 15.1). It is deliberately *not* part of the OpenAPI schema, so
 * the generated types see it as an open record and the compiler can prove nothing about it.
 * This module is where that ends: every field a screen reads is pulled out through a runtime
 * guard, once, and every screen above it works in typed values.
 *
 * Three rules the guards encode, all of them consequences of how the engine serialises:
 *
 * - **A number may legitimately be `null`.** JSON has no infinity, and an infinite profit
 *   factor is a real result — no losing trades — so `serialize` writes non-finite floats as
 *   null rather than dropping them (spec section 11). `null` is therefore data, not absence,
 *   and `Reported` in `suppression.ts` keeps the two apart.
 * - **A missing key is the server withholding a figure**, not a bug to paper over with a
 *   zero (spec section 15.2).
 * - **Nothing here recomputes a verdict or a derived statistic.** `is_credible`, `failures`,
 *   `improvement_pct`, `at_bound` and the rest are contractual properties the engine already
 *   computed; a second implementation here would eventually disagree with the first in public
 *   (spec section 15.1).
 */

export type Json = Record<string, unknown>

export function asRecord(value: unknown): Json | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Json)
    : null
}

export function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

/** A finite number, or null for both "absent" and "the engine computed a non-finite value". */
export function asNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

export function asString(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

/**
 * A boolean, and only a boolean.
 *
 * Never coerces. `Trade.is_open` once arrived as the string `"False"` — a numpy scalar that
 * reached the serializer's `str()` fallback — and `Boolean("False")` is `true`, so a coercing
 * reader would have marked every closed trade open and dropped the entire trade list out of
 * every aggregate while looking correct (spec section 11, amended 2026-08-17). The engine now
 * coerces at its own boundary and a repo test holds it there; this refuses to paper over a
 * regression rather than silently absorbing one.
 */
export function asBoolean(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null
}

export function field(source: Json | null, key: string): unknown {
  return source ? source[key] : undefined
}

export function record(source: Json | null, key: string): Json | null {
  return asRecord(field(source, key))
}

export function number(source: Json | null, key: string): number | null {
  return asNumber(field(source, key))
}

export function text(source: Json | null, key: string): string | null {
  return asString(field(source, key))
}

export function flag(source: Json | null, key: string): boolean | null {
  return asBoolean(field(source, key))
}

export function records(source: Json | null, key: string): Json[] {
  return asArray(field(source, key))
    .map(asRecord)
    .filter((item): item is Json => item !== null)
}

// --------------------------------------------------------------------------- domain readers

export type YearReturn = { year: number; returnPct: number | null }

export type Metrics = {
  totalTrades: number | null
  winRatePct: number | null
  profitFactor: number | null
  totalPnl: number | null
  finalEquity: number | null
  totalReturnPct: number | null
  cagrPct: number | null
  maxDrawdownPct: number | null
  sharpeRatio: number | null
  sortinoRatio: number | null
  calmarRatio: number | null
  exposurePct: number | null
  avgHoldingDays: number | null
  bestTradePnl: number | null
  worstTradePnl: number | null
  bars: number | null
  yearlyReturns: YearReturn[]
  worstRolling12mPct: number | null
  hasEnoughTradesToJudge: boolean | null
}

export function metricsOf(source: Json | null): Metrics | null {
  if (!source) return null
  return {
    totalTrades: number(source, 'total_trades'),
    winRatePct: number(source, 'win_rate_pct'),
    profitFactor: number(source, 'profit_factor'),
    totalPnl: number(source, 'total_pnl'),
    finalEquity: number(source, 'final_equity'),
    totalReturnPct: number(source, 'total_return_pct'),
    cagrPct: number(source, 'cagr_pct'),
    maxDrawdownPct: number(source, 'max_drawdown_pct'),
    sharpeRatio: number(source, 'sharpe_ratio'),
    sortinoRatio: number(source, 'sortino_ratio'),
    calmarRatio: number(source, 'calmar_ratio'),
    exposurePct: number(source, 'exposure_pct'),
    avgHoldingDays: number(source, 'avg_holding_days'),
    bestTradePnl: number(source, 'best_trade_pnl'),
    worstTradePnl: number(source, 'worst_trade_pnl'),
    bars: number(source, 'bars'),
    yearlyReturns: records(source, 'yearly_returns').map((entry) => ({
      year: number(entry, 'year') ?? 0,
      returnPct: number(entry, 'return_pct'),
    })),
    worstRolling12mPct: number(source, 'worst_rolling_12m_pct'),
    hasEnoughTradesToJudge: flag(source, 'has_enough_trades_to_judge'),
  }
}

/**
 * Whether a set of metrics may have its performance figures shown.
 *
 * The one place the trade floor is applied to a *result blob*, and it has to exist because the
 * two guarantees in the spec pull in different directions here. Section 15.2 says a suppressed
 * figure is never sent; section 15.1 says a result payload is the engine's serialisation passed
 * through unaltered. Both cannot hold for the same bytes, and 15.1 wins for `result` — stripping
 * keys out of it would mean the stored artifact and the served one differ, which costs more than
 * it buys. So 15.2 governs what the API *composes* (headlines, list rows, which genuinely omit
 * the keys), and below the floor the raw blob still carries every figure.
 *
 * That makes the floor this side's job wherever a screen reads the blob, and this is the single
 * chokepoint for it — the counterpart to `headlineFigure` in `suppression.ts`. Screens branch on
 * the result of this and cannot reach a suppressed number by accident, because the metrics are
 * not handed to them at all.
 *
 * `total_trades` is reported either way. It is the evidence for the suppression rather than a
 * claim about performance, and "too few trades" with no number attached is not an explanation.
 */
export type Figures =
  { shown: true; metrics: Metrics } | { shown: false; trades: number | null; metrics: null }

export function figuresOf(metrics: Metrics | null): Figures {
  if (!metrics) return { shown: false, trades: null, metrics: null }
  if (metrics.hasEnoughTradesToJudge === false) {
    return { shown: false, trades: metrics.totalTrades, metrics: null }
  }
  return { shown: true, metrics }
}

export type Trade = {
  entryDate: string | null
  exitDate: string | null
  entryPrice: number | null
  exitPrice: number | null
  size: number | null
  pnl: number | null
  returnPct: number | null
  fees: number | null
  holdingDays: number | null
  isOpen: boolean
  isWinner: boolean
}

/**
 * The trade list.
 *
 * `isOpen` defaults to *false* when the flag is unreadable, and that direction is deliberate:
 * a trade wrongly treated as open is excluded from every aggregate, which silently shrinks the
 * sample the user is judging. A trade wrongly treated as closed is at least visible and
 * countable. Neither is correct, so a repo test on the engine side keeps the flag honest;
 * this is only the direction to fall in if it ever is not.
 */
export function tradesOf(source: Json | null, key = 'trades'): Trade[] {
  return records(source, key).map((entry) => ({
    entryDate: text(entry, 'entry_date'),
    exitDate: text(entry, 'exit_date'),
    entryPrice: number(entry, 'entry_price'),
    exitPrice: number(entry, 'exit_price'),
    size: number(entry, 'size'),
    pnl: number(entry, 'pnl'),
    returnPct: number(entry, 'return_pct'),
    fees: number(entry, 'fees'),
    holdingDays: number(entry, 'holding_days'),
    isOpen: flag(entry, 'is_open') ?? false,
    isWinner: flag(entry, 'is_winner') ?? false,
  }))
}

/** Closed trades only. Every aggregate in the app counts these and no others (spec §8). */
export function closedTrades(trades: Trade[]): Trade[] {
  return trades.filter((trade) => !trade.isOpen)
}

export type DataVintage = {
  ticker: string | null
  firstBar: string | null
  lastBar: string | null
  bars: number | null
  filledBars: number | null
  filledPct: number | null
  fetchedOn: string | null
  frameDigest: string | null
}

export function vintageOf(source: Json | null): DataVintage | null {
  const vintage = record(source, 'vintage')
  if (!vintage) return null
  return {
    ticker: text(vintage, 'ticker'),
    firstBar: text(vintage, 'first_bar'),
    lastBar: text(vintage, 'last_bar'),
    bars: number(vintage, 'bars'),
    filledBars: number(vintage, 'filled_bars'),
    filledPct: number(vintage, 'filled_pct'),
    fetchedOn: text(vintage, 'fetched_on'),
    frameDigest: text(vintage, 'frame_digest'),
  }
}

export type Benchmark = {
  metrics: Metrics | null
  excessReturnPct: number | null
  excessCagrPct: number | null
  informationRatio: number | null
  beatsBuyAndHold: boolean | null
}

export function benchmarkOf(source: Json | null): Benchmark | null {
  const comparison = record(source, 'benchmark')
  if (!comparison) return null
  return {
    metrics: metricsOf(record(comparison, 'benchmark')),
    excessReturnPct: number(comparison, 'excess_return_pct'),
    excessCagrPct: number(comparison, 'excess_cagr_pct'),
    informationRatio: number(comparison, 'information_ratio'),
    beatsBuyAndHold: flag(comparison, 'beats_buy_and_hold'),
  }
}

export type ParameterChange = {
  path: string
  oldValue: number | null
  newValue: number | null
  low: number | null
  high: number | null
  moved: boolean
  atBound: boolean
}

/**
 * The parameter diff.
 *
 * `key` exists because the engine publishes the same shape twice: `changes` is every parameter
 * the search touched, and the `parameters_at_bound` property is the subset that finished on an
 * edge — a list of `ParameterChange` objects, not of names. Read as strings it came back empty
 * every time, which silently disabled the "widen the range and run again" warning while the
 * per-row badge beside it kept working, so nothing looked broken.
 */
export function changesOf(source: Json | null, key = 'changes'): ParameterChange[] {
  return records(source, key).map((entry) => ({
    path: text(entry, 'path') ?? '',
    oldValue: number(entry, 'old_value'),
    newValue: number(entry, 'new_value'),
    low: number(entry, 'low'),
    high: number(entry, 'high'),
    moved: flag(entry, 'moved') ?? false,
    atBound: flag(entry, 'at_bound') ?? false,
  }))
}

export type Fold = {
  index: number
  trainBars: number | null
  testBars: number | null
  firstTestBar: string | null
  lastTestBar: string | null
  metrics: Metrics | null
  trainMetrics: Metrics | null
  parameters: Record<string, number | null>
  wasProfitable: boolean | null
  /**
   * This fold's own trades, and only this fold's.
   *
   * Empty for a run recorded before the engine kept them (spec §12.9, decided 2026-08-17);
   * nothing backfills one, so a screen reading this must treat empty as "not recorded" rather
   * than as "took no trades" — `foldTrades` in `features/charts/series.ts` is where that
   * distinction is drawn.
   */
  trades: Trade[]
}

export function foldsOf(source: Json | null): Fold[] {
  return records(source, 'folds').map((entry, position) => {
    const parameters = record(entry, 'parameters') ?? {}
    return {
      index: number(entry, 'index') ?? position,
      trainBars: number(entry, 'train_bars'),
      testBars: number(entry, 'test_bars'),
      firstTestBar: text(entry, 'first_test_bar'),
      lastTestBar: text(entry, 'last_test_bar'),
      metrics: metricsOf(record(entry, 'metrics')),
      trainMetrics: metricsOf(record(entry, 'train_metrics')),
      parameters: Object.fromEntries(
        Object.entries(parameters).map(([path, value]) => [path, asNumber(value)]),
      ),
      wasProfitable: flag(entry, 'was_profitable'),
      trades: tradesOf(entry),
    }
  })
}

export type Interval = {
  point: number | null
  low: number | null
  high: number | null
  confidence: number | null
  excludesZero: boolean | null
}

export function intervalOf(source: Json | null, key: string): Interval | null {
  const interval = record(source, key)
  if (!interval) return null
  return {
    point: number(interval, 'point'),
    low: number(interval, 'low'),
    high: number(interval, 'high'),
    confidence: number(interval, 'confidence'),
    excludesZero: flag(interval, 'excludes_zero'),
  }
}

export type StabilityPoint = {
  path: string
  multiplier: number | null
  value: number | null
  score: number | null
  degradation: number | null
}

export type Stability = {
  baselineScore: number | null
  points: StabilityPoint[]
  worstDegradation: number | null
  worstSmallDegradation: number | null
  isStable: boolean | null
  fragileParameters: string[]
}

export function stabilityOf(source: Json | null): Stability | null {
  const stability = record(source, 'stability')
  if (!stability) return null
  return {
    baselineScore: number(stability, 'baseline_score'),
    points: records(stability, 'points').map((point) => ({
      path: text(point, 'path') ?? '',
      multiplier: number(point, 'multiplier'),
      value: number(point, 'value'),
      score: number(point, 'score'),
      degradation: number(point, 'degradation'),
    })),
    worstDegradation: number(stability, 'worst_degradation'),
    worstSmallDegradation: number(stability, 'worst_small_degradation'),
    isStable: flag(stability, 'is_stable'),
    fragileParameters: asArray(field(stability, 'fragile_parameters'))
      .map(asString)
      .filter((name): name is string => name !== null),
  }
}

export type CostScenario = {
  multiple: number | null
  slippagePct: number | null
  commissionPct: number | null
  metrics: Metrics | null
}

export type CostSensitivity = {
  scenarios: CostScenario[]
  survivesDoubleCosts: boolean | null
  breakEvenMultiple: number | null
}

export function costsOf(source: Json | null): CostSensitivity | null {
  const costs = record(source, 'costs')
  if (!costs) return null
  return {
    scenarios: records(costs, 'scenarios').map((scenario) => ({
      multiple: number(scenario, 'multiple'),
      slippagePct: number(scenario, 'slippage_pct'),
      commissionPct: number(scenario, 'commission_pct'),
      metrics: metricsOf(record(scenario, 'metrics')),
    })),
    survivesDoubleCosts: flag(costs, 'survives_double_costs'),
    breakEvenMultiple: number(costs, 'break_even_multiple'),
  }
}

export type DeflatedSharpe = {
  observed: number | null
  threshold: number | null
  probability: number | null
  trials: number | null
  observations: number | null
  varianceEstimated: boolean | null
  isSignificant: boolean | null
  beatsTheLuckyThreshold: boolean | null
}

export function deflatedOf(source: Json | null): DeflatedSharpe | null {
  const deflated = record(source, 'deflated')
  if (!deflated) return null
  return {
    observed: number(deflated, 'observed'),
    threshold: number(deflated, 'threshold'),
    probability: number(deflated, 'probability'),
    trials: number(deflated, 'trials'),
    observations: number(deflated, 'observations'),
    varianceEstimated: flag(deflated, 'variance_estimated'),
    isSignificant: flag(deflated, 'is_significant'),
    beatsTheLuckyThreshold: flag(deflated, 'beats_the_lucky_threshold'),
  }
}

export type Overfitting = {
  probability: number | null
  combinations: number | null
  medianLogit: number | null
  isAcceptable: boolean | null
}

export function overfittingOf(source: Json | null): Overfitting | null {
  const overfitting = record(source, 'overfitting')
  if (!overfitting) return null
  return {
    probability: number(overfitting, 'probability'),
    combinations: number(overfitting, 'combinations'),
    medianLogit: number(overfitting, 'median_logit'),
    isAcceptable: flag(overfitting, 'is_acceptable'),
  }
}

// --------------------------------------------------------------------------- whole results

export type BacktestResult = {
  strategyName: string | null
  ticker: string | null
  vintage: DataVintage | null
  metrics: Metrics | null
  trades: Trade[]
  entryDefinedPct: number | null
  exitDefinedPct: number | null
  activeStop: string | null
  shadowedStops: string[]
  benchmark: Benchmark | null
  riskFreeRate: number | null
  warmupBars: number | null
}

export function backtestResult(source: Json | null): BacktestResult {
  return {
    strategyName: text(source, 'strategy_name'),
    ticker: text(source, 'ticker'),
    vintage: vintageOf(source),
    metrics: metricsOf(record(source, 'metrics')),
    trades: tradesOf(source),
    entryDefinedPct: number(source, 'entry_defined_pct'),
    exitDefinedPct: number(source, 'exit_defined_pct'),
    activeStop: text(source, 'active_stop'),
    shadowedStops: asArray(field(source, 'shadowed_stops'))
      .map(asString)
      .filter((name): name is string => name !== null),
    benchmark: benchmarkOf(source),
    riskFreeRate: number(source, 'risk_free_rate'),
    warmupBars: number(source, 'warmup_bars'),
  }
}

export type OptimizationResult = {
  objective: string | null
  optimizedYaml: string | null
  testMetrics: Metrics | null
  trainMetrics: Metrics | null
  baselineTestMetrics: Metrics | null
  changes: ParameterChange[]
  trades: Trade[]
  trainBars: number | null
  testBars: number | null
  evaluations: number | null
  failures: number | null
  infeasible: number | null
  /**
   * The trade floor this run enforced, or null for a run recorded before the floor was
   * configurable. Kept beside `infeasible` because that count means nothing on its own — the
   * floor is resolved per run from the training window's length, so it is not a constant the
   * screen could state for itself.
   */
  minTradesRequired: number | null
  countsExact: boolean | null
  trials: number | null
  budget: number | null
  seed: number | null
  convergenceMessage: string | null
  mostCommonFailure: string | null
  improvementPct: number | null
  overfittingGapPct: number | null
  parametersAtBound: string[]
  /**
   * Buying and holding across the same test window.
   *
   * Null for a run recorded before the engine computed one (spec §9.5, amended 2026-08-17).
   * Null is the honest rendering: `improvementPct` beside it answers whether the search did
   * anything, and a screen that filled the gap by subtracting the baseline would be labelling
   * that answer as the other one.
   */
  benchmark: Benchmark | null
}

export function optimizationResult(source: Json | null): OptimizationResult {
  return {
    objective: text(source, 'objective'),
    optimizedYaml: text(source, 'optimized_yaml'),
    testMetrics: metricsOf(record(source, 'test_metrics')),
    trainMetrics: metricsOf(record(source, 'train_metrics')),
    baselineTestMetrics: metricsOf(record(source, 'baseline_test_metrics')),
    changes: changesOf(source),
    trades: tradesOf(source),
    trainBars: number(source, 'train_bars'),
    testBars: number(source, 'test_bars'),
    evaluations: number(source, 'evaluations'),
    failures: number(source, 'failures'),
    infeasible: number(source, 'infeasible'),
    minTradesRequired: number(source, 'min_trades_required'),
    countsExact: flag(source, 'counts_exact'),
    trials: number(source, 'trials'),
    budget: number(source, 'budget'),
    seed: number(source, 'seed'),
    convergenceMessage: text(source, 'convergence_message'),
    mostCommonFailure: text(source, 'most_common_failure'),
    improvementPct: number(source, 'improvement_pct'),
    overfittingGapPct: number(source, 'overfitting_gap_pct'),
    parametersAtBound: changesOf(source, 'parameters_at_bound').map((change) => change.path),
    benchmark: benchmarkOf(source),
  }
}

export type ValidationResult = {
  objective: string | null
  scheme: string | null
  folds: Fold[]
  benchmark: Metrics | null
  optimizedYaml: string | null
  deflated: DeflatedSharpe | null
  overfitting: Overfitting | null
  stability: Stability | null
  costs: CostSensitivity | null
  meanReturnInterval: Interval | null
  totalReturnInterval: Interval | null
  trials: number | null
  seed: number | null
  combinedReturnPct: number | null
  beatsBuyAndHold: boolean | null
  returns: (number | null)[]
  profitableFolds: number | null
  foldWinRate: number | null
  medianReturnPct: number | null
  returnIqrPct: number | null
  totalTrades: number | null
  isCredible: boolean | null
  failures: string[]
}

/**
 * A walk-forward report.
 *
 * `benchmark` here is a bare `Metrics`, not a `BenchmarkComparison` as on a backtest — the
 * combined out-of-sample window is stitched from folds and has no single excess figure. Read
 * it from the engine's `benchmark` check rather than subtracting one from the other here.
 */
export function validationResult(source: Json | null): ValidationResult {
  return {
    objective: text(source, 'objective'),
    scheme: text(source, 'scheme'),
    folds: foldsOf(source),
    benchmark: metricsOf(record(source, 'benchmark')),
    optimizedYaml: text(source, 'optimized_yaml'),
    deflated: deflatedOf(source),
    overfitting: overfittingOf(source),
    stability: stabilityOf(source),
    costs: costsOf(source),
    meanReturnInterval: intervalOf(source, 'mean_return_interval'),
    totalReturnInterval: intervalOf(source, 'total_return_interval'),
    trials: number(source, 'trials'),
    seed: number(source, 'seed'),
    combinedReturnPct: number(source, 'combined_return_pct'),
    beatsBuyAndHold: flag(source, 'beats_buy_and_hold'),
    returns: asArray(field(source, 'returns')).map(asNumber),
    profitableFolds: number(source, 'profitable_folds'),
    foldWinRate: number(source, 'fold_win_rate'),
    medianReturnPct: number(source, 'median_return_pct'),
    returnIqrPct: number(source, 'return_iqr_pct'),
    totalTrades: number(source, 'total_trades'),
    isCredible: flag(source, 'is_credible'),
    failures: asArray(field(source, 'failures'))
      .map(asString)
      .filter((detail): detail is string => detail !== null),
  }
}
