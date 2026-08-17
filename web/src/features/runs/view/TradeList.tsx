import { useMemo, useState } from 'react'
import { Badge, Group, SegmentedControl, Stack, Table, Text } from '@mantine/core'
import type { Trade } from '../../../lib/result'
import { dateOnly, integer, money, percent } from '../../../lib/format'

/**
 * Every trade the run made.
 *
 * Open trades are shown and marked, never counted. An unrealized gain is not a result (brief
 * §5.2), so the footer's totals run over closed trades only and say so rather than leaving the
 * reader to assume it.
 */

type Filter = 'all' | 'winners' | 'losers' | 'open'

const COLUMNS = [
  'Entry',
  'Exit',
  'Entry price',
  'Exit price',
  'Size',
  'PnL',
  'Return',
  'Fees',
  'Days',
] as const

/**
 * A stable identity for a row.
 *
 * The engine gives trades no id. Entry date alone is not unique — a strategy can close and
 * re-enter on the same bar — so the key carries the exit and the size too, which together
 * cannot repeat: two positions opened and closed on the same dates with the same size are the
 * same position.
 */
function keyOf(trade: Trade): string {
  return [trade.entryDate, trade.exitDate ?? 'open', trade.size, trade.pnl].join('|')
}

export function TradeList({ trades }: { trades: Trade[] }) {
  const [filter, setFilter] = useState<Filter>('all')

  const rows = useMemo(() => {
    switch (filter) {
      case 'winners':
        return trades.filter((trade) => !trade.isOpen && trade.isWinner)
      case 'losers':
        return trades.filter((trade) => !trade.isOpen && !trade.isWinner)
      case 'open':
        return trades.filter((trade) => trade.isOpen)
      default:
        return trades
    }
  }, [trades, filter])

  const closed = trades.filter((trade) => !trade.isOpen)
  const open = trades.length - closed.length
  const realised = closed.reduce((total, trade) => total + (trade.pnl ?? 0), 0)

  if (trades.length === 0) {
    return (
      <Text c="dimmed" size="sm">
        This run opened no positions at all. That is a result about the entry condition rather than
        about the strategy&apos;s performance — check how often the signal was defined.
      </Text>
    )
  }

  return (
    <Stack gap="sm">
      <Group justify="space-between">
        <SegmentedControl
          data={[
            { label: `All (${trades.length})`, value: 'all' },
            { label: `Winners (${closed.filter((t) => t.isWinner).length})`, value: 'winners' },
            { label: `Losers (${closed.filter((t) => !t.isWinner).length})`, value: 'losers' },
            { label: `Open (${open})`, value: 'open' },
          ]}
          onChange={(value) => setFilter(value as Filter)}
          size="xs"
          value={filter}
        />
        <Text c="dimmed" size="xs">
          Realised PnL over {integer(closed.length)} closed trade
          {closed.length === 1 ? '' : 's'}: {money(realised)}
          {open > 0 && ` · ${integer(open)} still open and not counted`}
        </Text>
      </Group>

      <Table highlightOnHover stickyHeader>
        <Table.Thead>
          <Table.Tr>
            {COLUMNS.map((column) => (
              <Table.Th key={column}>{column}</Table.Th>
            ))}
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map((trade) => (
            <Table.Tr key={keyOf(trade)}>
              <Table.Td>{dateOnly(trade.entryDate)}</Table.Td>
              <Table.Td>
                {trade.isOpen ? (
                  <Badge color="blue" size="xs" variant="light">
                    open
                  </Badge>
                ) : (
                  dateOnly(trade.exitDate)
                )}
              </Table.Td>
              <Table.Td>{money(trade.entryPrice)}</Table.Td>
              <Table.Td>{trade.exitPrice === null ? '—' : money(trade.exitPrice)}</Table.Td>
              <Table.Td>{money(trade.size, 4)}</Table.Td>
              <Table.Td>
                <Group gap={4}>
                  <Text size="sm">{money(trade.pnl)}</Text>
                  {!trade.isOpen && (
                    <Text c="dimmed" size="xs">
                      {trade.isWinner ? '▲' : '▼'}
                    </Text>
                  )}
                </Group>
              </Table.Td>
              <Table.Td>{percent(trade.returnPct, { signed: true })}</Table.Td>
              <Table.Td>{money(trade.fees)}</Table.Td>
              <Table.Td>{integer(trade.holdingDays)}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Stack>
  )
}
