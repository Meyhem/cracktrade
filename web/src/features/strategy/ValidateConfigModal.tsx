import { Alert, Button, Code, Group, Loader, Modal, Stack, Table, Text } from '@mantine/core'
import { IconAlertTriangle, IconCheck } from '@tabler/icons-react'
import { ProblemAlert } from '../../components/ProblemAlert'
import { useValidatedConfig } from '../../api/config'
import { useStrategyContext } from './context'
import { ratio } from '../../lib/format'

/**
 * Checking a configuration without running anything.
 *
 * Instant, and touches no market data. Worth its own action because the alternative — finding
 * out from a failed run five minutes later — spends real time to learn something the engine
 * could have said immediately.
 *
 * It also answers the question the launch dialog only summarises: which numeric fields the
 * search is allowed to move, and between what bounds. That range is what the optimizer is
 * actually permitted to do, and it is otherwise invisible until a run finishes.
 */
export function ValidateConfigModal({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const strategy = useStrategyContext()
  const { data, error, isPending } = useValidatedConfig(opened ? strategy.head.config : undefined)

  return (
    <Modal onClose={onClose} opened={opened} size="lg" title={`Validate v${strategy.head.version}`}>
      <Stack gap="md">
        {error && <ProblemAlert error={error} />}

        {isPending && !error && (
          <Group gap="xs">
            <Loader size="xs" />
            <Text c="dimmed" size="sm">
              Checking…
            </Text>
          </Group>
        )}

        {data && (
          <>
            {data.valid ? (
              <Alert color="green" icon={<IconCheck size={18} />} variant="light">
                This configuration is a valid strategy. Nothing was run and no market data was
                fetched.
              </Alert>
            ) : (
              <Alert color="red" icon={<IconAlertTriangle size={18} />} variant="light">
                <Stack gap={4}>
                  <Text fw={600} size="sm">
                    Please fix the following issues in your strategy file:
                  </Text>
                  {data.errors.map((issue) => (
                    <Text key={`${issue.path}-${issue.message}`} size="sm">
                      - {issue.path ? `${issue.path}: ` : ''}
                      {issue.message}
                    </Text>
                  ))}
                </Stack>
              </Alert>
            )}

            {data.warnings.length > 0 && (
              <Alert color="orange" icon={<IconAlertTriangle size={18} />} variant="light">
                <Stack gap={4}>
                  <Text fw={600} size="sm">
                    Warnings — these never block a run:
                  </Text>
                  {data.warnings.map((issue) => (
                    <Text key={`${issue.path}-${issue.message}`} size="sm">
                      - {issue.path ? `${issue.path}: ` : ''}
                      {issue.message}
                    </Text>
                  ))}
                </Stack>
              </Alert>
            )}

            <Stack gap="xs">
              <Text fw={600} size="sm">
                What a search may move
              </Text>
              {data.searchable_parameters.length === 0 ? (
                <Text size="sm">
                  Nothing. Every numeric field is pinned, so an optimization would re-score one
                  configuration — the engine refuses that rather than spending the time.
                </Text>
              ) : (
                <Table>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>Parameter</Table.Th>
                      <Table.Th>Now</Table.Th>
                      <Table.Th>Search range</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {data.searchable_parameters.map((parameter) => (
                      <Table.Tr key={parameter.path}>
                        <Table.Td>
                          <Code>{parameter.path}</Code>
                        </Table.Td>
                        <Table.Td>{ratio(parameter.value)}</Table.Td>
                        <Table.Td>
                          {ratio(parameter.low)} – {ratio(parameter.high)}
                        </Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              )}
              <Text c="dimmed" size="xs">
                Numbers written inside a signal expression are not in this list and cannot be: the
                search cannot reach them. To tune one, move it into an indicator.
              </Text>
            </Stack>

            <Stack gap="xs">
              <Text fw={600} size="sm">
                Names a signal may refer to
              </Text>
              <Group gap={4}>
                {data.namespace.map((name) => (
                  <Code key={name}>{name}</Code>
                ))}
              </Group>
            </Stack>
          </>
        )}

        <Group justify="flex-end">
          <Button onClick={onClose} variant="default">
            Close
          </Button>
        </Group>
      </Stack>
    </Modal>
  )
}
