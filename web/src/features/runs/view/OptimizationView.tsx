import {
  Accordion,
  Alert,
  Card,
  Code,
  Divider,
  Group,
  Progress,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core'
import { IconAlertTriangle } from '@tabler/icons-react'
import { Explain, ExplainedLabel } from '../../../components/Explain'
import { Figure, MetricsTable, TooFewTrades } from './Figures'
import { RangeTrack } from './RangeTrack'
import { TradeList } from './TradeList'
import { figuresOf, isIntraday, optimizationResult, type Json } from '../../../lib/result'
import { integer, percent, points, ratio } from '../../../lib/format'

/**
 * What the search found, and what it cost in credibility to find it.
 *
 * The train/test split is structural here rather than a footnote (brief §2.3). Everything in
 * the top band is out of sample; everything that compares train to test is beside it, because
 * the gap between the two is the cheapest overfitting diagnostic there is and a screen showing
 * only the flattering half would be misleading by omission.
 */
export function OptimizationView({ result }: { result: Json | null }) {
  const optimization = optimizationResult(result)
  const test = figuresOf(optimization.testMetrics)
  const train = figuresOf(optimization.trainMetrics)
  const baseline = figuresOf(optimization.baselineTestMetrics)

  const gap = optimization.overfittingGapPct
  const testCagr = test.shown ? test.metrics.cagrPct : null
  const gapExceedsResult = gap !== null && testCagr !== null && gap > testCagr

  return (
    <Stack gap="lg">
      <Alert color="yellow" icon={<IconAlertTriangle size={18} />} variant="light">
        A single train/test split is one draw. Run walk-forward validation before acting on this.
      </Alert>

      <Card padding="md" withBorder>
        <Stack gap="sm">
          <Text fw={600} size="sm">
            Out of sample — the final {integer(optimization.testBars)} bars of history, evaluated
            once, after every choice was final.
          </Text>
          {test.shown ? (
            <Group gap="xl">
              <Figure
                label="Return"
                term="oos_return"
                value={percent(test.metrics.totalReturnPct, { signed: true })}
              />
              <Figure term="cagr" value={percent(test.metrics.cagrPct, { signed: true })} />
              <Figure term="max_drawdown" value={percent(test.metrics.maxDrawdownPct)} />
              <Figure term="sharpe" value={ratio(test.metrics.sharpeRatio)} />
              <Figure term="trades" value={integer(test.metrics.totalTrades)} />
            </Group>
          ) : (
            <TooFewTrades trades={test.trades} />
          )}
        </Stack>
      </Card>

      <Group gap={6}>
        <Title order={4}>Three comparisons</Title>
        <Explain term="three_comparisons" />
      </Group>

      <Card padding="md" withBorder>
        <Stack gap="xs">
          <Text fw={600} size="sm">
            1. What the search bought you
          </Text>
          <Group gap="xl">
            <Figure
              size="md"
              term="improvement"
              value={percent(optimization.improvementPct, { signed: true })}
            />
            {test.shown && baseline.shown && (
              <>
                <Figure
                  size="md"
                  term="optimized"
                  value={percent(test.metrics.totalReturnPct, { signed: true })}
                />
                <Figure
                  size="md"
                  term="unoptimized_baseline"
                  value={percent(baseline.metrics.totalReturnPct, { signed: true })}
                />
              </>
            )}
          </Group>
        </Stack>
      </Card>

      <Card padding="md" withBorder>
        <Stack gap="xs">
          <Text fw={600} size="sm">
            2. Overfitting gap — train CAGR minus test CAGR
          </Text>
          {!test.shown ? (
            <TooFewTrades trades={test.trades} />
          ) : !train.shown ? (
            <Text size="sm">
              The in-sample window is itself below the trade floor, so there is no training CAGR to
              compare against. That is its own warning: a search fitted on too few trades chose
              these parameters.
            </Text>
          ) : (
            <Stack gap="xs">
              <Group gap="md">
                <Text size="sm" w={110}>
                  In sample
                </Text>
                <Progress color="gray" size="lg" value={barWidth(train.metrics.cagrPct)} w={220} />
                <Text fw={500} size="sm">
                  {percent(train.metrics.cagrPct, { signed: true })}
                </Text>
              </Group>
              <Group gap="md">
                <Text size="sm" w={110}>
                  Out of sample
                </Text>
                <Progress color="blue" size="lg" value={barWidth(test.metrics.cagrPct)} w={220} />
                <Text fw={500} size="sm">
                  {percent(test.metrics.cagrPct, { signed: true })}
                </Text>
              </Group>
              <Text size="sm">
                Gap: {points(gap)}.{' '}
                {gapExceedsResult
                  ? 'The gap is larger than the out-of-sample CAGR itself — more of this result ' +
                    'was left behind in the training window than survived into the test one, ' +
                    'which is the signature of a curve fit.'
                  : 'A large positive gap is the signature of a curve fit.'}
              </Text>
            </Stack>
          )}
        </Stack>
      </Card>

      <Card padding="md" withBorder>
        <Stack gap="xs">
          <Text fw={600} size="sm">
            3. Trade count
          </Text>
          {test.shown ? (
            <Text size="sm">
              {integer(test.metrics.totalTrades)} closed trades out of sample — enough for the
              figures above to mean something.
            </Text>
          ) : (
            <TooFewTrades trades={test.trades}>
              Every figure on this screen that describes performance is withheld for that reason.
              The parameter moves below are still shown: they describe what the search did, not how
              well it did it.
            </TooFewTrades>
          )}
        </Stack>
      </Card>

      <Stack gap="xs">
        <Group gap={6}>
          <Title order={4}>Parameters the search moved</Title>
          <Explain term="parameters_moved" />
        </Group>
        {optimization.parametersAtBound.length > 0 && (
          <Alert color="orange" icon={<IconAlertTriangle size={18} />} variant="light">
            {optimization.parametersAtBound.length} parameter
            {optimization.parametersAtBound.length === 1 ? '' : 's'} finished on the edge of the
            range: {optimization.parametersAtBound.join(', ')}. The true optimum probably lies
            outside the range, which means the range was the binding constraint rather than the
            data. Widen it and run again.
          </Alert>
        )}
        <Table withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>
                <ExplainedLabel term="parameter" />
              </Table.Th>
              <Table.Th>
                <ExplainedLabel term="value_before" />
              </Table.Th>
              <Table.Th>
                <ExplainedLabel term="value_after" />
              </Table.Th>
              <Table.Th>
                <ExplainedLabel term="search_range" label="Searched within" />
              </Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {optimization.changes.map((change) => (
              <Table.Tr key={change.path}>
                <Table.Td>
                  <Group gap={6}>
                    <Code>{change.path}</Code>
                    {change.atBound && (
                      <Text c="orange" size="xs">
                        on the bound
                      </Text>
                    )}
                  </Group>
                </Table.Td>
                <Table.Td>{ratio(change.oldValue)}</Table.Td>
                <Table.Td>
                  <Text fw={change.moved ? 600 : 400} size="sm">
                    {ratio(change.newValue)}
                  </Text>
                </Table.Td>
                <Table.Td w={280}>
                  <RangeTrack
                    atBound={change.atBound}
                    high={change.high}
                    low={change.low}
                    newValue={change.newValue}
                    oldValue={change.oldValue}
                  />
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Stack>

      <Accordion variant="contained">
        <Accordion.Item value="diagnostics">
          <Accordion.Control>
            <ExplainedLabel label="Search diagnostics" term="search_effort" />
          </Accordion.Control>
          <Accordion.Panel>
            <Stack gap="sm">
              <Group gap="xl">
                <Figure size="md" term="evaluations" value={integer(optimization.evaluations)} />
                <Figure size="md" term="failures" value={integer(optimization.failures)} />
                <Figure size="md" term="infeasible" value={integer(optimization.infeasible)} />
                <Figure
                  size="md"
                  term="trade_floor_required"
                  value={integer(optimization.minTradesRequired)}
                />
                <Figure size="md" term="trials" value={integer(optimization.trials)} />
                <Figure size="md" term="budget" value={integer(optimization.budget)} />
                <Figure size="md" term="seed" value={integer(optimization.seed)} />
              </Group>
              {optimization.countsExact === false && (
                <Text c="dimmed" size="sm">
                  These counts are approximate: a parallel search does not report every
                  worker&apos;s evaluations exactly.
                </Text>
              )}
              <Text size="sm">Convergence: {optimization.convergenceMessage ?? '—'}</Text>
              {optimization.mostCommonFailure && (
                <Text size="sm">Most common failure: {optimization.mostCommonFailure}</Text>
              )}
            </Stack>
          </Accordion.Panel>
        </Accordion.Item>
      </Accordion>

      {test.shown && (
        <Stack gap="xs">
          <Group gap={6}>
            <Title order={4}>Out-of-sample metrics</Title>
            <Explain term="metrics" />
          </Group>
          <MetricsTable metrics={test.metrics} strategyLabel="Out of sample" />
        </Stack>
      )}

      <Divider />

      <Stack gap="xs">
        <Group gap={6}>
          <Title order={4}>Out-of-sample trades</Title>
          <Explain term="out_of_sample" />
        </Group>
        <TradeList intraday={isIntraday(optimization.history)} trades={optimization.trades} />
      </Stack>
    </Stack>
  )
}

/** Bars are drawn against a fixed 50% span so the two are comparable to each other. */
function barWidth(value: number | null): number {
  if (value === null) return 0
  return Math.min(100, Math.max(0, (Math.abs(value) / 50) * 100))
}
