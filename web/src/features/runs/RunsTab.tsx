import { useState } from 'react'
import { Anchor, Button, Center, Group, Loader, Table, Text, Tooltip } from '@mantine/core'
import { IconX } from '@tabler/icons-react'
import { Link } from 'react-router'
import { ProblemAlert } from '../../components/ProblemAlert'
import { EmptyState } from '../../components/EmptyState'
import { StaleBadge } from '../../components/StaleBadge'
import { RunStatusCell } from '../../components/RunStatusBadge'
import { useActiveRuns } from '../../api/runs'
import { useStrategyContext } from '../strategy/context'
import { LaunchRunModal } from './LaunchRunModal'
import { useCancelRun, useRuns } from './queries'
import { HeadlineCells } from './headline'
import { HEADLINE_COLUMNS } from './columns'
import { ExplainedLabel } from '../../components/Explain'
import { duration, relative } from '../../lib/format'
import type { Run, RunKind } from '../../api/types'

/**
 * The per-kind run table.
 *
 * One component for all three kinds: the columns differ, but everything around them —
 * staleness, progress, cancellation, the empty state — is the same, and three near-copies
 * would drift.
 */

const EMPTY_STATE: Record<RunKind, { title: string; body: string; action: string }> = {
  backtest: {
    title: 'No backtests yet',
    body: 'A backtest runs the configuration exactly as written and compares it to buying and holding the same ticker over the same window. It takes seconds, and it is the fastest way to find out whether the strategy does anything at all.',
    action: 'Run a backtest',
  },
  optimize: {
    title: 'No optimizations yet',
    body: 'An optimization searches the parameters you left unpinned, fitting on the first 80% of history and reporting on the last 20% it never saw. It takes minutes. A single split is one draw, so treat the result as a candidate rather than a conclusion.',
    action: 'Run an optimization',
  },
  walk_forward: {
    title: 'Not validated',
    body: 'A walk-forward optimizes and evaluates across successive folds, then judges whether the result survives its robustness checks. It is the only run that issues a verdict, and the only evidence on this screen that pushes back on having chosen the ticker with hindsight.',
    action: 'Run a walk-forward',
  },
}

function CancelButton({ run }: { run: Run }) {
  const cancel = useCancelRun()
  const pending = run.status === 'queued' || run.status === 'running'
  if (!pending || run.cancel_requested) return null

  return (
    <Tooltip label="A cancelled run keeps no partial result — a search stopped early is not a cheaper search.">
      <Button
        color="gray"
        leftSection={<IconX size={14} />}
        loading={cancel.isPending}
        onClick={() => cancel.mutate(run.id)}
        size="compact-xs"
        variant="subtle"
      >
        Cancel
      </Button>
    </Tooltip>
  )
}

export function RunsTab({ kind }: { kind: RunKind }) {
  const strategy = useStrategyContext()
  const [launchOpen, setLaunchOpen] = useState(false)
  const { data, error, isPending } = useRuns({ strategyId: strategy.id, kind })
  const { data: active } = useActiveRuns()

  // A queued or running row is refetched by the poller; merging here means the progress bar
  // moves without the whole table refetching every two seconds.
  const rows = (data?.runs ?? []).map((run) => active?.find((live) => live.id === run.id) ?? run)

  if (isPending) {
    return (
      <Center py="xl">
        <Loader />
      </Center>
    )
  }
  if (error) return <ProblemAlert error={error} />

  const empty = EMPTY_STATE[kind]

  return (
    <>
      {rows.length === 0 ? (
        <EmptyState
          action={<Button onClick={() => setLaunchOpen(true)}>{empty.action}</Button>}
          title={empty.title}
        >
          {empty.body}
        </EmptyState>
      ) : (
        <Table highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>
                <ExplainedLabel term="run" />
              </Table.Th>
              <Table.Th>
                <ExplainedLabel term="version" />
              </Table.Th>
              {HEADLINE_COLUMNS[kind].map((column) => (
                <Table.Th key={`${column.term}-${column.label ?? ''}`}>
                  <ExplainedLabel {...column} />
                </Table.Th>
              ))}
              <Table.Th>
                <ExplainedLabel term="status" />
              </Table.Th>
              <Table.Th>
                <ExplainedLabel term="elapsed" />
              </Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((run) => (
              <Table.Tr key={run.id}>
                <Table.Td>
                  <Anchor
                    component={Link}
                    size="sm"
                    to={`/strategies/${strategy.id}/runs/${run.id}`}
                  >
                    #{run.number}
                  </Anchor>
                  <Text c="dimmed" size="xs" title={run.queued_at}>
                    {relative(run.queued_at)}
                  </Text>
                </Table.Td>
                <Table.Td>
                  <Group gap={6}>
                    <Text size="sm">v{run.version}</Text>
                    {run.stale && <StaleBadge version={run.version} />}
                  </Group>
                </Table.Td>
                <HeadlineCells kind={kind} run={run} />
                <Table.Td>
                  <RunStatusCell run={run} />
                </Table.Td>
                <Table.Td>
                  <Text size="sm">{duration(run.elapsed_seconds)}</Text>
                </Table.Td>
                <Table.Td>
                  <CancelButton run={run} />
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}

      <LaunchRunModal kind={kind} onClose={() => setLaunchOpen(false)} opened={launchOpen} />
    </>
  )
}
