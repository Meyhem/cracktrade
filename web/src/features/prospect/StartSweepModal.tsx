import { useState } from 'react'
import {
  Alert,
  Button,
  Chip,
  Group,
  Modal,
  NumberInput,
  Select,
  Stack,
  Text,
  TextInput,
} from '@mantine/core'
import { IconInfoCircle } from '@tabler/icons-react'
import { notifications } from '@mantine/notifications'
import { useNavigate } from 'react-router'
import { ProblemAlert } from '../../components/ProblemAlert'
import { useStartSweep } from './queries'

/**
 * Starting a sweep.
 *
 * The defaults are the screened universe and the small search, and the dialog explains why the
 * budget field is small rather than leaving the user to turn it up. `population * generations`
 * is the trial count the deflated Sharpe divides by, so a bigger search raises the bar its own
 * answer has to clear — and a sweep runs that search thousands of times, so the waste compounds
 * in a way one run's does not.
 *
 * The universe field is deliberately a free list rather than a picker over every symbol. Only
 * tickers with a defined family can be swept, because transfer is what does the rejecting, and
 * the server refuses the rest by name rather than failing hours later.
 */
const INTERVALS = [
  { value: '1h', label: '1h — about 2 years of history' },
  { value: '1d', label: '1d — 8 years of history' },
  { value: '30m', label: '30m — only ~58 days of history' },
  { value: '15m', label: '15m — only ~58 days of history' },
]

export function StartSweepModal({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const navigate = useNavigate()
  const start = useStartSweep()

  const [name, setName] = useState('')
  const [tickers, setTickers] = useState('')
  const [interval, setInterval] = useState('1h')
  const [population, setPopulation] = useState(30)
  const [generations, setGenerations] = useState(10)

  const trials = population * generations
  const universe = tickers
    .split(/[\s,]+/)
    .map((entry) => entry.trim().toUpperCase())
    .filter(Boolean)

  const submit = () => {
    start.mutate(
      {
        name: name.trim(),
        ...(universe.length > 0 ? { universe } : {}),
        params: { interval, population, generations },
      },
      {
        onSuccess: (session) => {
          notifications.show({
            message: `${session.name} is searching ${session.universe.length} tickers.`,
            title: 'Sweep started',
          })
          onClose()
          void navigate(`/prospect/${session.id}`)
        },
      },
    )
  }

  return (
    <Modal onClose={onClose} opened={opened} title="Start a sweep" size="lg">
      <Stack gap="md">
        {start.error && <ProblemAlert error={start.error} />}

        <TextInput
          data-autofocus
          label="Name"
          onChange={(event) => setName(event.currentTarget.value)}
          placeholder="overnight semis"
          required
          value={name}
        />

        <TextInput
          description="Leave empty for the twenty day-trading instruments that were screened for this. Only tickers with a defined family can be swept — the server will say which are not."
          label="Tickers"
          onChange={(event) => setTickers(event.currentTarget.value)}
          placeholder="AMD NVDA SOXL MU"
          value={tickers}
        />
        {universe.length > 0 && (
          <Group gap="xs">
            {universe.map((ticker) => (
              <Chip checked={false} key={ticker} size="xs" variant="light">
                {ticker}
              </Chip>
            ))}
          </Group>
        )}

        <Select
          data={INTERVALS}
          description="Part of the question, never searched over. The provider serves far less intraday history than daily."
          label="Bar interval"
          onChange={(value) => setInterval(value ?? '1h')}
          value={interval}
        />

        <Group grow>
          <NumberInput
            label="Population"
            min={2}
            onChange={(value) => setPopulation(Number(value) || 2)}
            value={population}
          />
          <NumberInput
            label="Generations"
            min={1}
            onChange={(value) => setGenerations(Number(value) || 1)}
            value={generations}
          />
        </Group>

        <Alert color="blue" icon={<IconInfoCircle size={16} />} variant="light">
          <Text size="sm">
            {trials.toLocaleString()} configurations per ticker. The default is small on purpose:
            scaling this budget forty-fold was measured to buy no improvement in or out of sample,
            while the significance bar a result must clear grows with the number of configurations
            tried. A sweep is better spent on the next ticker than on more generations of this one.
          </Text>
        </Alert>

        <Group justify="flex-end">
          <Button onClick={onClose} variant="default">
            Cancel
          </Button>
          <Button disabled={!name.trim()} loading={start.isPending} onClick={submit}>
            Start
          </Button>
        </Group>
      </Stack>
    </Modal>
  )
}
