import { Alert, Badge, Group, Stack, Table, Text, Tooltip } from '@mantine/core'
import { IconInfoCircle } from '@tabler/icons-react'
import { integer, percent, points, ratio } from '../../lib/format'
import { deltaFor, type Delta, type MetricKey, type Movement } from './delta'
import { summarise } from './summary'
import type { BuiltRow } from './table'
import type { VersionSummary } from '../../api/types'
import type { ComparableKind } from './delta'
import { ExplainedLabel } from '../../components/Explain'

/**
 * One row per version, oldest at the top, so the strategy reads as a progression.
 *
 * The Δ column is the reason this table is dangerous, and every rule about it lives in
 * `delta.ts` under test. What is left here is rendering — including two things that are not
 * decoration:
 *
 * - **direction is never carried by colour alone.** Every movement is printed signed, and a
 *   green figure is green *and* prefixed `+`. Roughly one man in twelve cannot separate the two
 *   hues, and a screenshot of this table pasted into a chat loses colour entirely.
 * - **an absent figure is an empty cell**, never a zero and never inherited from the row above.
 *   A version with no run of the selected kind has nothing to say, and a table that fills the
 *   gap with its neighbour's number is inventing a run that was never made.
 */
export function ComparisonTable({
  rows,
  versions,
  kind,
}: {
  rows: BuiltRow[]
  versions: VersionSummary[]
  kind: ComparableKind
}) {
  const noteFor = new Map(versions.map((entry) => [entry.version, entry]))

  return (
    <Stack gap="xs">
      <Table.ScrollContainer minWidth={1100}>
        <Table highlightOnHover striped withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>
                <ExplainedLabel term="version" />
              </Table.Th>
              <Table.Th>
                <ExplainedLabel term="changed_fields" />
              </Table.Th>
              <Table.Th>
                <ExplainedLabel term="runs_count" />
              </Table.Th>
              <Table.Th ta="right">
                <ExplainedLabel term="total_return" label="Return" />
              </Table.Th>
              <Table.Th ta="right">
                <ExplainedLabel term="excess" label="vs buy-and-hold" />
              </Table.Th>
              <Table.Th ta="right">
                <ExplainedLabel term="max_drawdown" />
              </Table.Th>
              <Table.Th ta="right">
                <ExplainedLabel term="sharpe" />
              </Table.Th>
              <Table.Th ta="right">
                <ExplainedLabel term="trades" />
              </Table.Th>
              {kind === 'walk_forward' && (
                <Table.Th ta="right">
                  <ExplainedLabel term="fold_win_rate" label="Folds won" />
                </Table.Th>
              )}
              {kind === 'walk_forward' && (
                <Table.Th>
                  <ExplainedLabel term="verdict" />
                </Table.Th>
              )}
              <Table.Th>
                <ExplainedLabel term="delta" label="Δ" />
              </Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((row, index) => (
              <Row
                delta={deltaFor(rows, index)}
                key={row.version}
                kind={kind}
                row={row}
                version={noteFor.get(row.version)}
              />
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>

      {kind === 'walk_forward' && <WalkForwardColumnsNote />}
    </Stack>
  )
}

function Row({
  row,
  version,
  delta,
  kind,
}: {
  row: BuiltRow
  version: VersionSummary | undefined
  delta: Delta
  kind: ComparableKind
}) {
  const figures = row.run?.figures ?? null
  const suppressed = row.run !== null && figures === null

  return (
    <Table.Tr>
      <Table.Td>
        <Group gap={6} wrap="nowrap">
          <Text fw={600} size="sm">
            v{row.version}
          </Text>
          {version?.head && (
            <Badge size="xs" variant="filled">
              head
            </Badge>
          )}
        </Group>
        {version?.note && (
          <Text c="dimmed" size="xs">
            {version.note}
          </Text>
        )}
      </Table.Td>

      <Table.Td>
        <Text c="dimmed" size="xs">
          {version && version.version > 1 ? summarise(version.change_summary, 2) : '—'}
        </Text>
      </Table.Td>

      <Table.Td>
        <Text size="sm">{row.runs === 0 ? '—' : row.runs}</Text>
        {row.runs > 1 && (
          <Tooltip
            label="The most recent one is reported. Picking the best of several would reward this version for having been run more often."
            multiline
            w={280}
          >
            <Text c="dimmed" size="xs">
              latest shown
            </Text>
          </Tooltip>
        )}
      </Table.Td>

      {row.pending ? (
        <Table.Td colSpan={kind === 'walk_forward' ? 7 : 5}>
          <Text c="dimmed" size="xs">
            loading…
          </Text>
        </Table.Td>
      ) : suppressed ? (
        <>
          <Table.Td colSpan={4} ta="right">
            <Text c="dimmed" size="xs">
              below the trade floor — no figures
            </Text>
          </Table.Td>
          <Table.Td ta="right">
            <Text size="sm">{integer(row.run?.trades ?? null)}</Text>
          </Table.Td>
          {kind === 'walk_forward' && <Table.Td />}
          {kind === 'walk_forward' && <Table.Td />}
        </>
      ) : (
        <>
          <Cell text={figures ? percent(figures.returnPct, { signed: true }) : null} />
          <Cell text={figures?.excessPp !== undefined ? pointsOrNull(figures?.excessPp) : null} />
          <Cell text={figures ? percentOrNull(figures.maxDrawdownPct) : null} />
          <Cell text={figures ? ratioOrNull(figures.sharpe) : null} />
          <Cell text={row.run ? integer(row.run.trades) : null} />
          {kind === 'walk_forward' && (
            <Cell text={figures?.foldWinRate != null ? percent(figures.foldWinRate * 100) : null} />
          )}
          {kind === 'walk_forward' && (
            <Table.Td>
              <Verdict credible={row.verdict} />
            </Table.Td>
          )}
        </>
      )}

      <Table.Td>
        <DeltaCell delta={delta} />
      </Table.Td>
    </Table.Tr>
  )
}

/** An empty cell for an absent figure. Never a zero, never the row above's value. */
function Cell({ text }: { text: string | null }) {
  return (
    <Table.Td ta="right">
      {text === null || text === 'n/a' ? (
        <Text c="dimmed" size="sm">
          —
        </Text>
      ) : (
        <Text size="sm">{text}</Text>
      )}
    </Table.Td>
  )
}

function pointsOrNull(value: number | null | undefined): string | null {
  return value === null || value === undefined ? null : points(value)
}

function percentOrNull(value: number | null): string | null {
  return value === null ? null : percent(value)
}

function ratioOrNull(value: number | null): string | null {
  return value === null ? null : ratio(value)
}

function Verdict({ credible }: { credible: boolean | null }) {
  if (credible === null) {
    return (
      <Text c="dimmed" size="xs">
        unvalidated
      </Text>
    )
  }
  return (
    <Badge color={credible ? 'teal' : 'red'} size="sm" variant="light">
      {credible ? 'credible' : 'not credible'}
    </Badge>
  )
}

const METRIC_LABEL: Record<MetricKey, string> = {
  returnPct: 'return',
  excessPp: 'vs hold',
  maxDrawdownPct: 'drawdown',
  sharpe: 'Sharpe',
  foldWinRate: 'folds',
}

/**
 * The Δ cell.
 *
 * A refusal prints as `—` with the reason on hover — the reason is never dropped, because "no
 * comparison was possible" and "the comparison came out flat" are the two readings of an empty
 * cell and they are opposites.
 */
function DeltaCell({ delta }: { delta: Delta }) {
  if (!delta.shown) {
    return (
      <Tooltip label={delta.reason} multiline w={300}>
        <Text c="dimmed" size="sm" style={{ cursor: 'help' }}>
          —
        </Text>
      </Tooltip>
    )
  }

  return (
    <Stack gap={2}>
      <Group gap={6} wrap="wrap">
        {delta.movements.map((movement) => (
          <MovementChip key={movement.metric} movement={movement} />
        ))}
      </Group>
      <Group gap={4} wrap="nowrap">
        <Text c="dimmed" size="xs">
          vs v{delta.against}
        </Text>
        {delta.caveats.length > 0 && (
          <Tooltip label={delta.caveats.join(' ')} multiline w={320}>
            <IconInfoCircle color="var(--mantine-color-orange-6)" size={13} />
          </Tooltip>
        )}
      </Group>
    </Stack>
  )
}

/**
 * One metric's movement.
 *
 * Signed text first, colour second. `+3.1pp` reads as an improvement without any colour at all,
 * which is the requirement — and it is why drawdown can be shown in the same form as return
 * even though English calls a smaller drawdown better: the engine signs it negative, so the
 * arithmetic and the word agree.
 *
 * The movement is computed from full precision, not from the rounded figures in the columns, so
 * a reader checking the subtraction by hand can find the last digit off by one: Sharpes of
 * 1.0956 and 1.0448 print as 1.10 and 1.04 while their difference prints as −0.05. Deriving the
 * Δ from the rounded columns instead would make the arithmetic check out and is the wrong
 * trade — two values a thousandth apart would both round to the same figure and the movement
 * would be labelled *flat*, which is a false claim about direction rather than a rounding
 * artefact in the last place.
 */
function MovementChip({ movement }: { movement: Movement }) {
  const flat = movement.value === 0
  const text =
    movement.metric === 'sharpe'
      ? `${movement.value > 0 ? '+' : ''}${movement.value.toFixed(2)}`
      : movement.metric === 'foldWinRate'
        ? points(movement.value * 100)
        : points(movement.value)

  return (
    <Badge
      color={flat ? 'gray' : movement.improved ? 'teal' : 'red'}
      size="sm"
      // Mantine uppercases badge text by default, which turns "+3.1pp" into "+3.1PP" and
      // "Sharpe" into "SHARPE". The unit matters here and PP is not a unit.
      tt="none"
      variant="light"
    >
      {METRIC_LABEL[movement.metric]} {text}
    </Badge>
  )
}

/**
 * Why three columns are permanently empty for a walk-forward.
 *
 * They are not missing data. A walk-forward re-optimizes in every fold, so the folds are
 * different strategies and the engine refuses to splice their equity curves into one — which
 * means there is no combined curve to take a drawdown or a Sharpe from, and no single excess
 * over buy-and-hold. Averaging the folds would produce a number that looked exactly like the
 * backtest column and meant something else entirely.
 */
function WalkForwardColumnsNote() {
  return (
    <Alert color="gray" variant="light">
      <Text size="xs">
        Drawdown, Sharpe and excess-over-hold are blank for walk-forward runs, and that is the
        honest answer rather than a gap. Every fold re-optimizes from scratch, so the folds are
        different strategies; there is no single equity curve across them to measure a drawdown on.
        Switch to backtest to see those figures for one configuration over one window.
      </Text>
    </Alert>
  )
}
