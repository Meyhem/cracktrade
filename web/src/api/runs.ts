import { useQuery } from '@tanstack/react-query'
import { api, unwrap } from './client'
import { queryKeys } from './keys'
import type { Run } from './types'

/**
 * The one poll for in-flight runs.
 *
 * SSE says *that* a run changed but not how far along it is: the worker writes
 * `progress: {stage, percent}` on a ~10s heartbeat and notifies only on claim and on
 * landing. So a moving progress bar needs polling, and one shared query serves every
 * indicator in the app rather than each run row opening its own.
 *
 * It stops polling the moment nothing is active, which is most of the time.
 */
export function useActiveRuns() {
  return useQuery({
    queryKey: queryKeys.runs.active,
    queryFn: async (): Promise<Run[]> => {
      const result = await api.GET('/api/v1/runs', {
        params: { query: { status: 'queued,running', limit: 100 } },
      })
      return unwrap(result).runs as unknown as Run[]
    },
    refetchInterval: (query) => ((query.state.data?.length ?? 0) > 0 ? 2000 : false),
    refetchIntervalInBackground: false,
  })
}

/** The live view of one run, when it is in flight. Falls back to whatever the row carried. */
export function useActiveRun(runId: string | null | undefined): Run | undefined {
  const { data } = useActiveRuns()
  if (!runId) return undefined
  return data?.find((run) => run.id === runId)
}
