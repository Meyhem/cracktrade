import { Alert, Button, Group, Modal, NumberInput, Stack, Text, TextInput } from '@mantine/core'
import { useForm } from '@mantine/form'
import { useNavigate } from 'react-router'
import { notifications } from '@mantine/notifications'
import dayjs from 'dayjs'
import { descriptionOf } from '../../lib/glossary'
import { ProblemAlert } from '../../components/ProblemAlert'
import { useMeta } from '../../api/metaContext'
import { useLaunchRun } from '../runs/queries'
import { useCreateStrategy } from './queries'

/**
 * Pick a ticker; the machine writes the strategy.
 *
 * This exists because the two-step version — create a strategy, then find the Evolve button on
 * it — asks the user to understand the chassis before they have seen what evolution does. The
 * chassis is an implementation fact: evolution needs somewhere to hang a ticker, some dates and
 * a set of costs, and this application already has a type for that. Nothing here hides it (the
 * strategy is real, appears in the list, and can be evolved again), but nothing here makes
 * learning about it a precondition either.
 *
 * The date default is deliberately long. The block library reaches back 200 bars before the
 * first scored one, and four segments plus a holdout have to fit after that, so the three years
 * that suit a hand-written strategy would be refused before the search started.
 */

/** Years of history requested by default. Matches the CLI's own default for the same reason. */
const DEFAULT_YEARS = 12

export function ComposeStrategyModal({
  opened,
  onClose,
}: {
  opened: boolean
  onClose: () => void
}) {
  const navigate = useNavigate()
  const { meta, defaultsFor } = useMeta()
  const create = useCreateStrategy()
  const launch = useLaunchRun()
  const defaults = defaultsFor('evolve')

  const form = useForm({
    initialValues: {
      name: '',
      ticker: '',
      start_date: dayjs().subtract(DEFAULT_YEARS, 'year').format('YYYY-MM-DD'),
      end_date: dayjs().format('YYYY-MM-DD'),
      population: defaults.population ?? 40,
      generations: defaults.generations ?? 25,
    },
    validate: {
      name: (value) => (value.trim() ? null : 'A name is required.'),
      ticker: (value) => (value.trim() ? null : 'One ticker is required — no portfolios.'),
      start_date: (value) => (/^\d{4}-\d{2}-\d{2}$/.test(value) ? null : 'Use YYYY-MM-DD.'),
      end_date: (value, values) => {
        if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return 'Use YYYY-MM-DD.'
        return value > values.start_date ? null : 'The end date must be after the start date.'
      },
    },
  })

  const close = () => {
    form.reset()
    create.reset()
    launch.reset()
    onClose()
  }

  const submit = form.onSubmit((values) => {
    create.mutate(
      {
        name: values.name.trim(),
        ticker: values.ticker.trim().toUpperCase(),
        start_date: values.start_date,
        end_date: values.end_date,
        // The composed conditions replace whatever is here, so the seed is only ever a
        // placeholder. `minimal` rather than `empty` so the chassis is a strategy that can be
        // backtested on its own if evolution turns out not to be what the user wanted.
        seed: 'minimal',
      },
      {
        onSuccess: (created) => {
          launch.mutate(
            {
              strategyId: created.strategy.id,
              kind: 'evolve',
              params: {
                objective: defaults.objective,
                population: values.population,
                generations: values.generations,
                segments: defaults.segments ?? 4,
                holdout_fraction: defaults.holdout_fraction ?? 0.2,
                min_trades: defaults.min_trades ?? 20,
                min_trades_per_year: defaults.min_trades_per_year ?? 4,
                cache: true,
              },
            },
            {
              onSuccess: (run) => {
                notifications.show({
                  color: 'blue',
                  message: `Composing a strategy for ${values.ticker.trim().toUpperCase()}. This takes a few minutes.`,
                  title: 'Queued',
                })
                close()
                void navigate(`/strategies/${created.strategy.id}/runs/${run.id}`)
              },
              // The strategy exists whatever happens to the run, so a failed launch lands the
              // user on it rather than leaving them on a dialog for something half-done.
              onError: () => {
                close()
                void navigate(`/strategies/${created.strategy.id}/evolution`)
              },
            },
          )
        },
      },
    )
  })

  const budget = form.values.population * form.values.generations

  return (
    <Modal onClose={close} opened={opened} title="Compose a strategy" size="lg">
      <form onSubmit={submit}>
        <Stack gap="md">
          {create.error && <ProblemAlert error={create.error} />}
          {launch.error && <ProblemAlert error={launch.error} />}

          <Text size="sm">
            Pick a ticker and the engine writes the strategy: it composes entry and exit conditions
            from a fixed library of blocks, keeps what scored best across four stretches of history,
            and judges the winner once on a final stretch it was never allowed to see.
          </Text>

          <TextInput
            data-autofocus
            description="What to call the chassis this run hangs on. The composed strategy gets its own name when you promote it."
            label="Name"
            placeholder="nvda_composed"
            {...form.getInputProps('name')}
          />
          <TextInput
            description={descriptionOf('ticker')}
            label="Ticker"
            placeholder="NVDA"
            {...form.getInputProps('ticker')}
          />
          <Group grow>
            <TextInput
              description={`At least ${meta.evolution_warmup_bars} trading days go to warm-up before anything is scored, so this wants years rather than months.`}
              label="Start date"
              {...form.getInputProps('start_date')}
            />
            <TextInput
              description={descriptionOf('end_date')}
              label="End date"
              {...form.getInputProps('end_date')}
            />
          </Group>

          <Group grow>
            <NumberInput
              description={descriptionOf('population')}
              label="Population"
              max={200}
              min={2}
              {...form.getInputProps('population')}
            />
            <NumberInput
              description={descriptionOf('generations')}
              label="Generations"
              max={200}
              min={1}
              {...form.getInputProps('generations')}
            />
          </Group>

          <Alert color="gray" variant="light">
            <Text size="sm">
              Up to {budget.toLocaleString()} strategies will be tried, and that count counts
              against the result: the best of {budget.toLocaleString()} attempts looks good whether
              or not there is anything there, so a larger search has to find a better strategy to
              clear the same bar.
            </Text>
          </Alert>

          <Text c="dimmed" size="xs">
            You chose this ticker knowing its history, and no engine corrects for that.
          </Text>

          <Group justify="flex-end">
            <Button onClick={close} variant="default">
              Cancel
            </Button>
            <Button loading={create.isPending || launch.isPending} type="submit">
              Compose
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  )
}
