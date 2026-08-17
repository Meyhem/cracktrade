import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { queryKeys } from './keys'

/**
 * Live run updates.
 *
 * The stream deliberately carries only `{id, strategy_id, status}` — no result, no progress
 * — so that there is exactly one code path that renders a run, the one that fetched it.
 * This hook therefore invalidates rather than writes into the cache. Progress percentages
 * come from the poller in `useActiveRuns`, because the worker writes them on a heartbeat and
 * does not notify per tick.
 */

export type RunEvent = {
  id: string
  strategy_id: string
  status: string
}

export type SseState = 'connecting' | 'open' | 'closed'

function isRunEvent(value: unknown): value is RunEvent {
  if (typeof value !== 'object' || value === null) return false
  const candidate = value as Record<string, unknown>
  return typeof candidate.id === 'string' && typeof candidate.strategy_id === 'string'
}

/**
 * Subscribe once, at the app root.
 *
 * Returns the connection state so the shell can say when live updates are not arriving —
 * a silently dead stream would leave a queued run looking stuck forever, and the user would
 * reasonably conclude the worker had died.
 */
export function useRunEvents(): SseState {
  const queryClient = useQueryClient()
  const [state, setState] = useState<SseState>('connecting')

  useEffect(() => {
    const source = new EventSource('/api/v1/events')

    const handle = (event: MessageEvent<string>) => {
      let payload: unknown
      try {
        payload = JSON.parse(event.data)
      } catch {
        return
      }
      if (!isRunEvent(payload)) return

      void queryClient.invalidateQueries({ queryKey: queryKeys.runs.detail(payload.id) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.runs.all })
      void queryClient.invalidateQueries({
        queryKey: queryKeys.strategies.detail(payload.strategy_id),
      })
      void queryClient.invalidateQueries({ queryKey: queryKeys.strategies.all })

      if (event.type === 'run.finished') {
        // Series only exist once the run landed; the catalog fetched while it was running
        // was correctly empty and would otherwise stay that way.
        void queryClient.invalidateQueries({ queryKey: queryKeys.runs.seriesCatalog(payload.id) })
      }
    }

    source.addEventListener('run.updated', handle)
    source.addEventListener('run.finished', handle)
    source.addEventListener('open', () => setState('open'))
    source.addEventListener('error', () => {
      // EventSource reconnects on its own; the state is reported so the shell can say so.
      setState(source.readyState === EventSource.CLOSED ? 'closed' : 'connecting')
    })

    return () => source.close()
  }, [queryClient])

  return state
}
