import { useState } from 'react'
import {
  Badge,
  Button,
  Card,
  Center,
  Group,
  Loader,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core'
import { IconRadar2 } from '@tabler/icons-react'
import { Link } from 'react-router'
import { EmptyState } from '../../components/EmptyState'
import { ProblemAlert } from '../../components/ProblemAlert'
import { relative } from '../../lib/format'
import { useProspectSessions } from './queries'
import { StartSweepModal } from './StartSweepModal'
import { SessionStatusBadge } from './SessionStatusBadge'

/**
 * The sweeps.
 *
 * A sweep is not a run and the screen says so throughout: no progress bar, no percentage, no
 * "complete". A search with no end has no percentage — what there is to say is how far round
 * the universe it has got and how much has survived, and both are counts (spec §19.9).
 */
export function ProspectPage() {
  const [starting, setStarting] = useState(false)
  const { data, isPending, error } = useProspectSessions()

  if (error) return <ProblemAlert error={error} />
  if (isPending) {
    return (
      <Center py="xl">
        <Loader />
      </Center>
    )
  }

  const sessions = data.sessions

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <div>
          <Title order={2}>Prospecting</Title>
          <Text c="dimmed" size="sm">
            A sweep searches one ticker at a time, round-robin, for as long as you leave it running.
            It publishes <strong>candidates</strong>, never verdicts — a candidate becomes credible
            only by passing its own walk-forward after you promote it.
          </Text>
        </div>
        <Button leftSection={<IconRadar2 size={16} />} onClick={() => setStarting(true)}>
          Start a sweep
        </Button>
      </Group>

      {sessions.length === 0 ? (
        <EmptyState
          action={
            <Button onClick={() => setStarting(true)} variant="light">
              Start a sweep
            </Button>
          }
          title="No sweeps yet"
        >
          A sweep evolves a strategy for one ticker, then re-runs it unchanged across that
          ticker&rsquo;s family. Anything that only worked on the instrument it was found on is
          rejected there and then — which is most of what a search produces. Leave it running and
          forward evidence accumulates on whatever survived.
        </EmptyState>
      ) : (
        <Card padding={0} withBorder>
          <Table highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Sweep</Table.Th>
                <Table.Th>State</Table.Th>
                <Table.Th>Now searching</Table.Th>
                <Table.Th ta="right">Laps</Table.Th>
                <Table.Th ta="right">Searches</Table.Th>
                <Table.Th ta="right">Candidates</Table.Th>
                <Table.Th ta="right">Survived transfer</Table.Th>
                <Table.Th>Started</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {sessions.map((session) => (
                <Table.Tr key={session.id}>
                  <Table.Td>
                    <Text
                      component={Link}
                      fw={500}
                      size="sm"
                      to={`/prospect/${session.id}`}
                      td="none"
                      c="inherit"
                    >
                      {session.name}
                    </Text>
                    <Text c="dimmed" size="xs">
                      {session.universe.length} tickers
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <SessionStatusBadge session={session} />
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm">
                      {session.status === 'running' ? session.current_ticker : '—'}
                    </Text>
                  </Table.Td>
                  <Table.Td ta="right">{session.passes_completed}</Table.Td>
                  <Table.Td ta="right">
                    {session.ticks_completed}
                    {session.ticks_failed > 0 && (
                      <Text c="dimmed" component="span" size="xs">
                        {' '}
                        (+{session.ticks_failed} failed)
                      </Text>
                    )}
                  </Table.Td>
                  <Table.Td ta="right">{session.candidates}</Table.Td>
                  <Table.Td ta="right">
                    {session.survivors > 0 ? (
                      <Badge color="teal" variant="light">
                        {session.survivors}
                      </Badge>
                    ) : (
                      <Text c="dimmed" size="sm">
                        0
                      </Text>
                    )}
                  </Table.Td>
                  <Table.Td>
                    <Text c="dimmed" size="sm">
                      {relative(session.created_at)}
                    </Text>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Card>
      )}

      <StartSweepModal onClose={() => setStarting(false)} opened={starting} />
    </Stack>
  )
}
