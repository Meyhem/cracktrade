import { Alert, Card, Code, Divider, Group, Select, Stack, Text } from '@mantine/core'
import { IconAlertTriangle, IconInfoCircle } from '@tabler/icons-react'
import { numberAt, putAt, removeAt, stringAt } from './document'
import { issuesAt, issuesUnder } from './issues'
import { Field, NumberField, TextField } from './fields'
import { searchIndex, type SectionProps } from './section'
import { OptimizeControl, SectionOptimizeControl } from './OptimizeControl'
import { IndicatorsSection } from './IndicatorsSection'
import { SignalField } from './SignalField'
import { money } from '../../lib/format'
import { useMeta } from '../../api/metaContext'
import { Explain } from '../../components/Explain'

const SIZING_TYPES = ['fixed_pct', 'fixed_cash', 'fixed_shares']

export function ConfigForm(section: SectionProps) {
  return (
    <Stack gap="xl">
      <BasicsSection {...section} />
      <Divider />
      <ExecutionSection {...section} />
      <Divider />
      <IndicatorsSection {...section} />
      <Divider />
      <RulesSection {...section} />
      <Divider />
      <SizingSection {...section} />
    </Stack>
  )
}

function BasicsSection(section: SectionProps) {
  return (
    <Stack gap="sm">
      <Group gap={4} wrap="nowrap">
        <Text fw={600}>Strategy and universe</Text>
        <Explain term="universe" />
      </Group>
      <Group align="flex-start" gap="lg">
        <TextField
          term="strategy_name"
          label="Name"
          path={['strategy', 'name']}
          section={section}
        />
        <TextField
          description="Exactly one symbol. This engine does not do portfolios."
          term="ticker"
          label="Ticker"
          path={['universe', 'ticker']}
          section={section}
        />
      </Group>
      <Group align="flex-start" gap="lg">
        <TextField
          term="start_date"
          label="Start date"
          path={['universe', 'start_date']}
          placeholder="2023-01-01"
          section={section}
        />
        <TextField
          description="Defaults to today when omitted."
          term="end_date"
          label="End date"
          path={['universe', 'end_date']}
          placeholder="2025-12-31"
          section={section}
        />
      </Group>
      {issuesUnder(section.validation?.errors ?? [], 'universe')
        .filter((issue) => issue.path === 'universe')
        .map((issue) => (
          <Text c="red" key={issue.message} size="xs">
            {issue.message}
          </Text>
        ))}
    </Stack>
  )
}

/**
 * Capital and frictions.
 *
 * The two unit conventions here sit in adjacent fields and mean different things:
 * `commission_pct: 0.05` is 0.05%, while `risk_free_rate: 0.04` is 4%. The brief calls this a
 * genuine footgun, and it is — a user reading them as the same convention is off by a factor
 * of a hundred on one of them. Both get their unit in the suffix and their resolved value
 * spelled out underneath in the terms they would check it in.
 */
function ExecutionSection(section: SectionProps) {
  const capital = numberAt(section.value, ['execution', 'initial_capital'])
  const commission = numberAt(section.value, ['execution', 'commission_pct'])
  const slippage = numberAt(section.value, ['execution', 'slippage_pct'])
  const riskFree = numberAt(section.value, ['execution', 'risk_free_rate'])

  return (
    <Stack gap="sm">
      <Group gap={4} wrap="nowrap">
        <Text fw={600}>Execution</Text>
        <Explain term="execution" />
      </Group>
      <Group align="flex-start" gap="lg">
        <NumberField
          hint="The account this strategy starts with."
          term="initial_capital"
          label="Initial capital"
          path={['execution', 'initial_capital']}
          section={section}
          suffix="$"
        />
        <NumberField
          hint={onATrade(capital, commission, 'commission')}
          term="commission"
          label="Commission"
          path={['execution', 'commission_pct']}
          section={section}
          step={0.01}
          suffix="%"
        />
        <NumberField
          hint={onATrade(capital, slippage, 'slippage')}
          term="slippage"
          label="Slippage"
          path={['execution', 'slippage_pct']}
          section={section}
          step={0.01}
          suffix="%"
        />
        <NumberField
          hint={
            riskFree === null
              ? 'A fraction, not a percent: 0.04 means 4% a year.'
              : `${riskFree * 100}% a year — written as a fraction, unlike the two fields to its left.`
          }
          term="risk_free_rate"
          label="Risk-free rate"
          path={['execution', 'risk_free_rate']}
          section={section}
          step={0.01}
          suffix="frac"
        />
      </Group>
    </Stack>
  )
}

