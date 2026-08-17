import { useState } from 'react'
import { Alert, Button, Group, Modal, Stack, Text, TextInput } from '@mantine/core'
import { IconAlertTriangle, IconPencilExclamation } from '@tabler/icons-react'
import { DiffView } from './DiffView'
import { useRestoreVersion } from './queries'
import type { VersionSummary } from '../../api/types'

/**
 * Restoring an older configuration.
 *
 * Restore **appends**. It does not rewind, and it deletes nothing (spec §14.2): a copy of the
 * old config arrives as a new head, and the versions in between stay exactly where they are.
 * The wording throughout says so, because "restore" in most applications means the opposite and
 * a user who believes they are discarding their recent work will not press this button.
 *
 * Two things must be true before it is offered:
 *
 * - the user has seen the diff they are about to apply, and the number of runs it will make
 *   stale — a restore is a config change like any other, and every run against the current head
 *   stops describing the current head the moment it lands;
 * - the editor has no unsaved edit. A restore moves the head, and the editor's draft is pinned
 *   to the version it was started from, so restoring underneath one turns a pending save into a
 *   409. Refusing here, with the reason, is better than letting them write a note and then be
 *   told no.
 */
export function RestoreModal({
  strategyId,
  target,
  head,
  editorDirty,
  onClose,
}: {
  strategyId: string
  target: VersionSummary | null
  /** The current head, in full — its run count is what the staleness warning is about. */
  head: VersionSummary | null
  editorDirty: boolean
  onClose: () => void
}) {
  const [note, setNote] = useState('')
  const restore = useRestoreVersion(strategyId)

  const close = () => {
    setNote('')
    restore.reset()
    onClose()
  }

  return (
    <Modal
      onClose={close}
      opened={target !== null}
      size="90rem"
      title={target && `Restore v${target.version}`}
    >
      {target && head !== null && (
        <Stack gap="md">
          <Alert color="blue" title="Nothing is rewound">
            <Text size="sm">
              This copies v{target.version}'s configuration to a <strong>new</strong> version at the
              head — v{head.version + 1}. v{head.version} and everything before it stay exactly
              where they are, so if you change your mind you can restore forward again.
            </Text>
          </Alert>

          {editorDirty && (
            <Alert
              color="orange"
              icon={<IconPencilExclamation size={16} />}
              title="There is an unsaved edit in the config editor"
            >
              Restoring moves the head, and that edit is pinned to the version it was started from —
              saving it afterwards would be refused. Save it or discard it on the Config tab first.
            </Alert>
          )}

          <StaleWarning count={head.runs_against} version={head.version} />

          <Text fw={600} size="sm">
            What restoring would change, against the current head
          </Text>
          <DiffView from={head.version} strategyId={strategyId} to={target.version} />

          <TextInput
            description="Optional. Defaults to recording which version was restored."
            label="Why"
            onChange={(event) => setNote(event.currentTarget.value)}
            placeholder={`restored the complete config of v${target.version}`}
            value={note}
          />

          {restore.isError && (
            <Alert color="red" title="The restore was refused">
              {restore.error instanceof Error
                ? restore.error.message
                : 'The server did not answer.'}
            </Alert>
          )}

          <Group justify="flex-end">
            <Button onClick={close} variant="default">
              Cancel
            </Button>
            <Button
              disabled={editorDirty}
              loading={restore.isPending}
              onClick={() =>
                restore.mutate(
                  { version: target.version, ...(note.trim() ? { note: note.trim() } : {}) },
                  { onSuccess: close },
                )
              }
            >
              Restore as v{head.version + 1}
            </Button>
          </Group>
        </Stack>
      )}
    </Modal>
  )
}

/**
 * How many runs this will make stale.
 *
 * Counted against the *current head*, because those are the runs that currently describe the
 * strategy as it stands. A restore does not invalidate them — they still record what happened —
 * but it does mean the strategy no longer matches them, which is what "stale" means everywhere
 * else in this application.
 */
function StaleWarning({ count, version }: { count: number; version: number }) {
  if (count === 0) {
    return (
      <Text c="dimmed" size="sm">
        No runs were made against v{version}, so nothing becomes stale.
      </Text>
    )
  }
  return (
    <Alert color="yellow" icon={<IconAlertTriangle size={16} />} title="Runs that become stale">
      <Text size="sm">
        {count} run{count === 1 ? '' : 's'} against v{version} will be marked stale. They are not
        deleted and their numbers do not change — they simply stop describing the current
        configuration, and the strategy's verdict reverts to whatever the restored config has earned
        on its own.
      </Text>
    </Alert>
  )
}
