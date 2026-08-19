import { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Checkbox,
  Group,
  Modal,
  NumberInput,
  Select,
  Stack,
  Text,
} from '@mantine/core'
import { IconAlertTriangle } from '@tabler/icons-react'
import { notifications } from '@mantine/notifications'
import { useMeta } from '../../api/metaContext'
import { useSearchableCount } from '../../api/config'
import { useStrategyContext } from '../strategy/context'
import { ProblemAlert } from '../../components/ProblemAlert'
import { duration, runKindLabel } from '../../lib/format'
import { descriptionOf, explanationOf, type TermKey } from '../../lib/glossary'
import { useLaunchRun } from './queries'
import type { RunKind } from '../../api/types'

/**
 * The launch dialog for all four run kinds.
 *
 * Defaults come from `/meta` rather than being written here, so the dialog pre-fills with
 * what the engine would have used anyway.
 *
 * Evolution's branch differs from the other three in one way worth stating: its budget field
 * is not only a time control. `population * generations` is the trial count the deflated
 * Sharpe divides the result by, so asking for a bigger search raises the bar that search's own
 * answer has to clear. A form that showed only the minutes would invite the user to turn it up.
 */

/** Each objective the engine offers, mapped to the glossary entry that explains it. */
const OBJECTIVE_TERM: Record<string, TermKey> = {
  calmar: 'objective_calmar',
  sortino: 'objective_sortino',
  sharpe: 'objective_sharpe',
  legacy_pnl: 'objective_legacy_pnl',
}

function objectiveExplanation(objective: string): string | undefined {
  const term = OBJECTIVE_TERM[objective]
  return term && explanationOf(term)
}

/** A rough guide, not a promise: the search cost varies with the strategy. */
const SECONDS_PER_EPOCH = 4

/** One simulation per segment per genome, and a simulation is far cheaper than an epoch. */
const SECONDS_PER_GENOME = 0.12

function estimate(
  kind: RunKind,
  epochs: number,
  folds: number,
  budget: number,
  segments: number,
): string | null {
  if (kind === 'backtest') return null
  if (kind === 'evolve') return duration(budget * segments * SECONDS_PER_GENOME)
  const searches = kind === 'walk_forward' ? folds : 1
  return duration(searches * epochs * SECONDS_PER_EPOCH)
}

/**
 * Trading days a chassis has to span before evolution can divide it.
 *
 * The engine refuses the division itself and says so precisely; this reproduces the arithmetic
 * only closely enough to stop the user queueing a run that cannot start. Warm-up comes from
 * `/meta`, so the number is the engine's rather than one written here — and the segments and
 * holdout are the ones this form is currently asking for, not the defaults.
 *
 * **Daily bars only.** One daily bar is one trading day, which is the entire reason this
 * comparison is meaningful; on any intraday interval a trading day is a *session* worth of
 * bars, and how many that is belongs to the exchange. Seven weeks of 30-minute Xetra bars is
 * about 37 trading days and 629 bars — compared against a floor counted in bars it looks like
 * a twentieth of what it is, and the form blocks a run the engine would have accepted. See
 * `isDaily` at the call site.
 */
function requiredBars(warmup: number, segments: number, holdout: number): number {
  const MIN_SEGMENT_BARS = 60
  const evolutionBars = warmup + segments * MIN_SEGMENT_BARS
  return Math.ceil(Math.max(evolutionBars / (1 - holdout), MIN_SEGMENT_BARS / holdout))
}

/** Trading days between two ISO dates, at roughly 252 a year. */
function tradingDaysBetween(start: string | undefined, end: string | undefined): number | null {
  if (!start || !end) return null
  const days = (Date.parse(end) - Date.parse(start)) / 86_400_000
  return Number.isFinite(days) ? Math.floor((days * 252) / 365) : null
}

