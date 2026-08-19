import { useEffect } from 'react'
import { Alert, Button, Group, Modal, Stack, Text } from '@mantine/core'
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
 * guarantee everything else is built on (spec §14.8). So it does ask, and it says what goes:
 * the version and run counts are the number most likely to change a mind, since a strategy
 * accumulates runs quietly and the user may not have that figure to hand. It says
 * "permanently" rather than "cannot be undone", which reads as boilerplate.
 *
 * It is a plain confirm otherwise. An earlier version made the user type the strategy name;
 * that was dropped as friction that bought nothing the counts and the named title do not
 * already buy — the dialog is opened from inside the strategy it would delete, and names it
 * in the title, so "opened on the wrong strategy" is already visible without a typing test.
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

  useEffect(() => {
    if (opened) remove.reset()
    // `remove` is a stable mutation object; re-running on its identity would clear a refusal
    // the user is still reading.
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
          <Text size="sm">
            This deletes <strong>{strategy.name}</strong> permanently, with{' '}
            {plural(strategy.counts.versions ?? 0, 'configuration version')}, {plural(runs, 'run')}{' '}
            and every number and chart they produced.
          </Text>
        </Alert>

        <Text c="dimmed" size="sm">
          Forks of this strategy are independent and are not touched. If one exists, this deletion
          will be refused rather than orphaning it — delete the fork first, or keep it.
        </Text>

        <Group justify="flex-end">
          {/* Focus rests on Cancel, not on the destructive button: a reflexive Enter on the
              dialog should do nothing. */}
          <Button data-autofocus onClick={onClose} variant="default">
            Cancel
          </Button>
          <Button color="red" loading={remove.isPending} onClick={submit}>
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
