import { Card, Stack, Text } from '@mantine/core'
import type { ReactNode } from 'react'

/**
 * What an empty tab says.
 *
 * The brief is specific about this: an empty state explains what the action does and why
 * you would run it, and carries the button to run it. A shrug — "no data" — leaves the user
 * to guess whether the tab is broken, unimplemented, or simply unused.
 */
export function EmptyState({
  title,
  children,
  action,
}: {
  title: string
  children?: ReactNode
  action?: ReactNode
}) {
  return (
    <Card padding="xl" withBorder>
      <Stack align="center" gap="sm">
        <Text fw={600}>{title}</Text>
        {children && (
          <Text c="dimmed" maw={560} size="sm" ta="center">
            {children}
          </Text>
        )}
        {action}
      </Stack>
    </Card>
  )
}