/**
 * The resolved cost of one trade, in dollars.
 *
 * The percentage is printed exactly as configured, never through `percent()`. That helper
 * rounds to one decimal, which turns a commission of `0.05` into "0.1%" — doubling, in the
 * hint whose entire purpose is to stop someone misreading this number by a factor of a hundred.
 * A resolved value that is itself wrong is worse than no resolved value.
 */
function onATrade(capital: number | null, pct: number | null, what: string): string {
  if (pct === null) return `A percent, not a fraction: 0.05 means 0.05% ${what}.`
  if (capital === null) return `${pct}% — a percent, not a fraction.`
  return `${pct}% = $${money((capital * pct) / 100)} of ${what} on a $${money(capital, 0)} trade.`
}

/**
 * Entry and exit.
 *
 * The exit is where the schema's subtlest rules live, and all of them are about a strategy that
 * looks configured and does not behave as configured: an exit with no mechanism holds its first
 * position forever, a shadowed stop is set and does nothing, and `min_holding_days` suppresses
 * signal and time exits but never stops.
 */
function RulesSection(section: SectionProps) {
  const meta = useMeta()
  const search = searchIndex(section.validation)
  const exitPinned = (section.value.exit as { optimize?: unknown } | undefined)?.optimize === false
  const exitErrors = issuesAt(section.validation?.errors ?? [], 'exit')

  // The chain is the engine's, read off `/meta`, not a list of three names repeated here. The
  // whole point of serving `stop_priority` is that a UI saying one order and an engine
  // resolving another cannot be told apart from a working one until it has misled someone.
  const stops = meta.meta.exit_fields
    .filter((field) => field.stop_priority !== null)
    .sort((left, right) => (left.stop_priority ?? 0) - (right.stop_priority ?? 0))
    .map((field) => field.name)

  const set = stops.filter((field) => numberAt(section.value, ['exit', field]) !== null)
  const active = set[0]
  const shadowed = set.slice(1)

  return (
    <Stack gap="md">
      <Group gap={4} wrap="nowrap">
        <Text fw={600}>Entry</Text>
        <Explain term="entry_signal" />
      </Group>
      <SignalField
        term="entry_signal"
        label="Signal"
        path={['entry', 'signal']}
        placeholder="(close > sma_long) & (rsi_ind < 35)"
        section={section}
      />

      <Group justify="space-between">
        <Group gap={4} wrap="nowrap">
          <Text fw={600}>Exit</Text>
          <Explain term="exit_signal" />
        </Group>
        <SectionOptimizeControl
          entryPath={['exit']}
          label="exit"
          onChange={section.onChange}
          value={section.value}
          yaml={section.yaml}
        />
      </Group>

      {exitErrors.map((issue) => (
        <Alert
          color="red"
          icon={<IconAlertTriangle size={16} />}
          key={issue.message}
          variant="light"
        >
          {issue.message}
        </Alert>
      ))}

      <SignalField
        term="exit_signal"
        label="Exit signal"
        path={['exit', 'signal']}
        placeholder="close < sma_long"
        section={section}
      />

      <Group align="flex-start" gap="lg">
        {stops.map((field) => (
          <NumberField
            control={
              <OptimizeControl
                entryPath={['exit']}
                onChange={section.onChange}
                parameterKey={field}
                searchable={search.get(`exit.${field}`)}
                sectionPinned={exitPinned}
                value={section.value}
                yaml={section.yaml}
              />
            }
            hint={
              shadowed.includes(field)
                ? `Set, but it does nothing: ${active} outranks it.`
                : field === active
                  ? 'The active stop.'
                  : undefined
            }
            key={field}
            label={field}
            path={['exit', field]}
            section={section}
            suffix={field === 'atr_stop_multiplier' ? '×ATR' : '%'}
          />
        ))}
        <NumberField
          control={
            <OptimizeControl
              entryPath={['exit']}
              onChange={section.onChange}
              parameterKey="take_profit_pct"
              searchable={search.get('exit.take_profit_pct')}
              sectionPinned={exitPinned}
              value={section.value}
              yaml={section.yaml}
            />
          }
          hint="Orthogonal to the stops — it always applies."
          term="take_profit"
          label="take_profit_pct"
          path={['exit', 'take_profit_pct']}
          section={section}
          suffix="%"
        />
      </Group>

      {shadowed.length > 0 && (
        <Alert color="orange" icon={<IconAlertTriangle size={16} />} variant="light">
          The stop priority chain is {stops.join(' › ')}, and only the highest one set is active.{' '}
          {shadowed.join(' and ')} {shadowed.length === 1 ? 'is' : 'are'} configured and will never
          fire. This does not block a run — the strategy is valid, it simply does not do what the
          extra stops suggest.
        </Alert>
      )}

      <Group align="flex-start" gap="lg">
        <NumberField
          control={
            <OptimizeControl
              entryPath={['exit']}
              onChange={section.onChange}
              parameterKey="min_holding_days"
              searchable={search.get('exit.min_holding_days')}
              sectionPinned={exitPinned}
              value={section.value}
              yaml={section.yaml}
            />
          }
          term="min_holding_days"
          label="min_holding_days"
          path={['exit', 'min_holding_days']}
          section={section}
          suffix="d"
        />
        <NumberField
          control={
            <OptimizeControl
              entryPath={['exit']}
              onChange={section.onChange}
              parameterKey="max_holding_days"
              searchable={search.get('exit.max_holding_days')}
              sectionPinned={exitPinned}
              value={section.value}
              yaml={section.yaml}
            />
          }
          term="max_holding_days"
          label="max_holding_days"
          path={['exit', 'max_holding_days']}
          section={section}
          suffix="d"
        />
      </Group>

      <Alert color="gray" icon={<IconInfoCircle size={16} />} variant="light">
        Stops always fire, including inside <Code>min_holding_days</Code>. That window suppresses
        signal exits and time exits only — a stop disabled for N days would not be a stop.
      </Alert>
    </Stack>
  )
}

