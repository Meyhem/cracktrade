import { useState } from 'react'
import {
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Center,
  Group,
  Loader,
  Progress,
  SegmentedControl,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
  Tooltip,
  UnstyledButton,
} from '@mantine/core'
import { IconAlertTriangle, IconPlayerPlay, IconPlayerStop } from '@tabler/icons-react'
import { Link, useParams } from 'react-router'
import { ProblemAlert } from '../../components/ProblemAlert'
import { EmptyState } from '../../components/EmptyState'
import { percent, ratio, relative } from '../../lib/format'
import { useProspectCandidates, useProspectSession, useResumeSweep, useStopSweep } from './queries'
import { SessionStatusBadge } from './SessionStatusBadge'
import { CandidateModal } from './CandidateModal'
import type { ProspectCandidate } from '../../api/types'

/**
 * One sweep: where it has got to, and what it has published.
 *
 * The layout enforces §19.9's separation. Backtest and forward figures sit in adjacent columns
 * and are both labelled; the holdout figure the search *selected on* is not a column at all,
 * because a reader scanning a table believes the biggest number and that is the number most
 * likely to be large and least likely to mean anything. It is in the detail panel, under a
 * heading that says what it is.
 *
 * There is no progress bar over the sweep as a whole. A search with no end has no percentage.
 * The one bar here covers the current lap, which does have a denominator.
 */
