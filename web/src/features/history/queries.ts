import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '../../api/client'
import { IMMUTABLE, queryKeys } from '../../api/keys'
import type { DiffResponse, RunDetailNarrowed, VersionOut, VersionSummary } from '../../api/types'

export function useVersions(strategyId: string) {
  return useQuery({
    queryKey: queryKeys.versions.list(strategyId),
    queryFn: async (): Promise<VersionSummary[]> => {
      const result = await api.GET('/api/v1/strategies/{strategy_id}/versions', {
        params: { path: { strategy_id: strategyId } },
      })
      return unwrap(result)
    },
  })
}

/** One stored version, exactly as saved. Immutable, so it is fetched once and kept. */
export function useVersion(strategyId: string, version: number | null) {
  return useQuery({
    queryKey: queryKeys.versions.detail(strategyId, version ?? -1),
    enabled: version !== null,
    ...IMMUTABLE,
    queryFn: async (): Promise<VersionOut> => {
      const result = await api.GET('/api/v1/strategies/{strategy_id}/versions/{version}', {
        params: { path: { strategy_id: strategyId, version: version ?? 1 } },
      })
      return unwrap(result)
    },
  })
}

/**
 * Two versions compared.
 *
 * Both the structured groups and the two YAML panes come from here rather than from diffing
 * the stored texts client-side: the server canonicalises both sides through the YAML writer
 * (spec §15.1, amended 2026-08-17), which is what makes a listed change correspond to a visible
 * line. A client-side diff of the raw stored documents would show an imported version as having
 * changed in every line.
 */
export function useVersionDiff(strategyId: string, from: number | null, to: number | null) {
  return useQuery({
    queryKey: queryKeys.versions.diff(strategyId, from ?? -1, to ?? -1),
    enabled: from !== null && to !== null,
    ...IMMUTABLE,
    queryFn: async (): Promise<DiffResponse> => {
      const result = await api.GET('/api/v1/strategies/{strategy_id}/diff', {
        params: { path: { strategy_id: strategyId }, query: { from: from ?? 1, to: to ?? 1 } },
      })
      return unwrap(result)
    },
  })
}

/**
 * Run details for the comparison table, one query per selected run.
 *
 * The list endpoint's `headline` is not enough: it carries no Sharpe, no fold win rate and no
 * frame digest, and the last of those is what the vintage caveat is built on. Fetching details
 * is therefore not an optimisation failure but the only way to fill the columns honestly.
 *
 * One query per run rather than one for all of them, because a finished run is immutable and
 * these keys are shared with the run views — opening a run after reading the table costs
 * nothing, and a version added later refetches one run instead of all of them.
 */
export function useRunDetails(runIds: string[]) {
  return useQueries({
    queries: runIds.map((runId) => ({
      queryKey: queryKeys.runs.detail(runId),
      ...IMMUTABLE,
      queryFn: async (): Promise<RunDetailNarrowed> => {
        const result = await api.GET('/api/v1/runs/{run_id}', {
          params: { path: { run_id: runId } },
        })
        return unwrap(result) as unknown as RunDetailNarrowed
      },
    })),
  })
}

/**
 * Restore an older version.
 *
 * Appends a copy at the head; nothing is rewound and nothing is deleted (spec §14.2). The
 * response is the *new* version, whose number is one past the previous head rather than the
 * number being restored.
 */
export function useRestoreVersion(strategyId: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (input: { version: number; note?: string }): Promise<VersionOut> => {
      const result = await api.POST('/api/v1/strategies/{strategy_id}/versions/{version}/restore', {
        params: { path: { strategy_id: strategyId, version: input.version } },
        body: input.note === undefined ? {} : { note: input.note },
      })
      return unwrap(result)
    },
    onSuccess: () => {
      // The head moved, so every run against the old one is stale and the editor is showing a
      // config that is no longer current.
      void queryClient.invalidateQueries({ queryKey: queryKeys.strategies.all })
      void queryClient.invalidateQueries({ queryKey: queryKeys.versions.all(strategyId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.runs.all })
    },
  })
}
