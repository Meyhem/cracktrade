import {
  Alert,
  Badge,
  Card,
  Center,
  Code,
  Divider,
  Group,
  Loader,
  Modal,
  ScrollArea,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core'
import { IconAlertTriangle } from '@tabler/icons-react'
import { ProblemAlert } from '../../components/ProblemAlert'
import { dateOnly, percent, ratio, relative } from '../../lib/format'
import { useProspectCandidate } from './queries'
import type { ProspectCandidate } from '../../api/types'

/**
 * One candidate, with its three kinds of number under three headings.
 *
 * The order is deliberate and is the opposite of what a reader would choose. Transfer and
 * forward come first because they are the two rungs that decide anything; the holdout figures
 * come last, under a heading that says they are what the search selected on. A panel that led
 * with a +24% holdout return would be read as a result no matter what the caption said.
 */
export function CandidateModal({
  candidate,
  onClose,
}: {
  candidate: ProspectCandidate | null
  onClose: () => void
}) {
  return (
    <Modal
      onClose={onClose}
      opened={candidate !== null}
      size="xl"
      title={candidateTitle(candidate)}
    >
      {candidate && <CandidateBody candidateId={candidate.id} />}
    </Modal>
  )
}

function candidateTitle(candidate: ProspectCandidate | null): string {
  if (!candidate) return ''
  return `${candidate.ticker} — found ${relative(candidate.discovered_at)}`
}

function CandidateBody({ candidateId }: { candidateId: string }) {
  const { data, isPending, error } = useProspectCandidate(candidateId)

  if (error) return <ProblemAlert error={error} />
  if (isPending) {
    return (
      <Center py="xl">
        <Loader />
      </Center>
    )
  }

  return (
    <Stack gap="md">
      <Alert
        color={data.survived_transfer ? 'teal' : 'gray'}
        icon={data.survived_transfer ? undefined : <IconAlertTriangle size={16} />}
        variant="light"
      >
        <Text size="sm">
          {data.survived_transfer
            ? 'This still made money when run unchanged across its family, and beat instruments sharing none of that family’s mechanism. That is a reason to look further — it is not a verdict. Promote it and run a walk-forward to get one.'
            : `Rejected. ${rejectionReason(data.transfer)}`}
        </Text>
      </Alert>

      <div>
        <Title order={5}>Transfer — the same strategy on its relatives</Title>
        <Text c="dimmed" size="xs" mb="xs">
          Run unchanged on every member of {data.transfer.family}, and on control instruments that
          share none of its mechanism. A strategy that only works where it was found is fitted to
          that instrument&rsquo;s noise.
        </Text>
        <SimpleGrid cols={{ base: 2, sm: 4 }}>
          <Figure label="On its own ticker" value={ratio(data.transfer.home_sharpe)} />
          <Figure
            label="Median across family"
            positive={data.transfer.median_sibling_sharpe > 0}
            value={ratio(data.transfer.median_sibling_sharpe)}
          />
          <Figure
            label="Median across controls"
            value={ratio(data.transfer.median_control_sharpe)}
          />
          <Figure
            label="Losing members"
            value={`${data.transfer.negative_members} of ${data.transfer.members}`}
          />
        </SimpleGrid>
        {data.transfer.failures.length > 0 && (
          <Text c="dimmed" mt="xs" size="xs">
            Not measurable: {data.transfer.failures.join(', ')}
          </Text>
        )}
      </div>

      <Divider />

      <div>
        <Title order={5}>Forward — bars that did not exist when this was found</Title>
        <Text c="dimmed" size="xs" mb="xs">
          The only evidence here the search could not have influenced. It accrues slowly, and
          nothing is reported until at least 30 new bars have arrived.
        </Text>
        {data.forward_history.length === 0 ? (
          <Text c="dimmed" size="sm">
            Not measured yet — bars are still accumulating since {dateOnly(data.last_bar_seen)}.
          </Text>
        ) : (
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Scored</Table.Th>
                <Table.Th>Window</Table.Th>
                <Table.Th ta="right">Bars</Table.Th>
                <Table.Th ta="right">Return</Table.Th>
                <Table.Th ta="right">Sharpe</Table.Th>
                <Table.Th ta="right">Trades</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data.forward_history.map((score) => (
                <Table.Tr key={score.scored_at}>
                  <Table.Td>{relative(score.scored_at)}</Table.Td>
                  <Table.Td>
                    {dateOnly(score.first_bar)} → {dateOnly(score.last_bar)}
                  </Table.Td>
                  <Table.Td ta="right">{score.bars}</Table.Td>
                  {/* A window without trades is flat because nothing happened, not because a
                      trade lost nothing. Colouring it red would report a loss that was never
                      taken. */}
                  <Table.Td c={idleReturnColour(score)} ta="right">
                    {percent(score.return_pct, { signed: true })}
                  </Table.Td>
                  {/* `ratio` renders null as "n/a": no trades means no return variance, so the
                      Sharpe is undefined rather than zero or infinite. */}
                  <Table.Td c={score.sharpe === null ? 'dimmed' : 'inherit'} ta="right">
                    {ratio(score.sharpe)}
                  </Table.Td>
                  <Table.Td ta="right">{score.trades}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </div>

      <Divider />

      <div>
        <Title order={5}>What the search selected on — not evidence</Title>
        <Text c="dimmed" size="xs" mb="xs">
          These are the figures the search picked this candidate <em>by</em>. They are shown last
          and labelled because they are the numbers most likely to be large and least likely to mean
          anything: {data.selected_on.distinct_configurations.toLocaleString()} configurations were
          tried, and the best of that many will look good on the sample it was chosen from whether
          or not it works.
        </Text>
        <SimpleGrid cols={{ base: 2, sm: 4 }}>
          <Figure
            label="Holdout return"
            value={percent(data.selected_on.holdout_return_pct, { signed: true })}
          />
          <Figure label="Holdout Sharpe" value={ratio(data.selected_on.holdout_sharpe)} />
          <Figure label="Holdout trades" value={String(data.selected_on.holdout_trades)} />
          <Figure
            label="Configurations tried"
            value={data.selected_on.distinct_configurations.toLocaleString()}
          />
        </SimpleGrid>
        {data.selected_on.holdout_trades < 20 && (
          <Text c="orange" mt="xs" size="xs">
            {data.selected_on.holdout_trades} closed trades is too few to conclude anything from the
            two figures above.
          </Text>
        )}
      </div>

      <Divider />

      <div>
        <Group justify="space-between" mb="xs">
          <Title order={5}>The strategy</Title>
          <Badge variant="light">seed {data.seed}</Badge>
        </Group>
        <Text c="dimmed" mb="xs" size="xs">
          {data.composition}
        </Text>
        <ScrollArea.Autosize mah={280}>
          <Code block>{data.strategy_yaml}</Code>
        </ScrollArea.Autosize>
      </div>
    </Stack>
  )
}

/** Colour for a forward window's return.
 *
 * A window in which the candidate never traded is flat because nothing happened. Red would
 * report a loss it did not take, and green would credit it with holding its ground on purpose;
 * neither is what the row measured.
 */
function idleReturnColour(score: { return_pct: number; trades: number }): string {
  if (score.trades === 0) return 'dimmed'
  return score.return_pct > 0 ? 'teal' : 'red'
}

function rejectionReason(transfer: {
  median_sibling_sharpe: number
  beats_controls: boolean
}): string {
  if (transfer.median_sibling_sharpe <= 0) {
    return 'Across its family it lost money, so what it found does not exist beyond the instrument it was found on.'
  }
  if (!transfer.beats_controls) {
    return 'It made money across its family, but no more than on instruments sharing none of that family’s mechanism — which is what being long the market looks like.'
  }
  return 'It did not clear the transfer test.'
}

function Figure({ label, value, positive }: { label: string; value: string; positive?: boolean }) {
  return (
    <Card padding="xs" withBorder>
      <Text c="dimmed" size="xs">
        {label}
      </Text>
      {positive === undefined ? (
        <Text fw={600}>{value}</Text>
      ) : (
        <Text c={positive ? 'teal' : 'red'} fw={600}>
          {value}
        </Text>
      )}
    </Card>
  )
}
