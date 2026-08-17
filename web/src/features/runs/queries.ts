import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '../../api/client'
import { IMMUTABLE, queryKeys, type RunListFilters } from '../../api/keys'
import type { PromotedStrategy, Run, RunDetailNarrowed, RunKind } from '../../api/types'

export type RunListResult = { runs: Run[]; total: number }

export function useRuns(filters: RunListFilters) {
  return useQuery({
    queryKey: queryKeys.runs.list(filters),
    queryFn: async (): Promise<RunListResult> => {
      const query: Record<string, string | number> = {}
      if (filters.strategyId) query.strategy_id = filters.strategyId
      if (filters.kind) query.kind = filters.kind
      if (filters.status) query.status = filters.status
      if (filters.limit !== undefined) query.limit = filters.limit
      if (filters.offset !== undefined) query.offset = filters.offset

      const result = await api.GET('/api/v1/runs', { params: { query } })
      return unwrap(result) as unknown as RunListResult
    },
  })
}

export function useRun(runId: string) {
  return useQuery({
    queryKey: queryKeys.runs.detail(runId),
    queryFn: async (): Promise<RunDetailNarrowed> => {
      const result = await api.GET('/api/v1/runs/{run_id}', {
        params: { path: { run_id: runId } },
      })
      return unwrap(result) as unknown as RunDetailNarrowed
    },
    // A finished run is immutable — the database rejects an update on it — so once one has
    // landed there is nothing to refetch. While it is still in flight the SSE stream and the
    // active-run poller drive invalidation.
    staleTime: (query) => {
      const status = query.state.data?.run.status
      return status === 'queued' || status === 'running' ? 0 : IMMUTABLE.staleTime
    },
  })
}

export type LaunchInput = {
  strategyId: string
  kind: RunKind
  params: Record<string, unknown>
  seed?: number
}

/**
 * Queue a run.
 *
 * There is no version in the request: the server pins the head at launch, so a run always
 * records the configuration it actually executed rather than one the client believed was
 * current a moment earlier.
 */
export function useLaunchRun() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (input: LaunchInput): Promise<Run> => {
      const result = await api.POST('/api/v1/strategies/{strategy_id}/runs', {
        params: { path: { strategy_id: input.strategyId } },
        body: {
          kind: input.kind,
          params: input.params,
          ...(input.seed === undefined ? {} : { seed: input.seed }),
        },
      })
      return unwrap(result) as unknown as Run
    },
    onSuccess: (_run, input) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.runs.all })
      void queryClient.invalidateQueries({
        queryKey: queryKeys.strategies.detail(input.strategyId),
      })
      void queryClient.invalidateQueries({ queryKey: queryKeys.strategies.all })
    },
  })
}

/**
 * Ask a run to stop.
 *
 * Cancellation cannot be withdrawn — the database enforces that — so the UI treats this as
 * final once it succeeds. A cancelled run has no result and no error: a partial search is
 * not a cheaper search, and reporting one would invite reading it as a result.
 */
export function useCancelRun() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (runId: string): Promise<Run> => {
      const result = await api.POST('/api/v1/runs/{run_id}/cancel', {
        params: { path: { run_id: runId } },
      })
      return unwrap(result) as unknown as Run
    },
    onSuccess: (run) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.runs.all })
      void queryClient.invalidateQueries({ queryKey: queryKeys.runs.detail(run.id) })
    },
  })
}

export function usePromoteRun() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (input: { runId: string; name: string }): Promise<PromotedStrategy> => {
      const result = await api.POST('/api/v1/runs/{run_id}/promote', {
        params: { path: { run_id: input.runId } },
        body: { name: input.name },
      })
      return unwrap(result)
    },
    onSuccess: () => {
      // A promotion creates a strategy, a version and a queued backtest at once.
      void queryClient.invalidateQueries({ queryKey: queryKeys.strategies.all })
      void queryClient.invalidateQueries({ queryKey: queryKeys.runs.all })
    },
  })
}
