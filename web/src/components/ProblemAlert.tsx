import { Alert, Code, Group, List, Stack, Text } from '@mantine/core'
import { IconAlertTriangle } from '@tabler/icons-react'
import { ApiError } from '../api/errors'

/**
 * How a failure is shown.
 *
 * The engine's own error text is rendered verbatim, never replaced with "something went
 * wrong": the categories it distinguishes (a bad config, missing market data, an engine
 * fault, a causality violation) call for different reactions from the user, and only the
 * last is a bug in this software.
 */
export function ProblemAlert({ error, title }: { error: unknown; title?: string }) {
  const problem = error instanceof ApiError ? error : null
  const heading = title ?? problem?.title ?? 'Something failed'
  const detail =
    problem?.detail ?? (error instanceof Error ? error.message : 'An unexpected error occurred.')

  return (
    <Alert
      color={problem?.kind === 'invariant' ? 'red' : 'orange'}
      icon={<IconAlertTriangle size={18} />}
      title={heading}
      variant="light"
    >
      <Stack gap="xs">
        <Text size="sm">{detail}</Text>

        {problem && problem.issues.length > 0 && (
          <List size="sm" spacing={4}>
            {problem.issues.map((issue) => (
              <List.Item key={`${issue.path}:${issue.message}`}>
                {issue.path && <Code>{issue.path}</Code>} {issue.message}
                {issue.suggestion && (
                  <Text component="span" size="sm" c="dimmed">
                    {' '}
                    — try <Code>{issue.suggestion}</Code>
                  </Text>
                )}
              </List.Item>
            ))}
          </List>
        )}

        {problem?.kind === 'invariant' && (
          <Text size="sm" fw={500}>
            This is a bug in the engine rather than a problem with your configuration.
          </Text>
        )}

        {problem?.requestId && (
          <Group gap={6}>
            <Text size="xs" c="dimmed">
              request
            </Text>
            <Code>{problem.requestId}</Code>
          </Group>
        )}
      </Stack>
    </Alert>
  )
}
