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
 * The launch dialog for all three run kinds.
 *
 * Defaults come from `/meta` rather than being written here, so the dialog pre-fills with
 * what the engine would have used anyway.
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

function estimate(kind: RunKind, epochs: number, folds: number): string | null {
  if (kind === 'backtest') return null
  const searches = kind === 'walk_forward' ? folds : 1
  return duration(searches * epochs * SECONDS_PER_EPOCH)
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

  useEffect(() => {
    if (!opened) return
    setObjective(defaults.objective)
    setEpochs(defaults.epochs)
    setFolds(defaults.folds ?? 6)
    setScheme(defaults.scheme ?? 'anchored')
    setCache(defaults.cache ?? true)
    setMinTrades(defaults.min_trades ?? 20)
    setMinTradesPerYear(defaults.min_trades_per_year ?? 4)
    launch.reset()
    // `launch` is a stable mutation object; re-running this on its identity would reset the
    // form mid-edit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened, kind])

  const searches = kind === 'walk_forward'
  const optimizes = kind !== 'backtest'
  const nothingToSearch = optimizes && searchableParameters === 0

  const submit = () => {
    const params: Record<string, unknown> =
      kind === 'backtest'
        ? {}
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

  const estimated = estimate(kind, epochs, folds)

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
            <NumberInput
              description={descriptionOf('epochs')}
              label="Epochs"
              max={200}
              min={1}
              onChange={(value) => setEpochs(Number(value) || 1)}
              value={epochs}
            />
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

        {kind === 'optimize' && (
          <Checkbox
            checked={cache}
            description={explanationOf('cache_prices')}
            label="Cache downloaded price history"
            onChange={(event) => setCache(event.currentTarget.checked)}
          />
        )}

        {estimated && (
          <Text c="dimmed" size="xs">
            Roughly {estimated}, very approximately — {searches ? `${folds} searches of ` : ''}
            {epochs} generations. The real cost depends on the strategy.
          </Text>
        )}

        <Group justify="flex-end">
          <Button onClick={onClose} variant="default">
            Cancel
          </Button>
          <Button disabled={nothingToSearch} loading={launch.isPending} onClick={submit}>
            Run
          </Button>
        </Group>
      </Stack>
    </Modal>
  )
}
