/**
 * Names for the generated schema types, plus hand-written types for the parts the
 * generator could only see as `unknown`.
 *
 * FastAPI documents a `dict[str, Any]` field as an open record, so `headline`, `progress`,
 * `defaults` and the indicator registry all arrive untyped. They are narrowed here, once,
 * against payloads captured from a running server. Anything read out of the verbatim engine
 * `result` blob is narrowed in `lib/result.ts` instead, with runtime guards, because that
 * blob is the engine's own serialisation and not part of the HTTP schema at all.
 */
import type { components } from './schema.gen'

type Schemas = components['schemas']

export type StrategySummary = Schemas['StrategySummary']
export type StrategyDetail = Schemas['StrategyDetail']
export type StrategyList = Schemas['StrategyList']
export type CreatedStrategy = Schemas['CreatedStrategy']
export type DeletedStrategy = Schemas['DeletedStrategy']
export type Lineage = Schemas['Lineage']
export type VerdictBlock = Schemas['VerdictBlockOut']
export type VerdictCheck = Schemas['VerdictCheck']
export type PromotedWarning = Schemas['PromotedWarningOut']

export type VersionOut = Schemas['VersionOut']
export type VersionSummary = Schemas['VersionSummary']
export type SavedVersion = Schemas['SavedVersion']
export type DiffResponse = Schemas['DiffResponse']
export type SectionDiff = Schemas['SectionDiffOut']
export type Change = Schemas['ChangeOut']

export type RunOut = Schemas['RunOut']
export type RunDetail = Schemas['RunDetail']
export type RunList = Schemas['RunList']
export type ParameterMove = Schemas['ParameterMoveOut']
export type PromotedStrategy = Schemas['PromotedStrategy']
export type SeriesCatalog = Schemas['SeriesCatalog']
export type SeriesPoints = Schemas['SeriesPoints']

export type ValidateResponse = Schemas['ValidateResponse']
export type ConfigDiffResponse = Schemas['ConfigDiffResponse']
export type SearchableParameter = Schemas['SearchableParameterOut']

/**
 * A configuration, as the wire carries it.
 *
 * Deliberately an open mapping rather than a modelled type. What makes a configuration a
 * strategy is the engine's judgement, not a schema's (spec section 3.1), and a hand-written
 * mirror of the config shape here would be a second definition that drifts the first time an
 * indicator gains a field. Screens read it through validation, which answers with the engine's
 * own verdict.
 */
export type ConfigMapping = Schemas['VersionOut']['config']
export type Issue = Schemas['Issue']
export type MetaResponse = Schemas['MetaResponse']
export type HealthResponse = Schemas['HealthResponse']

/** The four things a run can be. Kept as a union because every screen branches on it. */
export const RUN_KINDS = ['backtest', 'optimize', 'walk_forward', 'evolve'] as const
export type RunKind = (typeof RUN_KINDS)[number]

export const RUN_STATUSES = ['queued', 'running', 'succeeded', 'failed', 'cancelled'] as const
export type RunStatus = (typeof RUN_STATUSES)[number]

/** Statuses a worker may still act on. Drives the active-run poller and the cancel button. */
export const ACTIVE_STATUSES: readonly RunStatus[] = ['queued', 'running']

export const VERDICT_STATES = ['credible', 'not_credible', 'unvalidated', 'never_run'] as const
export type VerdictState = (typeof VERDICT_STATES)[number]

/**
 * Why a run failed. `causality_violation` is an engine bug rather than a user mistake and is
 * presented as one (spec section 13).
 */
export const FAILURE_CATEGORIES = [
  'config_invalid',
  'market_data',
  'engine_failure',
  'causality_violation',
] as const
export type FailureCategory = (typeof FAILURE_CATEGORIES)[number]

export type RunError = {
  category: FailureCategory | string
  exit_code: number
  message: string
}

/** `run.progress`, written by the worker heartbeat every ~10s while a run holds its lease. */
export type RunProgress = {
  stage: string
  percent: number
}

/** `run.strategy`, the denormalised parent every run row links back to. */
export type RunStrategyRef = {
  id: string
  name: string
}

