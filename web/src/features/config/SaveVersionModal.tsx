import { useState } from 'react'
import { Alert, Button, Group, Loader, Modal, Stack, Text, TextInput } from '@mantine/core'
import { IconAlertTriangle } from '@tabler/icons-react'
import { notifications } from '@mantine/notifications'
import { ProblemAlert } from '../../components/ProblemAlert'
import { ConfigDiff } from '../runs/view/ConfigDiff'
import { useConfigDiff } from '../../api/config'
import { useSaveVersion } from './queries'
import { useStrategyContext } from '../strategy/context'

/**
 * Saving an edit as a version.
 *
 * The diff is shown before the save, not after. A version is the unit in which runs are made
 * comparable, so what changed between two of them is the thing that explains why two runs
 * disagree — and the moment to check that it says what the user thinks they did is before it
 * becomes an immutable row.
 *
 * Section-grouped, from the server, for the same reason the promote dialog uses it: "the date
 * range moved" and "an RSI window moved by one" are not the same magnitude of change and a line
 * diff presents them identically.
 */
export function SaveVersionModal({
  baseVersion,
  onClose,
  onSaved,
  opened,
  yaml,
}: {
  baseVersion: number
  onClose: () => void
  onSaved: () => void
  opened: boolean
  yaml: string
}) {
  const strategy = useStrategyContext()
  const save = useSaveVersion(strategy.id)
  const [note, setNote] = useState('')

  const diff = useConfigDiff(opened ? { yaml: strategy.head.yaml } : null, opened ? { yaml } : null)

  const submit = () => {
    save.mutate(
      { baseVersion, yaml, ...(note.trim() ? { note: note.trim() } : {}) },
      {
        onSuccess: (saved) => {
          notifications.show({
            color: 'blue',
            message:
              saved.stale_runs === 0
                ? `Saved as v${saved.version.version}.`
                : `Saved as v${saved.version.version}. ${saved.stale_runs} run${
                    saved.stale_runs === 1 ? '' : 's'
                  } now describe an older configuration.`,
            title: 'Version saved',
          })
          onSaved()
          onClose()
        },
      },
    )
  }

  return (
    <Modal onClose={onClose} opened={opened} size="lg" title={`Save as v${baseVersion + 1}`}>
      <Stack gap="md">
        {save.error && <ProblemAlert error={save.error} />}

        <Text size="sm">
          This appends v{baseVersion + 1}. Nothing is overwritten — v{baseVersion} keeps every run
          made against it, and those runs start describing an older configuration rather than
          disappearing.
        </Text>

        <TextInput
          description="Optional, and worth writing. In three weeks this is the only record of why."
          label="Note"
          onChange={(event) => setNote(event.currentTarget.value)}
          placeholder="widened the RSI window after fold 3 underperformed"
          value={note}
        />

        <Stack gap="xs">
          <Text fw={600} size="sm">
            What changes
          </Text>
          {diff.isPending && (
            <Group gap="xs">
              <Loader size="xs" />
              <Text c="dimmed" size="sm">
                Comparing…
              </Text>
            </Group>
          )}
          {diff.error && (
            <Alert color="orange" icon={<IconAlertTriangle size={16} />} variant="light">
              The diff could not be computed, so this save is not previewed. It will still be
              checked by the server before anything is written.
            </Alert>
          )}
          {diff.data && <ConfigDiff groups={diff.data.groups} />}
        </Stack>

        <Group justify="flex-end">
          <Button onClick={onClose} variant="default">
            Cancel
          </Button>
          <Button loading={save.isPending} onClick={submit}>
            Save v{baseVersion + 1}
          </Button>
        </Group>
      </Stack>
    </Modal>
  )
}