export function LaunchRunModal({
  opened,
  onClose,
  kind,
}: {
  opened: boolean
  onClose: () => void
  kind: RunKind
}) {
  const strategy = useStrategyContext()
  const { meta, defaultsFor } = useMeta()
  const launch = useLaunchRun()
  const defaults = defaultsFor(kind)
  // Zero when every numeric field is pinned; the engine refuses to search nothing. `null`
  // while the answer is still in flight, which is not the same fact and must not read as one.
  const searchableParameters = useSearchableCount(opened ? strategy.head.config : undefined)

  const [objective, setObjective] = useState(defaults.objective)
  const [epochs, setEpochs] = useState(defaults.epochs)
  const [folds, setFolds] = useState(defaults.folds ?? 6)
  const [scheme, setScheme] = useState(defaults.scheme ?? 'anchored')
  const [cache, setCache] = useState(defaults.cache ?? true)
  const [minTrades, setMinTrades] = useState(defaults.min_trades ?? 20)
  const [minTradesPerYear, setMinTradesPerYear] = useState(defaults.min_trades_per_year ?? 4)
  const [population, setPopulation] = useState(defaults.population ?? 40)
  const [generations, setGenerations] = useState(defaults.generations ?? 25)
  const [segments, setSegments] = useState(defaults.segments ?? 4)
  const [holdout, setHoldout] = useState(defaults.holdout_fraction ?? 0.2)

  useEffect(() => {
    if (!opened) return
    setObjective(defaults.objective)
    setEpochs(defaults.epochs)
    setFolds(defaults.folds ?? 6)
    setScheme(defaults.scheme ?? 'anchored')
    setCache(defaults.cache ?? true)
    setMinTrades(defaults.min_trades ?? 20)
    setMinTradesPerYear(defaults.min_trades_per_year ?? 4)
    setPopulation(defaults.population ?? 40)
    setGenerations(defaults.generations ?? 25)
    setSegments(defaults.segments ?? 4)
    setHoldout(defaults.holdout_fraction ?? 0.2)
    launch.reset()
    // `launch` is a stable mutation object; re-running this on its identity would reset the
    // form mid-edit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened, kind])

  const searches = kind === 'walk_forward'
  const evolves = kind === 'evolve'
  const optimizes = kind !== 'backtest'
  // Evolution composes its own indicators, so a fully pinned config is no obstacle to it —
  // there is nothing of the base strategy for it to be blocked by.
  const nothingToSearch = optimizes && !evolves && searchableParameters === 0

  const budget = population * generations
  const universe = (
    strategy.head.config as {
      universe?: { start_date?: string; end_date?: string; interval?: string }
    }
  ).universe
  // Absent means `1d`: the field was added after these configs existed and the engine reads a
  // missing interval the same way.
  const isDaily = (universe?.interval ?? '1d') === '1d'
  const availableBars = tradingDaysBetween(universe?.start_date, universe?.end_date)
  const needed = requiredBars(meta.evolution_warmup_bars, segments, holdout)
  // Intraday is left to the server, which knows the bars it actually fetched and refuses with
  // the exact arithmetic. A client guess here has no way to know how many bars a session holds
  // — 17 on Xetra at 30m, 13 in New York — and guessing wrong disables the button on a run
  // that would have worked.
  const tooShort = evolves && isDaily && availableBars !== null && availableBars < needed

  const submit = () => {
    const params: Record<string, unknown> =
      kind === 'backtest'
        ? {}
        : kind === 'evolve'
          ? {
              objective,
              population,
              generations,
              segments,
              holdout_fraction: holdout,
              cache,
              min_trades: minTrades,
              min_trades_per_year: minTradesPerYear,
            }
          : kind === 'optimize'
            ? {
                objective,
                epochs,
                cache,
                min_trades: minTrades,
                min_trades_per_year: minTradesPerYear,
              }
            : {
                objective,
                epochs,
                folds,
                scheme,
                min_trades: minTrades,
                min_trades_per_year: minTradesPerYear,
              }

    launch.mutate(
      { strategyId: strategy.id, kind, params },
      {
        onSuccess: (run) => {
          notifications.show({
            color: 'blue',
            message: `${runKindLabel(kind)} #${run.number} is queued against v${run.version}.`,
            title: 'Queued',
          })
          onClose()
        },
      },
    )
  }

  const estimated = estimate(kind, epochs, folds, budget, segments)

  return (
    <Modal onClose={onClose} opened={opened} title={`Run ${runKindLabel(kind).toLowerCase()}`}>
      <Stack gap="md">
        {launch.error && <ProblemAlert error={launch.error} />}

        {nothingToSearch && (
          <Alert color="orange" icon={<IconAlertTriangle size={18} />} variant="light">
            Every numeric field in this configuration is pinned, so there is nothing for the search
            to move. The engine refuses a search with no parameters rather than burning time
            re-scoring one configuration.
          </Alert>
        )}

        {kind === 'backtest' && (
          <Text size="sm">
            Runs the configuration exactly as written, against buy-and-hold over the same window. No
            search, no parameters.
          </Text>
        )}

        {evolves && (
          <>
            <Alert color="blue" variant="light">
              <Text size="sm">{descriptionOf('chassis')}</Text>
            </Alert>

            {tooShort && (
              <Alert color="orange" icon={<IconAlertTriangle size={18} />} variant="light">
                This strategy&rsquo;s date range is about {availableBars} trading days, and this
                division needs roughly {needed}. The block library reaches back{' '}
                {meta.evolution_warmup_bars} bars before the first scored one, and each of the{' '}
                {segments} segments plus the holdout needs enough bars to mean anything. Widen the
                dates on the Config tab, or ask for fewer segments.
              </Alert>
            )}
          </>
        )}

        {optimizes && (
          <>
            <Select
              allowDeselect={false}
              data={meta.objectives.map((value) => ({
                value,
                label: value === 'legacy_pnl' ? `${value} — not recommended` : value,
              }))}
              description={objectiveExplanation(objective)}
              label="Objective"
              onChange={(value) => value && setObjective(value)}
              value={objective}
            />
            {!evolves && (
              <NumberInput
                description={descriptionOf('epochs')}
                label="Epochs"
                max={200}
                min={1}
                onChange={(value) => setEpochs(Number(value) || 1)}
                value={epochs}
              />
            )}
            <NumberInput
              description={descriptionOf('min_trades')}
              label="Minimum trades"
              max={1000}
              min={0}
              onChange={(value) => setMinTrades(Number(value) || 0)}
              value={minTrades}
            />
            <NumberInput
              decimalScale={1}
              description={descriptionOf('min_trades_per_year')}
              label="Minimum trades per year"
              max={500}
              min={0}
              onChange={(value) => setMinTradesPerYear(Number(value) || 0)}
              step={1}
              value={minTradesPerYear}
            />
            <Text c="dimmed" size="xs">
              The search rejects any combination below the higher of these two, measured on the
              training window. Above the floor it has no preference for trading more, so this is the
              setting that rules out a result built on a handful of trades.
            </Text>
          </>
        )}

        {evolves && (
          <>
            <NumberInput
              description={descriptionOf('population')}
              label="Population"
              max={5000}
              min={2}
              onChange={(value) => setPopulation(Number(value) || 2)}
              value={population}
            />
            <NumberInput
              description={descriptionOf('generations')}
              label="Generations"
              max={100}
              min={1}
              onChange={(value) => setGenerations(Number(value) || 1)}
              value={generations}
            />

            {/* The point of this box. Every other kind's budget control trades time for
                thoroughness; here it also raises the bar the answer has to clear, and a user
                who only sees the minutes will reach for a bigger number believing bigger is
                strictly better. */}
            <Alert color="gray" variant="light">
              <Stack gap={4}>
                <Text fw={600} size="sm">
                  Up to {budget.toLocaleString()} strategies will be tried.
                </Text>
                <Text size="sm">
                  That count is not only a time cost. The deflated Sharpe divides the winner&rsquo;s
                  result by how many attempts produced it, because the best of{' '}
                  {budget.toLocaleString()} tries looks good whether or not anything is there. A
                  larger search has to find a better strategy to pass the same check.
                </Text>
              </Stack>
            </Alert>

            <NumberInput
              description={descriptionOf('segments')}
              label="Segments"
              max={12}
              min={4}
              onChange={(value) => setSegments(Number(value) || 4)}
              value={segments}
            />
            <NumberInput
              decimalScale={2}
              description={descriptionOf('holdout')}
              label="Holdout share"
              max={0.5}
              min={0.1}
              onChange={(value) => setHoldout(Number(value) || 0.2)}
              step={0.05}
              value={holdout}
            />
          </>
        )}

        {searches && (
          <>
            <NumberInput
              description={descriptionOf('folds')}
              label="Folds"
              max={20}
              min={2}
              onChange={(value) => setFolds(Number(value) || 2)}
              value={folds}
            />
            <Select
              allowDeselect={false}
              data={meta.fold_schemes}
              description={`${explanationOf('training_window')} ${
                scheme === 'anchored'
                  ? 'This one grows from a fixed start.'
                  : 'This one is a fixed length that slides forward.'
              }`}
              label="Training window"
              onChange={(value) => value && setScheme(value)}
              value={scheme}
            />
          </>
        )}

        {(kind === 'optimize' || evolves) && (
          <Checkbox
            checked={cache}
            description={explanationOf('cache_prices')}
            label="Cache downloaded price history"
            onChange={(event) => setCache(event.currentTarget.checked)}
          />
        )}

        {estimated && (
          <Text c="dimmed" size="xs">
            Roughly {estimated}, very approximately —{' '}
            {evolves
              ? `${budget.toLocaleString()} strategies across ${segments} segments`
              : `${searches ? `${folds} searches of ` : ''}${epochs} generations`}
            . The real cost depends on the strategy.
          </Text>
        )}

        <Group justify="flex-end">
          <Button onClick={onClose} variant="default">
            Cancel
          </Button>
          <Button
            disabled={nothingToSearch || tooShort}
            loading={launch.isPending}
            onClick={submit}
          >
            {evolves ? 'Compose' : 'Run'}
          </Button>
        </Group>
      </Stack>
    </Modal>
  )
}
