import { Badge, Tooltip } from '@mantine/core'
import type { ProspectSession } from '../../api/types'

/**
 * What state a sweep is in, including the one a single word cannot carry.
 *
 * A sweep asked to stop while a worker holds it stays *running* until that worker finishes the
 * search it is in — stopping mid-search would pay for compute and throw the result away. The
 * badge says "stopping" for that, because "running" would look like the button did nothing and
 * "stopped" would be a lie about a search still in progress.
 */
export function SessionStatusBadge({ session }: { session: ProspectSession }) {
  if (session.status === 'running' && session.stop_requested) {
    return (
      <Tooltip label="Finishing the search it is in. A sweep never stops mid-search.">
        <Badge color="orange" variant="light">
          Stopping
        </Badge>
      </Tooltip>
    )
  }
  if (session.status === 'running' && !session.claimed_by) {
    return (
      <Tooltip label="No worker has picked this sweep up yet. It resumes from its cursor when one does.">
        <Badge color="gray" variant="light">
          Waiting for a worker
        </Badge>
      </Tooltip>
    )
  }
  if (session.status === 'running') {
    return (
      <Badge color="blue" variant="light">
        Searching
      </Badge>
    )
  }
  if (session.status === 'failed') {
    return (
      <Tooltip label={String(session.error?.message ?? 'The sweep could not continue.')}>
        <Badge color="red" variant="light">
          Failed
        </Badge>
      </Tooltip>
    )
  }
  return (
    <Badge color="gray" variant="light">
      Stopped
    </Badge>
  )
}
