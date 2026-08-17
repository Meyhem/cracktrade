import { Alert, Badge, Code, Group, Loader, Paper, Stack, Text } from '@mantine/core'
import { IconAlertTriangle } from '@tabler/icons-react'
import { clause } from './summary'
import { alignLines } from './linediff'
import { useVersionDiff } from './queries'
import type { DiffResponse, SectionDiff } from '../../api/types'

/**
 * Two versions, side by side.
 *
 * Two panes and a structured summary above them, in that order of importance. §2.6 asks for the
 * grouping specifically because a flat line diff presents "the date range moved" and "an RSI
 * window moved by one" identically, and they are not the same magnitude of change at all — one
 * of them invalidates every run ever made against this strategy.
 *
 * The server does the comparing. Both sides are canonicalised through the YAML writer before
 * either the groups or the panes are produced (spec §15.1, amended 2026-08-17), which is what
 * makes a change listed above correspond to a highlighted line below. Diffing the two stored
 * documents here instead would show an imported config as having changed in every line, because
 * an import keeps its own formatting on purpose.
 */
export function DiffView({
  strategyId,
  from,
  to,
}: {
  strategyId: string
  from: number
  to: number
}) {
  const diff = useVersionDiff(strategyId, from, to)

  if (diff.isPending) return <Loader size="sm" />
  if (diff.isError || !diff.data) {
    return (
      <Alert color="red" title="The diff could not be loaded">
        {diff.error instanceof Error ? diff.error.message : 'The server did not answer.'}
      </Alert>
    )
  }

  return <DiffBody diff={diff.data} from={from} to={to} />
}

export function DiffBody({ diff, from, to }: { diff: DiffResponse; from: number; to: number }) {
  const changed = diff.groups.filter((group) => group.changes.length > 0)
  const breaking = changed.filter((group) => group.consequence !== null)

  return (
    <Stack gap="md">
      {breaking.length > 0 && (
        <Alert
          color="orange"
          icon={<IconAlertTriangle size={16} />}
          title="This is not a tuning change"
        >
          <Stack gap={6}>
            {breaking.map((group) => (
              <Text key={group.section} size="sm">
                {group.consequence}
              </Text>
            ))}
            <Text size="sm">
              Runs on either side of this version answered different questions, so the comparison
              table below is comparing experiments rather than strategies.
            </Text>
          </Stack>
        </Alert>
      )}

      {changed.length === 0 ? (
        <Text c="dimmed" size="sm">
          v{from} and v{to} describe the same configuration.
        </Text>
      ) : (
        <Stack gap="sm">
          {changed.map((group) => (
            <SectionGroup group={group} key={group.section} />
          ))}
        </Stack>
      )}

      <Group align="stretch" gap="sm" grow wrap="nowrap">
        <YamlPane label={`v${from}`} text={diff.from_yaml} other={diff.to_yaml} />
        <YamlPane label={`v${to}`} text={diff.to_yaml} other={diff.from_yaml} />
      </Group>

      <Text c="dimmed" size="xs">
        Both panes are written in the engine's canonical form so that the two can be compared line
        for line. A version imported from a file keeps its own formatting everywhere else.
      </Text>
    </Stack>
  )
}

const BREAKING: Record<string, string> = {
  universe: 'Universe',
  execution: 'Execution',
}

function SectionGroup({ group }: { group: SectionDiff }) {
  return (
    <Paper p="sm" withBorder>
      <Group gap="xs" mb={6}>
        <Text fw={600} size="sm" tt="capitalize">
          {group.section}
        </Text>
        {BREAKING[group.section] && (
          <Badge color="orange" size="sm" variant="light">
            breaks comparison
          </Badge>
        )}
        <Text c="dimmed" size="xs">
          {group.changes.length} change{group.changes.length === 1 ? '' : 's'}
        </Text>
      </Group>
      <Stack gap={2}>
        {group.changes.map((change) => (
          <Code key={change.path} style={{ background: 'transparent' }}>
            {clause(change)}
          </Code>
        ))}
      </Stack>
    </Paper>
  )
}

/**
 * One YAML pane with its differing lines marked.
 *
 * Aligned by longest common subsequence rather than by line number: adding an indicator shifts
 * every line after it, and a positional comparison would then mark the rest of the file as
 * changed — highlighting an `exit:` block nobody touched, underneath a summary correctly
 * reporting one addition. See `linediff.ts`.
 *
 * Changed lines carry a marker as well as a background, because a background colour alone is
 * not readable to everyone and does not survive a screenshot pasted into a chat.
 *
 * The key carries the line's position because position is what identifies it here — a YAML
 * config contains many identical lines, so the text cannot serve.
 */
function YamlPane({ label, text, other }: { label: string; text: string; other: string }) {
  const own = text.split('\n')
  const alignment = alignLines(own, other.split('\n'))
  const lines = own.map((line, index) => ({
    id: `line-${index}`,
    text: line,
    differs: alignment.left[index] === true,
  }))

  return (
    <Stack gap={4} style={{ minWidth: 0 }}>
      <Text c="dimmed" fw={600} size="xs">
        {label}
      </Text>
      <Paper p="xs" withBorder style={{ overflowX: 'auto' }}>
        <pre style={{ fontSize: 12, lineHeight: 1.5, margin: 0 }}>
          {lines.map((line) => (
            <div
              key={line.id}
              style={{
                background: line.differs ? 'var(--mantine-color-yellow-light)' : undefined,
                whiteSpace: 'pre',
              }}
            >
              <span style={{ opacity: 0.5 }}>{line.differs ? '~ ' : '  '}</span>
              {line.text || ' '}
            </div>
          ))}
        </pre>
      </Paper>
    </Stack>
  )
}
