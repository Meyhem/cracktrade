import {
  Badge,
  Card,
  Code,
  Divider,
  Grid,
  Group,
  Progress,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core'
import { IconCheck, IconX } from '@tabler/icons-react'
import { LimitsPanel } from '../../../components/LimitsPanel'
import { Explain, ExplainedLabel } from '../../../components/Explain'
import { Figure } from './Figures'
import { validationResult, type Json } from '../../../lib/result'
import { dateOnly, integer, percent, ratio } from '../../../lib/format'
import type { VerdictCheck } from '../../../api/types'

/**
 * The screen a user should be looking at when they decide to commit money.
 *
 * The verdict is a boolean, deliberately not a score. A number that is 80% trustworthy is not
 * something to put money behind, and averaging the checks would let a strong headline return
 * paper over a failed overfitting test — which is the single failure this whole product exists
 * to prevent (brief §2.5).
 *
 * Nothing here recomputes anything. `checks` is the engine's own list, with its own plain-English
 * explanation per check, and `is_credible` is the engine's conjunction over it (spec §12, §15.1).
 * A second implementation of the verdict is a second verdict, and eventually the two disagree in
 * public.
 */
export function ValidationView({
  result,
  checks,
}: {
  result: Json | null
  checks: VerdictCheck[]
}) {
  const report = validationResult(result)
  const credible = report.isCredible === true

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
              This strategy cleared every robustness check below. That is not a prediction — it
              means the result survived the tests the engine knows how to apply, and the limits at
              the bottom of this page still hold.
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
                // Reached only when the run recorded no verdict at all. Failing closed is the
                // right direction — the alternative is presenting an unknown as a pass — but it
                // has to say that is what happened rather than showing a bare heading.
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

      <Group gap="xl">
        <Figure
          term="fold_win_rate"
          value={
            report.foldWinRate === null
              ? 'n/a'
              : `${integer(report.profitableFolds)}/${integer(report.folds.length)}`
          }
        />
        <Figure term="combined_oos" value={percent(report.combinedReturnPct, { signed: true })} />
        <Figure
          term="buy_and_hold"
          value={percent(report.benchmark?.totalReturnPct ?? null, { signed: true })}
        />
        <Figure term="median_fold" value={percent(report.medianReturnPct, { signed: true })} />
        <Figure term="fold_spread" value={percent(report.returnIqrPct)} />
        <Figure
          label="Out-of-sample trades"
          term="out_of_sample"
          value={integer(report.totalTrades)}
        />
      </Group>

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
        <Title order={4}>Folds</Title>
        <Explain term="folds" />
      </Group>
      <Text c="dimmed" size="sm">
        Each fold re-optimizes independently, so every row below chose its own parameters. A
        strategy profitable in 6 of 6 folds and one carried entirely by fold 3 are different
        objects, and an average cannot tell them apart.
      </Text>
      <Table withTableBorder>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>
              <ExplainedLabel term="fold" />
            </Table.Th>
            <Table.Th>
              <ExplainedLabel term="out_of_sample" label="Test window" />
            </Table.Th>
            <Table.Th>
              <ExplainedLabel term="out_of_sample" label="Out of sample" />
            </Table.Th>
            <Table.Th>
              <ExplainedLabel term="in_sample" />
            </Table.Th>
            <Table.Th>
              <ExplainedLabel term="trades" />
            </Table.Th>
            <Table.Th>
              <ExplainedLabel term="parameters_moved" label="Parameters it chose" />
            </Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {report.folds.map((fold) => (
            <Table.Tr key={fold.index}>
              <Table.Td>
                <Group gap={6}>
                  <Text size="sm">{fold.index + 1}</Text>
                  <Badge color={fold.wasProfitable ? 'green' : 'red'} size="xs" variant="light">
                    {fold.wasProfitable ? 'up' : 'down'}
                  </Badge>
                </Group>
              </Table.Td>
              <Table.Td>
                <Text size="sm">
                  {dateOnly(fold.firstTestBar)} → {dateOnly(fold.lastTestBar)}
                </Text>
              </Table.Td>
              <Table.Td>
                <Text fw={500} size="sm">
                  {percent(fold.metrics?.totalReturnPct ?? null, { signed: true })}
                </Text>
              </Table.Td>
              <Table.Td>
                <Text c="dimmed" size="sm">
                  {percent(fold.trainMetrics?.totalReturnPct ?? null, { signed: true })}
                </Text>
              </Table.Td>
              <Table.Td>{integer(fold.metrics?.totalTrades ?? null)}</Table.Td>
              <Table.Td>
                <Stack gap={2}>
                  {Object.entries(fold.parameters).map(([path, value]) => (
                    <Text key={path} size="xs">
                      <Code>{path}</Code> {ratio(value)}
                    </Text>
                  ))}
                </Stack>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>

      <Group gap={6}>
        <Title order={4}>Cost sensitivity</Title>
        <Explain term="cost_sensitivity" />
      </Group>
      <Card padding="md" withBorder>
        <Stack gap="xs">
          <Text c="dimmed" size="sm">
            The headline recomputed with costs multiplied. An edge that dies when costs double
            belongs to the broker.
          </Text>
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
          { label: 'Mean fold return', interval: report.meanReturnInterval },
          { label: 'Total return', interval: report.totalReturnInterval },
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
          <Title order={4}>The winning configuration</Title>
          <Explain term="winning_configuration" />
        </Group>
        <Card padding="md" withBorder>
          <Code block>{report.optimizedYaml ?? '—'}</Code>
        </Card>
      </Stack>

      <LimitsPanel />
    </Stack>
  )
}

/**
 * One robustness check.
 *
 * `plain` is the engine's own one-line explanation of what the check means. The user is a
 * trader, not a statistician, and a bare "PBO 0.62" teaches nothing.
 */
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

/** Kept for the PBO scale, which needs 0.5 drawn as a hard line rather than implied. */
export function ProbabilityScale({ value, limit }: { value: number | null; limit: number }) {
  return (
    <Stack gap={2}>
      <Progress size="lg" value={value === null ? 0 : value * 100} />
      <Text c="dimmed" size="xs">
        {ratio(value)} against a {ratio(limit)} bar
      </Text>
    </Stack>
  )
}
