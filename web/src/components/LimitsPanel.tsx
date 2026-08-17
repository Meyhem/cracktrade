import { Card, Group, List, Stack, Text } from '@mantine/core'
import { IconInfoCircle } from '@tabler/icons-react'
import { useMeta } from '../api/metaContext'

/**
 * What this engine cannot tell you.
 *
 * These are residual biases the spec records as outside engine control (section 2.7), not
 * copywriting, which is why they are served by `/meta` rather than written here. They belong
 * next to a verdict: the screen where someone decides to commit money is exactly where the
 * things no amount of validation corrects for need saying.
 */
export function LimitsPanel() {
  const { meta } = useMeta()

  return (
    <Card padding="md" withBorder>
      <Stack gap="xs">
        <Group gap="xs">
          <IconInfoCircle size={16} />
          <Text fw={600} size="sm">
            What this cannot tell you
          </Text>
        </Group>
        <List size="sm" spacing={4}>
          {meta.limits.map((limit) => (
            <List.Item key={limit}>{limit}</List.Item>
          ))}
        </List>
      </Stack>
    </Card>
  )
}
