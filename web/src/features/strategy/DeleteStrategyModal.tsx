import { useEffect, useState } from 'react'
import { Alert, Button, Group, List, Modal, Stack, Text, TextInput } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { IconAlertTriangle } from '@tabler/icons-react'
import { useNavigate } from 'react-router'
import { ProblemAlert } from '../../components/ProblemAlert'
import { useDeleteStrategy } from '../strategies/queries'
import { useStrategyContext } from './context'

/**
 * Deleting a strategy, permanently.
 *
 * The only destructive action in the application, and the only exception to the append-only
 * guarantee everything else is built on (spec §14.8). Everywhere else, the wording exists to
 * reassure — a restore deletes nothing, a fork leaves the parent alone. Here it exists to do
 * the opposite, and the dialog is deliberately the least convenient one in the app:
 *
 * - it lists what will be destroyed, counted, **before** anything is asked of the user. "3
 *   versions and 8 runs" is information a user may not have — a strategy accumulates runs
 *   quietly — and it is the number most likely to change their mind;
 * - it asks for the name to be typed. A confirm button is muscle memory by the second time,
 *   and this is an action there is no second time for. Typing the name also means the dialog
 *   cannot be dismissed correctly by someone who opened it on the wrong strategy, which is the
 *   actual failure mode: not "meant not to delete" but "meant to delete the other one";
 * - it says the word "permanently" rather than "cannot be undone", which reads as boilerplate.
 *
 * The two server-side refusals (a run still in flight, a fork or promotion descending from it)
 * are not pre-checked here. The client cannot know either without racing — a run can be
 * launched from another tab between render and click — so the server decides and its refusal
 * is rendered as-is, naming what is in the way.
 */
export function DeleteStrategyModal({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const strategy = useStrategyContext()
  const navigate = useNavigate()
  const remove = useDeleteStrategy()
  const [typed, setTyped] = useState('')

  useEffect(() => {
    if (opened) {
      setTyped('')
      remove.reset()
    }
    // `remove` is a stable mutation object; re-running on its identity would clear the field
    // mid-typing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened])

  // `counts` is an open record on the wire, so every read is `number | undefined` under
  // `noUncheckedIndexedAccess`. Defaulting to 0 rather than asserting: a missing count is a
  // server that sent less than expected, and rendering "0 runs" understates what is about to
  // be destroyed, which is the wrong direction to be wrong in -- so the copy below leans on
  // the categories rather than only on this total.
  const runs =
    (strategy.counts.backtest ?? 0) +
    (strategy.counts.optimize ?? 0) +
    (strategy.counts.walk_forward ?? 0)
  const confirmed = typed.trim() === strategy.name

  const submit = () => {
    remove.mutate(strategy.id, {
      onSuccess: (deleted) => {
        notifications.show({
          color: 'red',
          message: `${deleted.name} is gone, with ${plural(deleted.versions, 'version')} and ${plural(deleted.runs, 'run')}.`,
          title: 'Deleted',
        })
        onClose()
        void navigate('/strategies')
      },
    })
  }

  return (
    <Modal onClose={onClose} opened={opened} title={`Delete ${strategy.name}`}>
      <Stack gap="md">
        {remove.error && <ProblemAlert error={remove.error} />}

        <Alert color="red" icon={<IconAlertTriangle size={18} />} variant="light">
          <Stack gap={4}>
            <Text size="sm">
              This deletes <strong>{strategy.name}</strong> permanently, along with everything
              recorded about it:
            </Text>
            <List size="sm" withPadding>
              <List.Item>
                {plural(strategy.counts.versions ?? 0, 'configuration version')}, including every
                earlier one
              </List.Item>
              <List.Item>
                {plural(runs, 'run')} and every number they produced — backtests, optimizations and
                walk-forwards alike
              </List.Item>
              <List.Item>the charts captured for those runs</List.Item>
            </List>
          </Stack>
        </Alert>

        <Text c="dimmed" size="sm">
          Nothing else in the app is affected: forks of this strategy are independent and are not
          touched. If one exists, this deletion will be refused rather than orphaning it — delete
          the fork first, or keep it.
        </Text>

        <TextInput
          data-autofocus
          description="Typed out, so this cannot be done to the wrong strategy by reflex."
          label={`Type ${strategy.name} to confirm`}
          onChange={(event) => setTyped(event.currentTarget.value)}
          placeholder={strategy.name}
          value={typed}
        />

        <Group justify="flex-end">
          <Button onClick={onClose} variant="default">
            Cancel
          </Button>
          <Button color="red" disabled={!confirmed} loading={remove.isPending} onClick={submit}>
            Delete permanently
          </Button>
        </Group>
      </Stack>
    </Modal>
  )
}

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? '' : 's'}`
}
