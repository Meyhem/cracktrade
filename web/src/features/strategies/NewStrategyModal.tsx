import { Button, Group, Modal, Radio, Stack, Text, TextInput } from '@mantine/core'
import { useForm } from '@mantine/form'
import { useNavigate } from 'react-router'
import { notifications } from '@mantine/notifications'
import dayjs from 'dayjs'
import { ProblemAlert } from '../../components/ProblemAlert'
import { useCreateStrategy } from './queries'

/**
 * Create a strategy.
 *
 * The seed choice matters more than it looks: "minimal" produces a configuration that
 * already validates and can be backtested immediately, which is the only way a new user
 * sees the loop work before they have learned the schema. "Empty" is for someone who knows
 * what they are writing.
 */
export function NewStrategyModal({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const navigate = useNavigate()
  const create = useCreateStrategy()

  const form = useForm({
    initialValues: {
      name: '',
      ticker: '',
      start_date: dayjs().subtract(3, 'year').format('YYYY-MM-DD'),
      end_date: dayjs().format('YYYY-MM-DD'),
      seed: 'minimal' as 'minimal' | 'empty',
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
    onClose()
  }

  const submit = form.onSubmit((values) => {
    create.mutate(
      { ...values, ticker: values.ticker.trim().toUpperCase(), name: values.name.trim() },
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
            label="Name"
            placeholder="rsi_pullback"
            {...form.getInputProps('name')}
          />
          <TextInput
            description="One symbol. This engine does not do portfolios or cross-sectional strategies."
            label="Ticker"
            placeholder="NVDA"
            {...form.getInputProps('ticker')}
          />
          <Group grow>
            <TextInput
              label="Start date"
              placeholder="2023-01-01"
              {...form.getInputProps('start_date')}
            />
            <TextInput
              label="End date"
              placeholder="2025-12-31"
              {...form.getInputProps('end_date')}
            />
          </Group>

          <Radio.Group label="Starting configuration" {...form.getInputProps('seed')}>
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
