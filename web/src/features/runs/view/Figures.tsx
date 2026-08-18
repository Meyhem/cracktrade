import { Card, Group, Stack, Table, Text } from '@mantine/core'
import type { ReactNode } from 'react'
import { suppressionMessage } from '../../../lib/suppression'
import { useMeta } from '../../../api/metaContext'
import { Explain, ExplainedLabel } from '../../../components/Explain'
import { termOf, type TermKey } from '../../../lib/glossary'
import type { Metrics } from '../../../lib/result'
import { integer, money, percent, ratio } from '../../../lib/format'

/**
 * The pieces every run view builds its numbers out of.
 *
 * Kept together because they encode one rule between them: a figure is either reported with
 * its units, or replaced by the sentence saying why it is not. There is no third rendering,
 * and in particular no blank cell and no zero — both of those read as information.
 */

/**
 * One labelled number, with its plain-language explanation attached.
 *
 * `term` is required rather than optional, which is the entire point: a figure that nobody
 * thought to explain cannot be added, because it will not type-check. `label` overrides the
 * glossary's own title only where the screen has room for something shorter.
 */
export function Figure({
  term,
  label,
  value,
  size = 'lg',
}: {
  term: TermKey
  label?: string
  value: string
  size?: 'lg' | 'md'
}) {
  return (
    <Stack gap={2}>
      <Group gap={4} wrap="nowrap">
        <Text c="dimmed" size="xs" tt="uppercase">
          {label ?? termOf(term).title}
        </Text>
        <Explain term={term} />
      </Group>
      <Text fw={600} size={size === 'lg' ? 'xl' : 'md'}>
        {value}
      </Text>
    </Stack>
  )
}

/**
 * What stands in for a figure below the trade floor.
 *
 * Occupies the footprint the figures would have, rather than collapsing the layout — a section
 * that quietly shrinks tells the reader nothing was withheld.
 */
export function TooFewTrades({
  trades,
  children,
}: {
  trades: number | null
  children?: ReactNode
}) {
  const { meta } = useMeta()
  return (
    <Card padding="md" withBorder>
      <Stack gap={4}>
        <Text fw={600} size="sm">
          {suppressionMessage(trades ?? 0, meta.trade_floor)}
        </Text>
        <Text c="dimmed" size="sm">
          {children ??
            'The engine withholds performance figures below this count rather than printing a ' +
              'return to two decimal places off a handful of trades. The count itself is the ' +
              'evidence for that, so it is shown.'}
        </Text>
      </Stack>
    </Card>
  )
}

/** The rows of a metrics table, in the brief's order (§2.4). */
const ROWS: { term: TermKey; render: (metrics: Metrics) => string }[] = [
  { term: 'total_return', render: (m) => percent(m.totalReturnPct, { signed: true }) },
  { term: 'cagr', render: (m) => percent(m.cagrPct, { signed: true }) },
  { term: 'max_drawdown', render: (m) => percent(m.maxDrawdownPct) },
  { term: 'sharpe', render: (m) => ratio(m.sharpeRatio) },
  { term: 'sortino', render: (m) => ratio(m.sortinoRatio) },
  { term: 'calmar', render: (m) => ratio(m.calmarRatio) },
  { term: 'win_rate', render: (m) => percent(m.winRatePct) },
  {
    term: 'profit_factor',
    render: (m) => (m.profitFactor === null ? 'no losing trades' : ratio(m.profitFactor)),
  },
  { term: 'exposure', render: (m) => percent(m.exposurePct) },
  { term: 'avg_holding_days', render: (m) => ratio(m.avgHoldingDays, 1) },
  { term: 'trades', render: (m) => integer(m.totalTrades) },
  { term: 'final_equity', render: (m) => money(m.finalEquity) },
]

/**
 * The strategy in one column, buy-and-hold beside it.
 *
 * Two columns rather than one, always, wherever a benchmark exists. A strategy result without
 * its benchmark is not a result (brief §5.7) — 13% where the ticker returned 40% is a failure,
 * and a single column presents it as a success.
 */
export function MetricsTable({
  metrics,
  benchmark,
  strategyLabel = 'Strategy',
}: {
  metrics: Metrics
  benchmark?: Metrics | null
  strategyLabel?: string
}) {
  return (
    <Table withTableBorder>
      <Table.Thead>
        <Table.Tr>
          <Table.Th />
          <Table.Th>{strategyLabel}</Table.Th>
          {benchmark && (
            <Table.Th>
              <ExplainedLabel term="buy_and_hold" />
            </Table.Th>
          )}
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {ROWS.map((row) => (
          <Table.Tr key={row.term}>
            <Table.Td>
              <ExplainedLabel term={row.term} />
            </Table.Td>
            <Table.Td>
              <Text fw={500} size="sm">
                {row.render(metrics)}
              </Text>
            </Table.Td>
            {benchmark && (
              <Table.Td>
                <Text c="dimmed" size="sm">
                  {row.render(benchmark)}
                </Text>
              </Table.Td>
            )}
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  )
}
