import { Table, Text, Tooltip } from '@mantine/core'
import { headlineFigure, suppressionOf, type Reported } from '../../lib/suppression'
import { percent, points, integer } from '../../lib/format'
import type { Run, RunKind, WalkForwardHeadline } from '../../api/types'

/**
 * The per-kind list columns, rendered from the server's own headline.
 *
 * The figures are computed server-side so that every table and every detail header agree.
 * Below the trade floor the server omits them entirely, and this renders the sentence the
 * engine would have said rather than a blank cell — a blank reads as "nothing happened",
 * which is a different claim from "not enough evidence to say".
 */

/** One figure, or the reason there is no figure. */
function Figure({
  value,
  render,
  suppressed,
}: {
  value: Reported<number>
  render: (value: number) => string
  suppressed: string | null
}) {
  if (value.state === 'present') return <Text size="sm">{render(value.value)}</Text>

  if (value.state === 'null') {
    return (
      <Tooltip label="The engine computed a value that is not finite. That is a result, not a missing number.">
        <Text c="dimmed" size="sm">
          not finite
        </Text>
      </Tooltip>
    )
  }

  if (suppressed) {
    return (
      <Tooltip label={suppressed} multiline w={260}>
        <Text c="dimmed" fs="italic" size="sm">
          too few trades
        </Text>
      </Tooltip>
    )
  }

  return (
    <Text c="dimmed" size="sm">
      —
    </Text>
  )
}

/**
 * A count the engine reports whatever the trade floor says — trials and trade counts are
 * never suppressed, since they are the evidence for the suppression rather than a claim
 * about performance. A run still in flight has no headline at all, and reads as absent.
 */
function Count({ value }: { value: unknown }) {
  if (typeof value !== 'number') {
    return (
      <Text c="dimmed" size="sm">
        —
      </Text>
    )
  }
  return <Text size="sm">{integer(value)}</Text>
}

export function HeadlineCells({ run, kind }: { run: Run; kind: RunKind }) {
  const headline = run.headline
  const suppression = suppressionOf(headline)
  const suppressed = suppression.suppressed ? suppression.message : null

  const figure = (key: string) => headlineFigure(headline, key)
  const raw = (headline ?? {}) as unknown as Record<string, unknown>

  if (kind === 'backtest') {
    return (
      <>
        <Table.Td>
          <Figure
            render={(v) => percent(v, { signed: true })}
            suppressed={suppressed}
            value={figure('return_pct')}
          />
        </Table.Td>
        <Table.Td>
          <Figure
            render={(v) => percent(v, { signed: true })}
            suppressed={suppressed}
            value={figure('benchmark_return_pct')}
          />
        </Table.Td>
        <Table.Td>
          <Figure render={points} suppressed={suppressed} value={figure('excess_pp')} />
        </Table.Td>
        <Table.Td>
          <Figure
            render={(v) => percent(v)}
            suppressed={suppressed}
            value={figure('max_drawdown_pct')}
          />
        </Table.Td>
        <Table.Td>
          <Count value={raw.trades} />
        </Table.Td>
      </>
    )
  }

  if (kind === 'optimize') {
    return (
      <>
        <Table.Td>
          <Figure
            render={(v) => percent(v, { signed: true })}
            suppressed={suppressed}
            value={figure('oos_return_pct')}
          />
        </Table.Td>
        <Table.Td>
          <Figure
            render={(v) => percent(v, { signed: true })}
            suppressed={suppressed}
            value={figure('improvement_pct')}
          />
        </Table.Td>
        <Table.Td>
          <Figure
            render={(v) => points(v)}
            suppressed={suppressed}
            value={figure('overfitting_gap_pct')}
          />
        </Table.Td>
        <Table.Td>
          <Count value={raw.trials} />
        </Table.Td>
        <Table.Td>
          <Count value={raw.trades} />
        </Table.Td>
      </>
    )
  }

  const walkForward = headline as WalkForwardHeadline | null
  return (
    <>
      <Table.Td>
        <Figure
          render={(v) => percent(v, { signed: true })}
          suppressed={null}
          value={figure('combined_oos_pct')}
        />
      </Table.Td>
      <Table.Td>
        <Figure
          render={(v) => percent(v, { signed: true })}
          suppressed={null}
          value={figure('benchmark_pct')}
        />
      </Table.Td>
      <Table.Td>
        <Text size="sm">{walkForward?.profitable_folds ?? '—'}</Text>
      </Table.Td>
      <Table.Td>
        <Count value={walkForward?.oos_trades} />
      </Table.Td>
      <Table.Td>
        {walkForward ? (
          <Text c={walkForward.is_credible ? 'teal' : 'red'} fw={600} size="sm">
            {walkForward.is_credible
              ? 'Credible'
              : `Not credible · ${walkForward.failed_checks} failed`}
          </Text>
        ) : (
          <Text c="dimmed" size="sm">
            —
          </Text>
        )}
      </Table.Td>
    </>
  )
}
