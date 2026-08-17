import { Card, Container, Group, List, Stack, Text, Title } from '@mantine/core'
import { IconInfoCircle } from '@tabler/icons-react'
import { useMeta } from '../../api/metaContext'

/**
 * A placeholder landing page that proves the data layer is wired, and a home for the
 * "what this cannot tell you" panel.
 *
 * The limits are served by `/meta` rather than written here. They are residual biases the
 * engine records as outside its control (spec section 2.7), not copy, and a UI that phrased
 * them itself would eventually phrase them more softly than the engine meant them.
 */
export function EnginePanel() {
  const { meta } = useMeta()

  return (
    <Container size="md">
      <Stack gap="lg">
        <div>
          <Title order={2}>Strategies</Title>
          <Text c="dimmed" size="sm">
            The strategy list lands in the next phase. The engine is connected.
          </Text>
        </div>

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

        <Card padding="md" withBorder>
          <Stack gap={4}>
            <Text fw={600} size="sm">
              Engine
            </Text>
            <Text size="sm">
              version {meta.engine_version} · {meta.indicators.length} indicators ·{' '}
              {meta.objectives.join(', ')}
            </Text>
            <Text c="dimmed" size="sm">
              A result is suppressed below {meta.trade_floor} closed trades. Credibility is judged
              at {meta.significance} significance, and a parameter is unstable when a nudge costs
              more than {Math.round(meta.instability_threshold * 100)}% of the objective.
            </Text>
          </Stack>
        </Card>
      </Stack>
    </Container>
  )
}