export function SessionPage() {
  const { sessionId = '' } = useParams()
  const [survivorsOnly, setSurvivorsOnly] = useState(true)
  const [open, setOpen] = useState<ProspectCandidate | null>(null)

  const session = useProspectSession(sessionId)
  const live = session.data?.status === 'running'
  const candidates = useProspectCandidates({ sessionId, survivorsOnly, limit: 50 }, live ?? false)
  const stop = useStopSweep()
  const resume = useResumeSweep()

  if (session.error) return <ProblemAlert error={session.error} />
  if (session.isPending) {
    return (
      <Center py="xl">
        <Loader />
      </Center>
    )
  }

  const row = session.data
  const lap = row.universe.length > 0 ? (row.cursor_index / row.universe.length) * 100 : 0

  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-start">
        <div>
          <Anchor component={Link} size="sm" to="/prospect">
            ← All sweeps
          </Anchor>
          <Group gap="sm">
            <Title order={2}>{row.name}</Title>
            <SessionStatusBadge session={row} />
          </Group>
          <Text c="dimmed" size="xs">
            {row.universe.join(' · ')}
          </Text>
        </div>
        {row.status === 'running' ? (
          <Button
            color="red"
            disabled={row.stop_requested}
            leftSection={<IconPlayerStop size={16} />}
            loading={stop.isPending}
            onClick={() => stop.mutate(sessionId)}
            variant="light"
          >
            {row.stop_requested ? 'Stopping…' : 'Stop'}
          </Button>
        ) : (
          <Button
            leftSection={<IconPlayerPlay size={16} />}
            loading={resume.isPending}
            onClick={() => resume.mutate(sessionId)}
            variant="light"
          >
            Resume
          </Button>
        )}
      </Group>

      {stop.error && <ProblemAlert error={stop.error} />}
      {resume.error && <ProblemAlert error={resume.error} />}
      {row.error && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />} title="The sweep stopped">
          {String(row.error.message ?? 'No reason was recorded.')}
        </Alert>
      )}

      <SimpleGrid cols={{ base: 2, sm: 4 }}>
        <Stat
          hint="Complete passes over the whole ticker list. Each lap re-searches every ticker with a fresh seed and against bars that have moved on since the last one."
          label="Laps completed"
          value={String(row.passes_completed)}
        />
        <Stat
          hint="Searches that finished. A ticker whose history the provider will not serve is counted as failed and the sweep carries on."
          label="Searches run"
          value={`${row.ticks_completed}${row.ticks_failed ? ` (+${row.ticks_failed} failed)` : ''}`}
        />
        <Stat
          hint="Every strategy the sweep found. A candidate is a record of something worth looking at, not a verdict."
          label="Candidates"
          value={String(row.candidates)}
        />
        <Stat
          hint="Candidates that still made money when run unchanged across their ticker's family, and did so by more than on unrelated instruments. This is the rejection that does the work."
          label="Survived transfer"
          value={String(row.survivors)}
        />
      </SimpleGrid>

      {row.status === 'running' && (
        <Card padding="sm" withBorder>
          <Group justify="space-between" mb={6}>
            <Text size="sm">
              Lap {row.passes_completed + 1} — searching{' '}
              <Text component="span" fw={600}>
                {row.current_ticker}
              </Text>
            </Text>
            <Text c="dimmed" size="xs">
              {row.cursor_index} of {row.universe.length} tickers this lap
            </Text>
          </Group>
          {/* The lap has a denominator; the sweep does not. Only the lap gets a bar. */}
          <Progress animated={!row.stop_requested} value={lap} />
        </Card>
      )}

      <Group justify="space-between">
        <Title order={4}>Candidates</Title>
        <SegmentedControl
          data={[
            { label: 'Survived transfer', value: 'survivors' },
            { label: 'Everything found', value: 'all' },
          ]}
          onChange={(value) => setSurvivorsOnly(value === 'survivors')}
          size="xs"
          value={survivorsOnly ? 'survivors' : 'all'}
        />
      </Group>

      {candidates.error && <ProblemAlert error={candidates.error} />}
      {candidates.data && candidates.data.candidates.length === 0 ? (
        <EmptyState
          title={survivorsOnly ? 'Nothing has survived transfer yet' : 'No candidates yet'}
        >
          {survivorsOnly
            ? 'Most searches produce a strategy that works only on the instrument it was found on. Those are rejected here rather than shown as results. Switch to “Everything found” to see what was rejected and why.'
            : 'The first search takes a few minutes. Candidates appear as each ticker finishes.'}
        </EmptyState>
      ) : (
        <Card padding={0} withBorder>
          <Table highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Ticker</Table.Th>
                <Table.Th>Built from</Table.Th>
                <Table.Th ta="right">
                  <Tooltip label="The same strategy run unchanged on its ticker's relatives. Median Sharpe across them.">
                    <span>Transfer (backtest)</span>
                  </Tooltip>
                </Table.Th>
                <Table.Th ta="right">
                  <Tooltip label="Measured on bars that did not exist when the candidate was found. The only figure here the search could not have influenced.">
                    <span>Forward (live bars)</span>
                  </Tooltip>
                </Table.Th>
                <Table.Th>Found</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {candidates.data?.candidates.map((candidate) => (
                <Table.Tr key={candidate.id}>
                  <Table.Td>
                    {/* The control is in the cell rather than on the row: a `tr` with an
                        onClick is unreachable by keyboard and invisible to a screen reader,
                        and every other table in this app puts a real control here. */}
                    <UnstyledButton onClick={() => setOpen(candidate)}>
                      <Group gap={6}>
                        <Text fw={500} size="sm" td="underline">
                          {candidate.ticker}
                        </Text>
                        {!candidate.survived_transfer && (
                          <Badge color="gray" size="xs" variant="light">
                            rejected
                          </Badge>
                        )}
                      </Group>
                    </UnstyledButton>
                  </Table.Td>
                  <Table.Td>
                    <Text c="dimmed" lineClamp={1} size="xs" maw={340}>
                      {candidate.composition}
                    </Text>
                  </Table.Td>
                  <Table.Td ta="right">
                    <Text
                      c={candidate.transfer.median_sibling_sharpe > 0 ? 'teal' : 'dimmed'}
                      size="sm"
                    >
                      {ratio(candidate.transfer.median_sibling_sharpe)}
                    </Text>
                    <Text c="dimmed" size="xs">
                      vs {ratio(candidate.transfer.median_control_sharpe)} control
                    </Text>
                  </Table.Td>
                  <Table.Td ta="right">
                    {candidate.forward ? (
                      <>
                        <Text c={candidate.forward.return_pct > 0 ? 'teal' : 'red'} size="sm">
                          {percent(candidate.forward.return_pct, { signed: true })}
                        </Text>
                        <Text c="dimmed" size="xs">
                          {candidate.forward.bars} bars, {candidate.forward.trades} trades
                        </Text>
                      </>
                    ) : (
                      // Never a zero. "Not measured yet" and "measured and flat" must not
                      // render the same.
                      <Tooltip label="Not enough bars have arrived since this was found. Nothing is reported until at least 30 have.">
                        <Text c="dimmed" size="sm">
                          not yet
                        </Text>
                      </Tooltip>
                    )}
                  </Table.Td>
                  <Table.Td>
                    <Text c="dimmed" size="xs">
                      {relative(candidate.discovered_at)}
                    </Text>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Card>
      )}

      <CandidateModal candidate={open} onClose={() => setOpen(null)} />
    </Stack>
  )
}

function Stat({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <Card padding="sm" withBorder>
      <Tooltip label={hint} multiline w={280}>
        <Text c="dimmed" size="xs">
          {label}
        </Text>
      </Tooltip>
      <Text fw={600} size="xl">
        {value}
      </Text>
    </Card>
  )
}
