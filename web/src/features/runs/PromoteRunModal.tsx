import { useEffect, useState } from 'react'
import { Alert, Button, Group, Loader, Modal, Stack, Text, TextInput } from '@mantine/core'
import { IconAlertTriangle } from '@tabler/icons-react'
import { notifications } from '@mantine/notifications'
import { useNavigate } from 'react-router'
import { ProblemAlert } from '../../components/ProblemAlert'
import { ConfigDiff } from './view/ConfigDiff'
import { useConfigDiff } from '../../api/config'
import { usePromoteRun } from './queries'
import { useStrategyContext } from '../strategy/context'
import type { Run, WalkForwardHeadline } from '../../api/types'

/**
 * Adopting a configuration a machine chose.
 *
 * The brief calls this the single most important anti-footgun in the app, and the reason is
 * that promotion is exactly the moment a caveat gets lost: the new strategy looks fresh while
 * its numbers came out of a search nobody validated. Two things guard against that here — the
 * diff, so the user sees precisely which parameters moved before adopting them, and the
 * warning that the source run was not credible, stated before the button rather than after.
 */
export function PromoteRunModal({
  run,
  optimizedYaml,
  defaultName,
  opened,
  onClose,
}: {
  run: Run
  optimizedYaml: string | null
  defaultName: string | null
  opened: boolean
  onClose: () => void
}) {
  const strategy = useStrategyContext()
  const navigate = useNavigate()
  const promote = usePromoteRun()
  const [name, setName] = useState(defaultName ?? '')

  useEffect(() => {
    if (opened) {
      setName(defaultName ?? '')
      promote.reset()
    }
    // `promote` is a stable mutation object; re-running on its identity would clear the field.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened, defaultName])

  const diff = useConfigDiff(
    opened ? { config: strategy.head.config } : null,
    opened && optimizedYaml ? { yaml: optimizedYaml } : null,
  )

  // The headline union carries no discriminant of its own, so the run's kind is the narrowing
  // — the same approach `headline.tsx` takes for the run tables.
  const notCredible =
    run.kind === 'walk_forward' &&
    (run.headline as WalkForwardHeadline | null)?.is_credible === false

  const submit = () => {
    promote.mutate(
      { runId: run.id, name: name.trim() },
      {
        onSuccess: (created) => {
          notifications.show({
            color: 'blue',
            message: `${name.trim()} is now its own strategy, with a backtest already queued against it.`,
            title: 'Promoted',
          })
          onClose()
          void navigate(`/strategies/${created.strategy_id}`)
        },
      },
    )
  }

  return (
    <Modal onClose={onClose} opened={opened} size="lg" title="Promote to strategy">
      <Stack gap="md">
        {promote.error && <ProblemAlert error={promote.error} />}

        {notCredible && (
          <Alert color="orange" icon={<IconAlertTriangle size={18} />} variant="light">
            This run was judged <strong>not credible</strong>. Promoting it is allowed — the
            configuration is still a reasonable place to start from — but the new strategy will
            carry that on its own page until its own walk-forward passes. It does not inherit a
            verdict it did not earn.
          </Alert>
        )}

        <TextInput
          description="A promoted strategy is independently editable and starts its own version history at v1."
          label="Name"
          onChange={(event) => setName(event.currentTarget.value)}
          value={name}
        />

        <Stack gap="xs">
          <Text fw={600} size="sm">
            What you are adopting
          </Text>
          <Text c="dimmed" size="sm">
            {strategy.name} v{strategy.head.version} compared with the configuration this run
            produced.
          </Text>
          {!optimizedYaml ? (
            <Text size="sm">This run recorded no configuration to promote.</Text>
          ) : diff.isPending ? (
            <Group gap="xs">
              <Loader size="xs" />
              <Text c="dimmed" size="sm">
                Comparing…
              </Text>
            </Group>
          ) : diff.error ? (
            <ProblemAlert error={diff.error} />
          ) : (
            <ConfigDiff groups={diff.data.groups} />
          )}
        </Stack>

        <Group justify="flex-end">
          <Button onClick={onClose} variant="default">
            Cancel
          </Button>
          <Button
            disabled={name.trim().length === 0 || !optimizedYaml}
            loading={promote.isPending}
            onClick={submit}
          >
            Promote
          </Button>
        </Group>
      </Stack>
    </Modal>
  )
}
