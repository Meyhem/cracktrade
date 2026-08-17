import { Alert, Loader, Paper, Stack, Text } from '@mantine/core'
import { absolute } from '../../lib/format'
import { useVersion } from './queries'

/**
 * One stored version, read-only.
 *
 * Shows the config **as it was saved**, byte for byte — including an imported file's own
 * formatting and comments. This is the one place that matters: the diff canonicalises both
 * sides so they can be compared, but "View" is the audit trail answering what the user actually
 * wrote, and reformatting it here would quietly destroy the thing the history exists to keep.
 */
export function VersionPane({ strategyId, version }: { strategyId: string; version: number }) {
  const stored = useVersion(strategyId, version)

  if (stored.isPending) return <Loader size="sm" />
  if (stored.isError || !stored.data) {
    return (
      <Alert color="red" title="That version could not be loaded">
        {stored.error instanceof Error ? stored.error.message : 'The server did not answer.'}
      </Alert>
    )
  }

  return (
    <Stack gap="xs">
      <Text c="dimmed" size="xs">
        Saved {absolute(stored.data.created_at)}
        {stored.data.note && ` — ${stored.data.note}`}
      </Text>
      <Paper p="sm" withBorder style={{ overflowX: 'auto' }}>
        <pre style={{ fontSize: 12, lineHeight: 1.5, margin: 0 }}>{stored.data.yaml}</pre>
      </Paper>
      <Text c="dimmed" size="xs">
        Exactly as it was saved, formatting and all.
      </Text>
    </Stack>
  )
}
