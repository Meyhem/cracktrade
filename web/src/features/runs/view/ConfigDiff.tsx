import { Card, Code, Group, Stack, Table, Text } from '@mantine/core'
import { IconAlertTriangle } from '@tabler/icons-react'
import type { SectionDiff } from '../../../api/types'

/**
 * A configuration diff, grouped by section.
 *
 * Grouped rather than flat because that is how consequences group: "the date range moved" and
 * "an RSI window moved by one" are not the same magnitude of change, and a line diff presents
 * them identically. Sections that changed nothing are listed too — "indicators: no changes"
 * tells the reader the search space is the same, and omitting it leaves them to infer that
 * from an absence.
 */
export function ConfigDiff({ groups }: { groups: SectionDiff[] }) {
  const changed = groups.filter((group) => group.changes.length > 0)
  const unchanged = groups.filter((group) => group.changes.length === 0)

  if (changed.length === 0) {
    return (
      <Text size="sm">
        The two configurations are identical. Nothing would change by adopting this one.
      </Text>
    )
  }

  return (
    <Stack gap="sm">
      {changed.map((group) => (
        <Card key={group.section} padding="sm" withBorder>
          <Stack gap="xs">
            <Text fw={600} size="sm">
              {group.section}
            </Text>
            {group.consequence && (
              <Group align="flex-start" gap={6} wrap="nowrap">
                <IconAlertTriangle size={16} />
                <Text size="sm">{group.consequence}</Text>
              </Group>
            )}
            <Table>
              <Table.Tbody>
                {group.changes.map((change) => (
                  <Table.Tr key={change.path}>
                    <Table.Td>
                      <Code>{change.path}</Code>
                    </Table.Td>
                    <Table.Td>
                      <Text c="dimmed" size="sm">
                        {render(change.old)}
                      </Text>
                    </Table.Td>
                    <Table.Td w={20}>→</Table.Td>
                    <Table.Td>
                      <Text fw={500} size="sm">
                        {render(change.new)}
                      </Text>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Stack>
        </Card>
      ))}

      {unchanged.length > 0 && (
        <Text c="dimmed" size="xs">
          Unchanged: {unchanged.map((group) => group.section).join(', ')}
        </Text>
      )}
    </Stack>
  )
}

/** `null` on one side of a change means the key was absent, which is not the value null. */
function render(value: unknown): string {
  if (value === null || value === undefined) return 'not set'
  if (typeof value === 'string') return value
  return JSON.stringify(value)
}