/**
 * The server-computed list columns for a run.
 *
 * Every field is optional on purpose. Below the trade floor the API omits the figures it
 * will not stand behind rather than sending them with a flag (spec section 15.2), so an
 * absent key here is the signal to render the suppressed state — see `lib/suppression.ts`.
 */
export type BacktestHeadline = {
  trades: number
  entry_defined_pct: number
  suppressed: boolean
  trade_floor: number
  return_pct?: number | null
  benchmark_return_pct?: number | null
  excess_pp?: number | null
  max_drawdown_pct?: number | null
}

export type OptimizeHeadline = {
  trials: number
  trades: number
  suppressed: boolean
  trade_floor: number
  oos_return_pct?: number | null
  improvement_pct?: number | null
  overfitting_gap_pct?: number | null
  test_cagr_pct?: number | null
}

export type WalkForwardHeadline = {
  folds: number
  scheme: string
  combined_oos_pct?: number | null
  benchmark_pct?: number | null
  profitable_folds?: string | null
  oos_trades: number
  is_credible: boolean
  failed_checks: number
}

/**
 * An evolution run's columns.
 *
 * `composition` is not a measurement — it says what the search built — so it survives
 * suppression, while every figure below the trade floor is omitted exactly as elsewhere.
 */
export type EvolveHeadline = {
  composition: string | null
  trials: number
  trades: number
  is_credible: boolean
  failed_checks: number
  suppressed: boolean
  trade_floor: number
  holdout_return_pct?: number | null
  benchmark_return_pct?: number | null
  max_drawdown_pct?: number | null
  profitable_segments?: string | null
}

export type Headline = BacktestHeadline | OptimizeHeadline | WalkForwardHeadline | EvolveHeadline

/** Launch parameters, per kind, as `/meta` pre-fills them. */
export type RunDefaults = {
  objective: string
  epochs: number
  min_trades?: number | null
  min_trades_per_year?: number | null
  folds: number | null
  scheme: string | null
  cache: boolean | null
  /** Evolution only; null for every other kind. */
  population?: number | null
  generations?: number | null
  segments?: number | null
  holdout_fraction?: number | null
}

export type IndicatorParameter = {
  name: string
  default: number
}

/**
 * One indicator type from the engine's registry.
 *
 * `outputs` is what the signal-expression namespace needs: an empty list means the indicator
 * contributes its own name, and a non-empty one means it fans out to `<name>_<output>` for
 * each entry (a MACD named `m` gives `m_macd`, `m_macds`, `m_macdh`).
 */
export type IndicatorDescription = {
  type: string
  description: string
  parameters: IndicatorParameter[]
  outputs: string[]
  inputs: string[]
  uses_source: boolean
}

/** One exit field and where it sits in the stop-priority chain (null = not a stop). */
export type ExitField = {
  name: string
  stop_priority: number | null
}

/** `/meta` with its open records narrowed. */
export type EngineMeta = Omit<MetaResponse, 'defaults' | 'indicators' | 'exit_fields'> & {
  defaults: Record<RunKind, RunDefaults>
  indicators: IndicatorDescription[]
  exit_fields: ExitField[]
}

/** `RunOut` with the fields the generator left open narrowed to what the server sends. */
export type Run = Omit<
  RunOut,
  'kind' | 'status' | 'strategy' | 'progress' | 'headline' | 'failure_category' | 'params'
> & {
  kind: RunKind
  status: RunStatus
  strategy: RunStrategyRef
  progress: RunProgress | null
  headline: Headline | null
  failure_category: FailureCategory | null
  params: Record<string, unknown>
}

/** `RunDetail` with its nested run narrowed, and the two loose blobs named. */
export type RunDetailNarrowed = Omit<RunDetail, 'run' | 'error' | 'result'> & {
  run: Run
  error: RunError | null
  /** The engine's own serialisation, passed through untouched. Narrowed with guards. */
  result: Record<string, unknown> | null
}

export type StrategyRow = Omit<StrategySummary, 'verdict' | 'last_run_kind' | 'last_run_status'> & {
  verdict: VerdictState
  last_run_kind: RunKind | null
  last_run_status: RunStatus | null
}
