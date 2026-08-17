import { Card, Group, Stack, Table, Text, Tooltip } from '@mantine/core'
import type { ReactNode } from 'react'
import { suppressionMessage } from '../../../lib/suppression'
import { useMeta } from '../../../api/metaContext'
import type { Metrics } from '../../../lib/result'
import { integer, money, percent, ratio } from '../../../lib/format'

/**
 * The pieces every run view builds its numbers out of.
 *
 * Kept together because they encode one rule between them: a figure is either reported with
 * its units, or replaced by the sentence saying why it is not. There is no third rendering,
 * and in particular no blank cell and no zero — both of those read as information.
 */

/** One labelled number. `hint` is never the only place something important is said. */
export function Figure({
  label,
  value,
  hint,
  size = 'lg',
}: {
  label: string
  value: string
  hint?: string
  size?: 'lg' | 'md'
}) {
  return (
    <Stack gap={2}>
      <Text c="dimmed" size="xs" tt="uppercase">
        {label}
      </Text>
      <Text fw={600} size={size === 'lg' ? 'xl' : 'md'}>
        {value}
      </Text>
      {hint && (
        <Text c="dimmed" size="xs">
          {hint}
        </Text>
      )}
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
const ROWS: { label: string; render: (metrics: Metrics) => string; note?: string }[] = [
  { label: 'Total return', render: (m) => percent(m.totalReturnPct, { signed: true }) },
  { label: 'CAGR', render: (m) => percent(m.cagrPct, { signed: true }) },
  { label: 'Max drawdown', render: (m) => percent(m.maxDrawdownPct) },
  { label: 'Sharpe', render: (m) => ratio(m.sharpeRatio) },
  { label: 'Sortino', render: (m) => ratio(m.sortinoRatio) },
  { label: 'Calmar', render: (m) => ratio(m.calmarRatio) },
  { label: 'Win rate', render: (m) => percent(m.winRatePct) },
  {
    label: 'Profit factor',
    render: (m) => (m.profitFactor === null ? 'no losing trades' : ratio(m.profitFactor)),
    note: 'Gross profit divided by gross loss. Reported as "no losing trades" when there is no loss to divide by — that is a real result, not a missing number.',
  },
  {
    label: 'Exposure',
    render: (m) => percent(m.exposurePct),
    note: 'Share of bars holding a position. Idle cash earns nothing here while the risk-free hurdle is charged across the whole period, so a low exposure makes Sharpe read worse than the trades did.',
  },
  { label: 'Avg holding days', render: (m) => ratio(m.avgHoldingDays, 1) },
  { label: 'Trades', render: (m) => integer(m.totalTrades) },
  { label: 'Final equity', render: (m) => money(m.finalEquity) },
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
          {benchmark && <Table.Th>Buy and hold</Table.Th>}
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {ROWS.map((row) => (
          <Table.Tr key={row.label}>
            <Table.Td>
              <Group gap={6}>
                <Text size="sm">{row.label}</Text>
                {row.note && (
                  <Tooltip label={row.note} multiline w={320}>
                    <Text c="dimmed" size="xs" style={{ cursor: 'help' }}>
                      ⓘ
                    </Text>
                  </Tooltip>
                )}
              </Group>
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
