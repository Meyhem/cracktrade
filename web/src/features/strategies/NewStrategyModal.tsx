import { Button, Group, Modal, Radio, Select, Stack, Text, TextInput } from '@mantine/core'
import { useForm } from '@mantine/form'
import { useNavigate } from 'react-router'
import { notifications } from '@mantine/notifications'
import type { BarInterval, IntervalOption } from '../../api/types'
import { useMeta } from '../../api/metaContext'
import { Explain } from '../../components/Explain'
import { descriptionOf } from '../../lib/glossary'
import { INTERVAL_LABELS, defaultRange, intervalHint, rangeTooWide } from '../../lib/intervals'
import { ProblemAlert } from '../../components/ProblemAlert'
import { useCreateStrategy } from './queries'

/**
 * Create a strategy.
 *
 * The seed choice matters more than it looks: "minimal" produces a configuration that
 * already validates and can be backtested immediately, which is the only way a new user
 * sees the loop work before they have learned the schema. "Empty" is for someone who knows
 * what they are writing.
 *
 * The bar interval is decided here and effectively only here. It is editable afterwards, but
 * changing it makes the existing date range meaningless — 15m and 30m data reaches back about
 * 55 days — so the choice belongs where the dates are being chosen anyway.
 */
export function NewStrategyModal({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const navigate = useNavigate()
  const create = useCreateStrategy()
  const { meta } = useMeta()

  const optionFor = (value: string): IntervalOption | undefined =>
    meta.intervals.find((option) => option.value === value)

  const form = useForm({
    initialValues: {
      name: '',
      ticker: '',
      ...defaultRange({ intraday: false, max_lookback_days: null }),
      interval: '1d' as BarInterval,
      seed: 'minimal' as 'minimal' | 'empty',
    },
    validate: {
      name: (value) => (value.trim() ? null : 'A name is required.'),
      ticker: (value) => (value.trim() ? null : 'One ticker is required — no portfolios.'),
      start_date: (value, values) => {
        if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return 'Use YYYY-MM-DD.'
        const option = optionFor(values.interval)
        return option ? rangeTooWide(option, value, values.end_date) : null
      },
      end_date: (value, values) => {
        if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return 'Use YYYY-MM-DD.'
        return value > values.start_date ? null : 'The end date must be after the start date.'
      },
    },
  })

  // Changing the interval re-defaults the dates. Anything the user typed under the old interval
  // was chosen against a different reach, and silently keeping a range the new interval cannot
  // serve would turn a choice into an error message.
  const chooseInterval = (value: string | null) => {
    const option = value === null ? undefined : optionFor(value)
    if (!option) return
    form.setValues({ interval: option.value, ...defaultRange(option) })
  }

  const close = () => {
    form.reset()
    create.reset()
    onClose()
  }

  const currentInterval = optionFor(form.values.interval)

  const submit = form.onSubmit((values) => {
    create.mutate(
      {
        ...values,
        ticker: values.ticker.trim().toUpperCase(),
        name: values.name.trim(),
        interval: values.interval,
      },
      {
        onSuccess: (created) => {
          if (created.warnings.length > 0) {
            notifications.show({
              color: 'orange',
              message: created.warnings.map((warning) => warning.message).join(' · '),
              title: 'Created, with warnings',
            })
          }
          close()
          void navigate(`/strategies/${created.strategy.id}`)
        },
      },
    )
  })

  return (
    <Modal onClose={close} opened={opened} title="New strategy">
      <form onSubmit={submit}>
        <Stack gap="md">
          {create.error && <ProblemAlert error={create.error} />}

          <TextInput
            data-autofocus
            description={descriptionOf('strategy_name')}
            label="Name"
            placeholder="rsi_pullback"
            {...form.getInputProps('name')}
          />
          <TextInput
            description={descriptionOf('ticker')}
            label="Ticker"
            placeholder="NVDA"
            {...form.getInputProps('ticker')}
          />
          <Select
            allowDeselect={false}
            data={meta.intervals.map((option) => ({
              label: INTERVAL_LABELS[option.value],
              value: option.value,
            }))}
            description={
              currentInterval ? intervalHint(currentInterval) : descriptionOf('bar_interval')
            }
            label={
              <>
                Bar interval{' '}
                {currentInterval?.intraday === true && <Explain term="session_close" />}
              </>
            }
            onChange={chooseInterval}
            value={form.values.interval}
          />

          <Group grow>
            <TextInput
              description={descriptionOf('start_date')}
              label="Start date"
              placeholder="2023-01-01"
              {...form.getInputProps('start_date')}
            />
            <TextInput
              description={descriptionOf('end_date')}
              label="End date"
              placeholder="2025-12-31"
              {...form.getInputProps('end_date')}
            />
          </Group>

          <Radio.Group
            description={descriptionOf('configuration')}
            label="Starting configuration"
            {...form.getInputProps('seed')}
          >
            <Stack gap={6} mt={6}>
              <Radio
                description="A working strategy you can back test straight away, then edit."
                label="Minimal"
                value="minimal"
              />
              <Radio
                description="Just the required scaffolding. You write the indicators and rules."
                label="Empty"
                value="empty"
              />
            </Stack>
          </Radio.Group>

          <Text c="dimmed" size="xs">
            You chose this ticker knowing its history, and no engine corrects for that.
          </Text>

          <Group justify="flex-end">
            <Button onClick={close} variant="default">
              Cancel
            </Button>
            <Button loading={create.isPending} type="submit">
              Create
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  )
}
