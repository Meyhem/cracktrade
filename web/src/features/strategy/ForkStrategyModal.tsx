import { useEffect, useState } from 'react'
import { Button, Group, Modal, NumberInput, Stack, Text, TextInput } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useNavigate } from 'react-router'
import { descriptionOf, explanationOf } from '../../lib/glossary'
import { ProblemAlert } from '../../components/ProblemAlert'
import { useForkStrategy } from '../strategies/queries'
import { useStrategyContext } from './context'

/**
 * Taking a strategy in a different direction.
 *
 * A fork starts a fresh version history at v1 rather than inheriting the parent's, but records
 * which version of the parent it came from — so the child's history has an origin point and the
 * parent's history is not polluted by an experiment.
 *
 * Any version can be forked, not only the head. "Take v3 in a different direction" is a natural
 * thing to want after a restore-or-branch decision, and without it the user restores first just
 * to fork, appending a version to the parent to do it.
 */
export function ForkStrategyModal({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const strategy = useStrategyContext()
  const navigate = useNavigate()
  const fork = useForkStrategy()
  const [name, setName] = useState(`${strategy.name} copy`)
  const [version, setVersion] = useState(strategy.head.version)

  useEffect(() => {
    if (opened) {
      setName(`${strategy.name} copy`)
      setVersion(strategy.head.version)
      fork.reset()
    }
    // `fork` is a stable mutation object; re-running on its identity would clear the field.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened, strategy.name, strategy.head.version])

  const submit = () => {
    fork.mutate(
      { strategyId: strategy.id, name: name.trim(), version },
      {
        onSuccess: (created) => {
          notifications.show({
            color: 'blue',
            message: `${name.trim()} starts at v1, copied from ${strategy.name} v${version}.`,
            title: 'Forked',
          })
          onClose()
          void navigate(`/strategies/${created.strategy.id}/config`)
        },
      },
    )
  }

  return (
    <Modal onClose={onClose} opened={opened} title="Fork strategy">
      <Stack gap="md">
        {fork.error && <ProblemAlert error={fork.error} />}

        <Text c="dimmed" size="sm">
          {explanationOf('fork')}
        </Text>

        <TextInput
          description={descriptionOf('strategy_name')}
          label="Name"
          onChange={(event) => setName(event.currentTarget.value)}
          value={name}
        />

        <NumberInput
          description={`${descriptionOf('version')} Any version, not only the head — this strategy has ${strategy.counts.versions}.`}
          label="Fork from version"
          max={strategy.head.version}
          min={1}
          onChange={(value) => setVersion(Number(value) || 1)}
          value={version}
        />

        <Text c="dimmed" size="sm">
          The copy is independently editable and starts its own version history at v1. It carries no
          runs — comparing two entry conditions means two strategies and two runs, which is what
          this is for.
        </Text>

        <Group justify="flex-end">
          <Button onClick={onClose} variant="default">
            Cancel
          </Button>
          <Button disabled={name.trim().length === 0} loading={fork.isPending} onClick={submit}>
            Fork
          </Button>
        </Group>
      </Stack>
    </Modal>
  )
}
