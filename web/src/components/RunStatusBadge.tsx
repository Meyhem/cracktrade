import { Badge, Group, Progress, Text, Tooltip } from '@mantine/core'
import { RUN_STATUS_COLOR } from '../theme/theme'
import type { Run, RunProgress, RunStatus } from '../api/types'

const STATUS_LABEL: Record<RunStatus, string> = {
  queued: 'Queued',
  running: 'Running',
  succeeded: 'Succeeded',
  failed: 'Failed',
  cancelled: 'Cancelled',
}

export function RunStatusBadge({
  status,
  cancelRequested = false,
}: {
  status: RunStatus
  cancelRequested?: boolean
}) {
  // Cancellation cannot be withdrawn once asked for, so a run in this state is going to
  // stop; saying "Running" would invite the user to keep waiting for a result.
  if (cancelRequested && (status === 'running' || status === 'queued')) {
    return (
      <Tooltip label="Cancellation was requested and cannot be withdrawn.">
        <Badge color="orange" variant="light">
          Cancelling
        </Badge>
      </Tooltip>
    )
  }

  return (
    <Badge color={RUN_STATUS_COLOR[status]} variant="light">
      {STATUS_LABEL[status]}
    </Badge>
  )
}

/**
 * Progress for an in-flight run.
 *
 * The stage string is the worker's own ("generation 4 of 10", "fold 2 of 6 - generation 3
 * of 10") and is shown verbatim: it says what is happening in terms the engine actually
 * measures, which a percentage alone does not.
 */
export function RunProgressBar({ progress }: { progress: RunProgress | null }) {
  if (!progress) return null

  return (
    <Group gap="xs" wrap="nowrap">
      <Progress
        aria-label={progress.stage}
        style={{ flex: 1, minWidth: 80 }}
        value={progress.percent}
      />
      <Text c="dimmed" size="xs" style={{ whiteSpace: 'nowrap' }}>
        {progress.stage}
      </Text>
    </Group>
  )
}

/** Status and, while it is in flight, how far along the run is. */
export function RunStatusCell({ run }: { run: Run }) {
  if (run.status === 'running' && run.progress) {
    return (
      <Group gap="xs" wrap="nowrap">
        <RunStatusBadge cancelRequested={run.cancel_requested} status={run.status} />
        <RunProgressBar progress={run.progress} />
      </Group>
    )
  }
  return <RunStatusBadge cancelRequested={run.cancel_requested} status={run.status} />
}