/** Position sizing is optional, and its absence means something specific. */
function SizingSection(section: SectionProps) {
  const type = stringAt(section.value, ['position_sizing', 'type'])
  const value = numberAt(section.value, ['position_sizing', 'value'])

  return (
    <Stack gap="sm">
      <Group gap={4} wrap="nowrap">
        <Text fw={600}>Position sizing</Text>
        <Explain term="position_sizing" />
      </Group>
      <Card padding="sm" withBorder>
        <Group align="flex-start" gap="lg">
          <Field term="position_sizing" label="Type" path="position_sizing.type">
            <Select
              aria-label="Position sizing type"
              clearable
              data={SIZING_TYPES}
              onChange={(next) => {
                section.onChange(
                  next === null
                    ? removeAt(section.yaml, ['position_sizing'])
                    : putAt(
                        putAt(section.yaml, ['position_sizing', 'type'], next),
                        ['position_sizing', 'value'],
                        value ?? (next === 'fixed_pct' ? 100 : 1),
                      ),
                )
              }}
              placeholder="all available cash"
              size="sm"
              value={type}
            />
          </Field>
          {type !== null && (
            <NumberField
              hint={
                type === 'fixed_pct'
                  ? 'A percentage of available cash, not of total equity — the two differ once a position is open.'
                  : type === 'fixed_cash'
                    ? 'A fixed dollar amount per trade.'
                    : 'A fixed share count per trade.'
              }
              label="Value"
              path={['position_sizing', 'value']}
              section={section}
              suffix={type === 'fixed_pct' ? '%' : type === 'fixed_cash' ? '$' : 'sh'}
            />
          )}
        </Group>
        {type === null && (
          <Text c="dimmed" mt="xs" size="xs">
            Omitted, which means every trade uses 100% of available cash.
          </Text>
        )}
      </Card>
    </Stack>
  )
}
