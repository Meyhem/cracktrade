import { Anchor, Card, Center, Code, Group, Loader, Stack, Text, Title } from '@mantine/core'
import { Link, useParams } from 'react-router'
import { ProblemAlert } from '../../components/ProblemAlert'
import { StaleBadge } from '../../components/StaleBadge'
import { RunStatusCell } from '../../components/RunStatusBadge'
import { EmptyState } from '../../components/EmptyState'
import { useActiveRun } from '../../api/runs'
import { useRun } from './queries'
import { absolute, duration, runKindLabel } from '../../lib/format'

/**
 * A run, in outline.
 *
 * The three full run views land in a later phase; what is here is the header every kind
 * shares and the failure path, which is the part that most needs to exist early — a run that
 * fails at 3am should say why rather than showing a spinner that never resolves.
 */
export function RunViewPage() {
  const { strategyId, runId } = useParams()
  const { data, error, isPending } = useRun(runId ?? '')
  const live = useActiveRun(runId)

  if (isPending) {
    return (
      <Center py="xl">
        <Loader />
      </Center>
    )
  }
  if (error) return <ProblemAlert error={error} />

  const run = live ?? data.run
  const failure = data.error

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Group gap="sm">
          <Title order={3}>
            {runKindLabel(run.kind)} #{run.number}
          </Title>
          <Text c="dimmed" size="sm">
            v{run.version}
          </Text>
          {run.stale && <StaleBadge version={run.version} />}
          <RunStatusCell run={run} />
        </Group>
        <Anchor component={Link} size="sm" to={`/strategies/${strategyId}/charts?run=${run.id}`}>
          See all charts
        </Anchor>
      </Group>

      <Text c="dimmed" size="sm">
        Queued {absolute(run.queued_at)}
        {run.finished_at && ` · finished ${absolute(run.finished_at)}`}
        {run.elapsed_seconds !== null && ` · took ${duration(run.elapsed_seconds)}`} · seed{' '}
        {run.seed}
      </Text>

      {failure && (
        <ProblemAlert
          error={new Error(failure.message)}
          title={`Run failed: ${failure.category.replace(/_/g, ' ')}`}
        />
      )}

      {failure?.category === 'causality_violation' && (
        <Card padding="md" withBorder>
          <Text size="sm">
            The engine refused an operation that would have let information from a later bar reach
            an earlier one. That is a bug in the engine rather than a problem with this
            configuration, and the run was stopped rather than allowed to produce a number.
          </Text>
        </Card>
      )}

      {run.status === 'cancelled' && (
        <Card padding="md" withBorder>
          <Text size="sm">
            Cancelled, and no partial result was kept. A search stopped halfway is not a cheaper
            search — the parameters it had reached were not chosen, they were merely where it
            happened to be.
          </Text>
        </Card>
      )}

      {run.status === 'succeeded' && (
        <EmptyState title="The full run view lands in a later phase">
          The result is stored and complete. Until this screen is built, here is the shape of what
          it holds.
        </EmptyState>
      )}

      {data.result && (
        <Card padding="md" withBorder>
          <Stack gap="xs">
            <Text fw={600} size="sm">
              Result keys
            </Text>
            <Group gap={6}>
              {Object.keys(data.result).map((key) => (
                <Code key={key}>{key}</Code>
              ))}
            </Group>
          </Stack>
        </Card>
      )}
    </Stack>
  )
}
