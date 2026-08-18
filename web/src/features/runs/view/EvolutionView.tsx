import {
  Alert,
  Badge,
  Card,
  Code,
  Divider,
  Grid,
  Group,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core'
import { IconAlertTriangle, IconCheck, IconX } from '@tabler/icons-react'
import { LimitsPanel } from '../../../components/LimitsPanel'
import { Explain, ExplainedLabel } from '../../../components/Explain'
import { Figure, TooFewTrades } from './Figures'
import { evolutionResult, type Json } from '../../../lib/result'
import { dateOnly, duration, integer, percent, ratio } from '../../../lib/format'
import type { VerdictCheck } from '../../../api/types'

/**
 * A strategy nobody wrote, and the case for and against believing in it.
 *
 * This screen carries a burden the other three do not. A backtest reports on a configuration
 * the reader chose and can therefore argue with; this reports on one that was *selected*, out
 * of hundreds or thousands, because it fit the past — and it arrives looking exactly as
 * authoritative as a strategy someone reasoned their way to. Everything below is arranged
 * around keeping those two things distinguishable.
 *
 * The layout follows from that. The holdout leads, because it is the only measurement here
 * that was not involved in the choosing. The in-sample segments come after the verdict rather
 * than beside it, under their own heading saying what they are, because they are the numbers
 * that *did* the choosing and would otherwise read as five more pieces of supporting evidence.
 * The search diagnostics come last, and they include the count that the deflation divides by.
 *
 * Nothing here recomputes a verdict. `checks` is the engine's own list and `isCredible` its own
 * conjunction (spec sections 15.1 and 16.6).
 */
export function EvolutionView({ result, checks }: { result: Json | null; checks: VerdictCheck[] }) {
  const report = evolutionResult(result)
  const credible = report.isCredible === true
  const holdout = report.holdoutMetrics
  const withheld = holdout?.hasEnoughTradesToJudge === false

  return (
    <Stack gap="lg">
      <Card
        padding="lg"
        style={{
          borderColor: credible ? 'var(--mantine-color-green-6)' : 'var(--mantine-color-red-6)',
          borderWidth: 2,
        }}
        withBorder
      >
        <Stack gap="sm">
          <Group gap="sm">
            {credible ? <IconCheck size={28} /> : <IconX size={28} />}
            <Title order={2}>{credible ? 'CREDIBLE' : 'NOT CREDIBLE'}</Title>
          </Group>
          {credible ? (
            <Text size="sm">
              This composition cleared every check below, measured on history the search never saw.
              That is not a prediction, and it is one holdout looked at once — see the note at the
              foot of this page before treating it as more than that.
            </Text>
          ) : (
            <Stack gap={4}>
              {report.failures.length > 0 ? (
                report.failures.map((failure) => (
                  <Text key={failure} size="sm">
                    ✗ {failure}
                  </Text>
                ))
              ) : (
                <Text size="sm">
                  This run recorded no credibility verdict. That is a fault in the run rather than a
                  judgement about the strategy, and it is reported as not credible because an
                  unknown must never read as a pass.
                </Text>
              )}
            </Stack>
          )}
        </Stack>
      </Card>

      <Stack gap="xs">
        <Group gap={6}>
          <Title order={4}>What it composed</Title>
          <Explain term="composition" />
        </Group>
        <Card padding="md" withBorder>
          <Stack gap="sm">
            <Text size="sm">{report.composition ?? '—'}</Text>
            {report.blocks.length > 0 && (
              <Group gap={6}>
                {report.blocks.map((block) => (
                  <Badge key={block} size="sm" variant="light">
                    {block.replace(/_/g, ' ')}
                  </Badge>
                ))}
              </Group>
            )}
            <Text c="dimmed" size="sm">
              Assembled from a fixed library of building blocks. Nobody chose these conditions
              because they made sense — they were kept because they scored well on the segments
              below.
            </Text>
          </Stack>
        </Card>
      </Stack>

      <Group gap={6}>
        <Title order={4}>On the holdout</Title>
        <Explain term="holdout" />
      </Group>

      {withheld ? (
        <TooFewTrades trades={holdout?.totalTrades ?? null}>
          The composition traded too rarely on the holdout for its return to mean anything. That is
          not a small problem here: the search had every opportunity to find something that trades,
          and what it settled on does not.
        </TooFewTrades>
      ) : (
        <Group gap="xl">
          <Figure
            term="total_return"
            label="Holdout return"
            value={percent(holdout?.totalReturnPct ?? null, { signed: true })}
          />
          <Figure
            term="buy_and_hold"
            value={percent(report.benchmark?.metrics?.totalReturnPct ?? null, { signed: true })}
          />
          <Figure term="max_drawdown" value={percent(holdout?.maxDrawdownPct ?? null)} />
          <Figure term="sharpe" value={ratio(holdout?.sharpeRatio ?? null)} />
          <Figure term="trades" value={integer(holdout?.totalTrades ?? null)} />
          <Figure
            term="overfitting_gap"
            value={percent(report.overfittingGapPct, { signed: true })}
          />
        </Group>
      )}

      <Group gap={6}>
        <Title order={4}>Robustness checks</Title>
        <Explain term="robustness_checks" />
      </Group>
      <Grid>
        {checks.map((check) => (
          <Grid.Col key={check.name} span={{ base: 12, md: 6 }}>
            <CheckCard check={check} />
          </Grid.Col>
        ))}
      </Grid>

      <Group gap={6}>
        <Title order={4}>The segments that chose it</Title>
        <Explain term="in_sample_segments" />
      </Group>
      {/* The most important caption on the page. Without it this table reads as four more
          results, when in fact it is the selection criterion wearing the same clothes as
          evidence. */}
      <Alert color="orange" icon={<IconAlertTriangle size={18} />} variant="light">
        None of these are evidence. These are the windows the search scored candidates on, so the
        winner looks good here by definition — it was kept because it did. They are shown because
        the spread across them is what fitness actually selected on: a strategy carried by one
        segment and one that was steady across all four are different objects, and the median alone
        cannot tell them apart.
      </Alert>
      <Table withTableBorder>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>
              <ExplainedLabel term="segments" label="Segment" />
            </Table.Th>
            <Table.Th>Window</Table.Th>
            <Table.Th>
              <ExplainedLabel term="in_sample" label="Return" />
            </Table.Th>
            <Table.Th>
              <ExplainedLabel term="max_drawdown" />
            </Table.Th>
            <Table.Th>
              <ExplainedLabel term="trades" />
            </Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {report.segments.map((segment) => (
            <Table.Tr key={segment.index}>
              <Table.Td>
                <Group gap={6}>
                  <Text size="sm">{(segment.index ?? 0) + 1}</Text>
                  <Badge color={segment.wasProfitable ? 'green' : 'red'} size="xs" variant="light">
                    {segment.wasProfitable ? 'up' : 'down'}
                  </Badge>
                </Group>
              </Table.Td>
              <Table.Td>
                <Text size="sm">
                  {dateOnly(segment.firstBar)} → {dateOnly(segment.lastBar)}
                </Text>
              </Table.Td>
              <Table.Td>
                <Text c="dimmed" size="sm">
                  {percent(segment.metrics?.totalReturnPct ?? null, { signed: true })}
                </Text>
              </Table.Td>
              <Table.Td>
                <Text c="dimmed" size="sm">
                  {percent(segment.metrics?.maxDrawdownPct ?? null)}
                </Text>
              </Table.Td>
              <Table.Td>{integer(segment.metrics?.totalTrades ?? null)}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>

      <Group gap={6}>
        <Title order={4}>The search</Title>
        <Explain term="evolution" />
      </Group>
      <Card padding="md" withBorder>
        <Stack gap="md">
          <Group gap="xl">
            <Figure
              size="md"
              term="trials"
              label="Distinct strategies"
              value={integer(report.distinctConfigurations)}
            />
            <Figure size="md" term="evaluations" value={integer(report.genomesEvaluated)} />
            <Figure size="md" term="population" value={integer(report.population)} />
            <Figure size="md" term="generations" value={integer(report.generations)} />
            <Figure
              size="md"
              term="trade_floor_required"
              value={integer(report.minTradesRequired)}
            />
          </Group>

          <Text c="dimmed" size="sm">
            {integer(report.distinctConfigurations)} distinct strategies were scored across{' '}
            {integer(report.evolutionBars)} bars, and the winner was judged on{' '}
            {integer(report.holdoutBars)} it had never seen. Took {duration(report.elapsedSeconds)},
            seed {integer(report.seed)}.
          </Text>

          {report.failedCandidates !== null && report.failedCandidates > 0 && (
            <Alert color="orange" icon={<IconAlertTriangle size={18} />} variant="light">
              {integer(report.failedCandidates)} candidates could not be simulated at all
              {report.mostCommonFailure ? `: ${report.mostCommonFailure}` : '.'} Any number above
              zero here is a defect in the block library rather than a fact about the market, and it
              means the search explored less than its budget suggests.
            </Alert>
          )}

          <ProgressTrace scores={report.bestScoreByGeneration} />
        </Stack>
      </Card>

      <Group gap={6}>
        <Title order={4}>Cost sensitivity</Title>
        <Explain term="cost_sensitivity" />
      </Group>
      <Card padding="md" withBorder>
        <Stack gap="xs">
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>
                  <ExplainedLabel term="cost_ladder" label="Costs" />
                </Table.Th>
                <Table.Th>
                  <ExplainedLabel term="slippage" />
                </Table.Th>
                <Table.Th>
                  <ExplainedLabel term="commission" />
                </Table.Th>
                <Table.Th>
                  <ExplainedLabel term="total_return" label="Return" />
                </Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {(report.costs?.scenarios ?? []).map((scenario) => (
                <Table.Tr key={scenario.multiple}>
                  <Table.Td>{ratio(scenario.multiple, 1)}×</Table.Td>
                  <Table.Td>{percent(scenario.slippagePct)}</Table.Td>
                  <Table.Td>{percent(scenario.commissionPct)}</Table.Td>
                  <Table.Td>
                    {percent(scenario.metrics?.totalReturnPct ?? null, { signed: true })}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          <Text size="sm">
            {report.costs?.breakEvenMultiple === null ||
            report.costs?.breakEvenMultiple === undefined
              ? 'The edge did not reach break-even within the range tested.'
              : `Break-even at ${ratio(report.costs.breakEvenMultiple, 1)}× the configured costs.`}
          </Text>
        </Stack>
      </Card>

      <Group gap={6}>
        <Title order={4}>Confidence intervals</Title>
        <Explain term="confidence_interval" />
      </Group>
      <Grid>
        {[
          { label: 'Mean daily holdout return', interval: report.meanReturnInterval },
          { label: 'Total holdout return', interval: report.totalReturnInterval },
        ].map(({ label, interval }) => (
          <Grid.Col key={label} span={{ base: 12, md: 6 }}>
            <Card padding="md" withBorder>
              <Stack gap={4}>
                <Text fw={600} size="sm">
                  {label}
                </Text>
                <Text size="sm">
                  {ratio(interval?.point ?? null, 4)} ({ratio(interval?.low ?? null, 4)} to{' '}
                  {ratio(interval?.high ?? null, 4)}, {percent((interval?.confidence ?? 0) * 100)})
                </Text>
                <Text c={interval?.excludesZero ? 'dimmed' : 'orange'} size="sm">
                  {interval?.excludesZero
                    ? 'The interval excludes zero.'
                    : 'The interval straddles zero — this return is not distinguishable from luck.'}
                </Text>
              </Stack>
            </Card>
          </Grid.Col>
        ))}
      </Grid>

      <Divider />

      <Stack gap="xs">
        <Group gap={6}>
          <Title order={4}>The composed configuration</Title>
          <Explain term="winning_configuration" />
        </Group>
        <Text c="dimmed" size="sm">
          This lives in the run, not in the strategy it ran against. Promote it to get something
          that can be edited, backtested and validated on its own terms.
        </Text>
        <Card padding="md" withBorder>
          <Code block>{report.strategyYaml ?? '—'}</Code>
        </Card>
      </Stack>

      {/* Stated on the page rather than in the docs, because it is the caveat most likely to be
          lost: a holdout is spent by being read, and running evolution again on the same ticker
          means the next answer was chosen partly by knowing how this one did. */}
      <Alert color="gray" variant="light">
        <Text size="sm">
          The holdout is one contiguous stretch of history, evaluated once. Running evolution again
          on this ticker does not get a fresh one — the second answer will have been chosen partly
          by your knowing how this one did. A walk-forward against the promoted strategy is the
          harder question, and the only thing that clears its warning.
        </Text>
      </Alert>

      <LimitsPanel />
    </Stack>
  )
}

/**
 * The best score after each generation.
 *
 * A bar per generation rather than a chart component: this is a shape question — did it keep
 * improving, or did it flatten early — and the answer is legible at a glance from relative
 * heights. Scores are objective values, so their absolute size means nothing here and is
 * deliberately not labelled.
 */
function ProgressTrace({ scores }: { scores: (number | null)[] }) {
  const usable = scores.filter((score): score is number => score !== null && score > -1e30)
  if (usable.length < 2) return null

  const low = Math.min(...usable)
  const high = Math.max(...usable)
  const span = high - low
  const flattenedAt = usable.findIndex((score) => score === high)
  const flatEarly = flattenedAt >= 0 && flattenedAt < usable.length / 2

  // Keyed by generation rather than by position: the generation number is the bar's actual
  // identity, and it stays right if the series is ever filtered differently.
  const bars = usable.map((score, index) => ({ generation: index + 1, score }))

  return (
    <Stack gap={6}>
      <Group gap={6}>
        <Text c="dimmed" size="xs" tt="uppercase">
          Best score by generation
        </Text>
        <Explain term="search_progress" />
      </Group>
      <Group align="flex-end" gap={2} h={48}>
        {bars.map((bar) => (
          <div
            key={bar.generation}
            style={{
              backgroundColor: 'var(--mantine-color-blue-5)',
              borderRadius: 2,
              flex: 1,
              // A flat trace is the interesting case, so an all-equal series draws as full
              // height rather than dividing by zero into nothing.
              height: `${span === 0 ? 100 : 8 + (92 * (bar.score - low)) / span}%`,
              minWidth: 3,
            }}
            title={`Generation ${bar.generation}: ${bar.score.toFixed(3)}`}
          />
        ))}
      </Group>
      {flatEarly && (
        <Text c="dimmed" size="xs">
          The search stopped improving at generation {flattenedAt + 1} of {usable.length}. The
          remaining generations still count as attempts against the deflated Sharpe, so a shorter
          run would have reached the same strategy with a lower bar to clear.
        </Text>
      )}
    </Stack>
  )
}

/** One robustness check, with the engine's own plain-English explanation of what it means. */
function CheckCard({ check }: { check: VerdictCheck }) {
  return (
    <Card h="100%" padding="md" withBorder>
      <Stack gap={6}>
        <Group gap="xs">
          {check.passed ? <IconCheck size={16} /> : <IconX size={16} />}
          <Text fw={600} size="sm">
            {check.label}
          </Text>
          <Badge color={check.passed ? 'green' : 'red'} size="xs" variant="light">
            {check.passed ? 'passed' : 'failed'}
          </Badge>
        </Group>
        <Text size="sm">{check.stat}</Text>
        <Text c="dimmed" size="sm">
          {check.plain}
        </Text>
        {!check.passed && <Text size="sm">{check.detail}</Text>}
      </Stack>
    </Card>
  )
}
