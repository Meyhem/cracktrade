import { useState } from 'react'
import {
  Anchor,
  Button,
  Card,
  Center,
  Group,
  Loader,
  Stack,
  Text,
  Title,
  Tooltip,
} from '@mantine/core'
import { Link, useParams } from 'react-router'
import { ProblemAlert } from '../../components/ProblemAlert'
import { StaleBadge } from '../../components/StaleBadge'
import { RunStatusCell } from '../../components/RunStatusBadge'
import { EmptyState } from '../../components/EmptyState'
import { useActiveRun } from '../../api/runs'
import { useRun } from './queries'
import { absolute, duration, runKindLabel } from '../../lib/format'
import { BacktestView } from './view/BacktestView'
import { OptimizationView } from './view/OptimizationView'
import { ValidationView } from './view/ValidationView'
import { PromoteRunModal } from './PromoteRunModal'
import { asString, field } from '../../lib/result'
import { explanationOf } from '../../lib/glossary'

/**
 * One run.
 *
 * The header, the failure path and the cancellation note are shared by all three kinds; what
 * each kind *says* differs enough that the three views are separate components rather than one
 * with branches. They read the engine's serialised result through `lib/result.ts`, which is the
 * only place that blob is interpreted.
 */
export function RunViewPage() {
  const { strategyId, runId } = useParams()
  const { data, error, isPending } = useRun(runId ?? '')
  const [promoteOpen, setPromoteOpen] = useState(false)
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
  const isPendingRun = run.status === 'queued' || run.status === 'running'
  // Promotable is the server's judgement, not ours: a backtest has nothing to promote, and
  // neither does a run that did not succeed.
  const optimizedYaml = asString(field(data.result, 'optimized_yaml'))

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
        <Group gap="sm">
          <Anchor component={Link} size="sm" to={`/strategies/${strategyId}/charts?run=${run.id}`}>
            See all charts
          </Anchor>
          {run.promotable && (
            <Tooltip label={explanationOf('promote')} multiline w={280}>
              <Button onClick={() => setPromoteOpen(true)} size="xs">
                Promote to strategy
              </Button>
            </Tooltip>
          )}
        </Group>
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

      {isPendingRun && (
        <EmptyState title={`This run is ${run.status}`}>
          Results appear here once it lands. You can navigate away — the run continues on the
          worker, and several strategies can run at once.
        </EmptyState>
      )}

      {run.status === 'succeeded' && data.result === null && (
        <EmptyState title="This run recorded no result">
          It landed as succeeded with nothing stored, which should not happen. Treat the run as
          unusable rather than as a result of zero.
        </EmptyState>
      )}

      {run.status === 'succeeded' && data.result && (
        <>
          {run.kind === 'backtest' && <BacktestView result={data.result} />}
          {run.kind === 'optimize' && <OptimizationView result={data.result} />}
          {run.kind === 'walk_forward' && (
            <ValidationView checks={data.checks} result={data.result} />
          )}
        </>
      )}
      <PromoteRunModal
        defaultName={data.default_promote_name}
        onClose={() => setPromoteOpen(false)}
        opened={promoteOpen}
        optimizedYaml={optimizedYaml}
        run={run}
      />
    </Stack>
  )
}
