/**
 * Query keys, in one place so the SSE handler can invalidate precisely.
 *
 * The alternative — keys spelled inline at each call site — means an event handler
 * invalidating a prefix it guessed at, which either refetches the world on every heartbeat
 * or silently leaves a stale run on screen.
 */

export type RunListFilters = {
  strategyId?: string
  kind?: string
  status?: string
  limit?: number
  offset?: number
}

export type StrategyListFilters = {
  search?: string
  verdict?: string
}

export const queryKeys = {
  meta: ['meta'] as const,
  health: ['health'] as const,

  strategies: {
    all: ['strategies'] as const,
    list: (filters: StrategyListFilters = {}) => ['strategies', 'list', filters] as const,
    detail: (id: string) => ['strategies', 'detail', id] as const,
  },

  versions: {
    all: (strategyId: string) => ['versions', strategyId] as const,
    list: (strategyId: string) => ['versions', strategyId, 'list'] as const,
    detail: (strategyId: string, version: number) =>
      ['versions', strategyId, 'detail', version] as const,
    diff: (strategyId: string, from: number, to: number) =>
      ['versions', strategyId, 'diff', from, to] as const,
  },

  runs: {
    all: ['runs'] as const,
    list: (filters: RunListFilters = {}) => ['runs', 'list', filters] as const,
    /** The single polled query every progress indicator selects from. */
    active: ['runs', 'active'] as const,
    detail: (runId: string) => ['runs', 'detail', runId] as const,
    seriesCatalog: (runId: string) => ['runs', 'series', runId] as const,
    series: (runId: string, name: string, fold: number) =>
      ['runs', 'series', runId, name, fold] as const,
  },

  /**
   * Editor validation, keyed by the exact input so identical edits reuse the answer.
   *
   * `form` distinguishes the two request shapes. The same configuration sent as a mapping and
   * as text does not come back the same: only the text carries line numbers (spec §3.9), and
   * a shared key would let one answer be served for the other request.
   */
  validate: (form: 'config' | 'yaml', source: string) => ['validate', form, source] as const,
} as const

/**
 * How long a result stays fresh.
 *
 * Versions, diffs, finished runs and captured series are immutable by construction — the
 * database rejects an UPDATE on all four (spec section 14) — so refetching them is pure
 * waste, and `Infinity` here is a statement about the domain rather than a cache tweak.
 */
export const IMMUTABLE = { staleTime: Infinity, gcTime: 30 * 60 * 1000 } as const
