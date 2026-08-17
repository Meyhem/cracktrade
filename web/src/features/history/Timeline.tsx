import { useState } from 'react'
import { ActionIcon, Badge, Button, Group, Modal, Paper, Stack, Text, Tooltip } from '@mantine/core'
import { IconArrowBackUp, IconGitCompare } from '@tabler/icons-react'
import { absolute, relative } from '../../lib/format'
import { FLAG_MEANING, flagLabel, summarise } from './summary'
import { DiffView } from './DiffView'
import { VersionPane } from './VersionPane'
import { RestoreModal } from './RestoreModal'
import type { VersionSummary } from '../../api/types'

/**
 * The version timeline, newest first.
 *
 * Newest first because the question a user brings to this screen is "what did I just do", and
 * the comparison table below reads oldest first because the question *it* answers is "am I
 * making progress". They disagree on purpose, and each says which way it runs.
 */
export function Timeline({
  strategyId,
  versions,
  editorDirty,
}: {
  strategyId: string
  versions: VersionSummary[]
  editorDirty: boolean
}) {
  const [diffing, setDiffing] = useState<{ from: number; to: number } | null>(null)
  const [viewing, setViewing] = useState<number | null>(null)
  const [restoring, setRestoring] = useState<VersionSummary | null>(null)

  return (
    <>
      <Stack gap="xs">
        {versions.map((version) => (
          <Row
            editorDirty={editorDirty}
            key={version.version}
            onDiff={() =>
              setDiffing({ from: Math.max(1, version.version - 1), to: version.version })
            }
            onRestore={() => setRestoring(version)}
            onView={() => setViewing(version.version)}
            version={version}
          />
        ))}
      </Stack>

      <Modal
        onClose={() => setDiffing(null)}
        opened={diffing !== null}
        size="90rem"
        title={diffing && `v${diffing.from} → v${diffing.to}`}
      >
        {diffing && <DiffView from={diffing.from} strategyId={strategyId} to={diffing.to} />}
      </Modal>

      <Modal
        onClose={() => setViewing(null)}
        opened={viewing !== null}
        size="lg"
        title={viewing && `v${viewing}, as it was saved`}
      >
        {viewing !== null && <VersionPane strategyId={strategyId} version={viewing} />}
      </Modal>

      <RestoreModal
        editorDirty={editorDirty}
        head={versions.find((entry) => entry.head) ?? null}
        onClose={() => setRestoring(null)}
        strategyId={strategyId}
        target={restoring}
      />
    </>
  )
}

function Row({
  version,
  onDiff,
  onView,
  onRestore,
  editorDirty,
}: {
  version: VersionSummary
  onDiff: () => void
  onView: () => void
  onRestore: () => void
  editorDirty: boolean
}) {
  const first = version.version === 1

  return (
    <Paper p="sm" withBorder>
      <Group align="flex-start" justify="space-between" wrap="nowrap">
        <Stack gap={4} style={{ minWidth: 0 }}>
          <Group gap="xs">
            <Text fw={600}>v{version.version}</Text>
            {version.head && (
              <Badge size="sm" variant="filled">
                head
              </Badge>
            )}
            <Badge color="gray" size="sm" variant="light">
              {version.origin}
            </Badge>
            {version.restored_from !== null && (
              <Badge color="grape" size="sm" variant="light">
                restored from v{version.restored_from}
              </Badge>
            )}
            {version.flags.map((flag) => (
              <Tooltip key={flag} label={FLAG_MEANING[flag] ?? flag} multiline w={340}>
                <Badge color="orange" size="sm" variant="light">
                  {flagLabel(flag)}
                </Badge>
              </Tooltip>
            ))}
          </Group>

          {version.note && <Text size="sm">{version.note}</Text>}

          <Text c="dimmed" size="xs">
            {first ? 'The first version of this strategy.' : summarise(version.change_summary)}
          </Text>

          <Group gap="xs">
            <Tooltip label={absolute(version.created_at)}>
              <Text c="dimmed" size="xs">
                {relative(version.created_at)}
              </Text>
            </Tooltip>
            <Text c="dimmed" size="xs">
              ·{' '}
              {version.runs_against === 0
                ? 'never run'
                : `${version.runs_against} run${version.runs_against === 1 ? '' : 's'} against it`}
            </Text>
          </Group>
        </Stack>

        <Group gap={4} wrap="nowrap">
          <Button onClick={onView} size="compact-sm" variant="subtle">
            View
          </Button>
          <Tooltip
            label={
              first
                ? 'There is nothing before v1 to compare with'
                : 'Compare with the version before it'
            }
          >
            <ActionIcon
              aria-label={`Diff v${version.version}`}
              disabled={first}
              onClick={onDiff}
              variant="subtle"
            >
              <IconGitCompare size={16} />
            </ActionIcon>
          </Tooltip>
          <Tooltip
            label={
              version.head
                ? 'This is already the head'
                : editorDirty
                  ? 'Save or discard the unsaved edit first'
                  : 'Bring this config back as a new version'
            }
          >
            <ActionIcon
              aria-label={`Restore v${version.version}`}
              disabled={version.head}
              onClick={onRestore}
              variant="subtle"
            >
              <IconArrowBackUp size={16} />
            </ActionIcon>
          </Tooltip>
        </Group>
      </Group>
    </Paper>
  )
}
