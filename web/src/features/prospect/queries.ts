import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '../../api/client'
import {
  queryKeys,
  type ProspectCandidateFilters,
  type ProspectSessionFilters,
} from '../../api/keys'
import type {
  ProspectCandidateDetail,
  ProspectCandidateList,
  ProspectSession,
  ProspectSessionList,
  ProspectSessionRow,
} from '../../api/types'

/**
 * How often a live sweep is re-read.
 *
 * There is no SSE channel for prospecting and deliberately so: the run stream carries a
 * status change, and a sweep's interesting events are a cursor moving and a candidate
 * appearing — neither of which is a state transition the stream models. Polling a running
 * session is a request every few seconds against two indexed queries, and it stops the
 * moment nothing is running.
 */
const LIVE_INTERVAL = 4000

/** A ticker's search takes minutes, so the leaderboard changes far more slowly than the header. */
const LEADERBOARD_INTERVAL = 15000

function anyRunning(sessions: readonly { status: string }[] | undefined): boolean {
  return (sessions ?? []).some((session) => session.status === 'running')
}

export function useProspectSessions(filters: ProspectSessionFilters = {}) {
  return useQuery({
    queryKey: queryKeys.prospect.sessions(filters),
    queryFn: async (): Promise<ProspectSessionList> => {
      const query: Record<string, string | number> = {}
      if (filters.status) query.status = filters.status
      if (filters.limit) query.limit = filters.limit
      const result = await api.GET('/api/v1/prospect/sessions', { params: { query } })
      return unwrap(result)
    },
    refetchInterval: (query) => (anyRunning(query.state.data?.sessions) ? LIVE_INTERVAL : false),
    refetchIntervalInBackground: false,
  })
}

export function useProspectSession(sessionId: string) {
  return useQuery({
    queryKey: queryKeys.prospect.session(sessionId),
    queryFn: async (): Promise<ProspectSessionRow> => {
      const result = await api.GET('/api/v1/prospect/sessions/{session_id}', {
        params: { path: { session_id: sessionId } },
      })
      return unwrap(result) as unknown as ProspectSessionRow
    },
    refetchInterval: (query) => (query.state.data?.status === 'running' ? LIVE_INTERVAL : false),
    refetchIntervalInBackground: false,
  })
}

export function useProspectCandidates(filters: ProspectCandidateFilters, live: boolean) {
  return useQuery({
    queryKey: queryKeys.prospect.candidates(filters),
    queryFn: async (): Promise<ProspectCandidateList> => {
      const query: Record<string, string | number | boolean> = {
        survivors_only: filters.survivorsOnly ?? true,
      }
      if (filters.sessionId) query.session_id = filters.sessionId
      if (filters.ticker) query.ticker = filters.ticker
      if (filters.limit) query.limit = filters.limit
      const result = await api.GET('/api/v1/prospect/candidates', { params: { query } })
      return unwrap(result)
    },
    refetchInterval: live ? LEADERBOARD_INTERVAL : false,
    refetchIntervalInBackground: false,
  })
}

export function useProspectCandidate(candidateId: string) {
  return useQuery({
    queryKey: queryKeys.prospect.candidate(candidateId),
    queryFn: async (): Promise<ProspectCandidateDetail> => {
      const result = await api.GET('/api/v1/prospect/candidates/{candidate_id}', {
        params: { path: { candidate_id: candidateId } },
      })
      return unwrap(result)
    },
  })
}

export type StartSweepInput = {
  name: string
  universe?: string[]
  params?: Record<string, unknown>
}

export function useStartSweep() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (input: StartSweepInput): Promise<ProspectSession> => {
      const result = await api.POST('/api/v1/prospect/sessions', { body: input })
      return unwrap(result)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.prospect.all })
    },
  })
}

/**
 * Stopping a sweep.
 *
 * No optimistic update. The server answers with one of two different outcomes — `stopped` if
 * nobody was working the session, `running` with `stop_requested` if a worker still has to
 * land it between ticks — and a client that guessed would show "stopped" over a sweep that is
 * still finishing a search.
 */
export function useStopSweep() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (sessionId: string): Promise<ProspectSession> => {
      const result = await api.POST('/api/v1/prospect/sessions/{session_id}/stop', {
        params: { path: { session_id: sessionId } },
      })
      return unwrap(result)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.prospect.all })
    },
  })
}
