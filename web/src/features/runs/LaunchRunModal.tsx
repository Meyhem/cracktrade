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
import { ProblemAlert } from '../../components/ProblemAlert'
import { duration, runKindLabel } from '../../lib/format'
import { useLaunchRun } from './queries'
import type { RunKind } from '../../api/types'

/**
 * The launch dialog for all three run kinds.
 *
 * Defaults come from `/meta` rather than being written here, so the dialog pre-fills with
 * what the engine would have used anyway.
 */

const OBJECTIVE_EXPLANATION: Record<string, string> = {
  calmar: 'Return against the worst drawdown. The default, and the most conservative.',
  sortino: 'Return against downside volatility only — upside swings are not penalised.',
  sharpe: 'Return against total volatility.',
  legacy_pnl: 'Raw profit. Kept for comparison with older runs; not recommended.',
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
  strategyId,
  /** Empty when every numeric field is pinned; the engine refuses to search nothing. */
  searchableParameters,
}: {
  opened: boolean
  onClose: () => void
  kind: RunKind
  strategyId: string
  searchableParameters: number
}) {
  const { meta, defaultsFor } = useMeta()
  const launch = useLaunchRun()
  const defaults = defaultsFor(kind)

  const [objective, setObjective] = useState(defaults.objective)
  const [epochs, setEpochs] = useState(defaults.epochs)
  const [folds, setFolds] = useState(defaults.folds ?? 6)
  const [scheme, setScheme] = useState(defaults.scheme ?? 'anchored')
  const [cache, setCache] = useState(defaults.cache ?? true)

  useEffect(() => {
    if (!opened) return
    setObjective(defaults.objective)
    setEpochs(defaults.epochs)
    setFolds(defaults.folds ?? 6)
    setScheme(defaults.scheme ?? 'anchored')
    setCache(defaults.cache ?? true)
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
          ? { objective, epochs, cache }
          : { objective, epochs, folds, scheme }

    launch.mutate(
      { strategyId, kind, params },
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
              description={OBJECTIVE_EXPLANATION[objective]}
              label="Objective"
              onChange={(value) => value && setObjective(value)}
              value={objective}
            />
            <NumberInput
              description="Differential-evolution generations. More is a longer, more thorough search."
              label="Epochs"
              max={200}
              min={1}
              onChange={(value) => setEpochs(Number(value) || 1)}
              value={epochs}
            />
          </>
        )}

        {searches && (
          <>
            <NumberInput
              description="Each fold is optimized and evaluated independently."
              label="Folds"
              max={20}
              min={2}
              onChange={(value) => setFolds(Number(value) || 2)}
              value={folds}
            />
            <Select
              allowDeselect={false}
              data={meta.fold_schemes}
              description={
                scheme === 'anchored'
                  ? 'The training window grows from a fixed start.'
                  : 'The training window is a fixed length that slides forward.'
              }
              label="Training window"
              onChange={(value) => value && setScheme(value)}
              value={scheme}
            />
          </>
        )}

        {kind === 'optimize' && (
          <Checkbox
            checked={cache}
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
