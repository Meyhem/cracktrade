import {
  ActionIcon,
  AppShell as MantineAppShell,
  Badge,
  Group,
  Text,
  Tooltip,
  useComputedColorScheme,
  useMantineColorScheme,
} from '@mantine/core'
import { IconMoon, IconSun, IconPlugConnected, IconPlugConnectedX } from '@tabler/icons-react'
import { Link, Outlet } from 'react-router'
import { useRunEvents } from '../api/events'
import { useActiveRuns } from '../api/runs'
import { useMeta } from '../api/metaContext'

/** The stream and the poller are started once, here, for the whole app. */
function LiveIndicator() {
  const sse = useRunEvents()
  const { data: active } = useActiveRuns()
  const count = active?.length ?? 0

  return (
    <Group gap="xs">
      {count > 0 && (
        <Badge color="blue" variant="light">
          {count} run{count === 1 ? '' : 's'} in flight
        </Badge>
      )}
      <Tooltip
        label={
          sse === 'open'
            ? 'Live updates connected'
            : 'Live updates disconnected — falling back to polling'
        }
      >
        <Text c={sse === 'open' ? 'teal' : 'orange'} component="span" lh={1}>
          {sse === 'open' ? <IconPlugConnected size={16} /> : <IconPlugConnectedX size={16} />}
        </Text>
      </Tooltip>
    </Group>
  )
}

function ColorSchemeToggle() {
  const { setColorScheme } = useMantineColorScheme()
  const computed = useComputedColorScheme('light')

  return (
    <ActionIcon
      aria-label="Toggle color scheme"
      onClick={() => setColorScheme(computed === 'dark' ? 'light' : 'dark')}
      variant="default"
    >
      {computed === 'dark' ? <IconSun size={16} /> : <IconMoon size={16} />}
    </ActionIcon>
  )
}

export function AppShell() {
  const { meta } = useMeta()

  return (
    <MantineAppShell header={{ height: 52 }} padding="md">
      <MantineAppShell.Header>
        <Group h="100%" justify="space-between" px="md">
          <Group gap="sm">
            <Text component={Link} fw={700} size="sm" to="/strategies" td="none" c="inherit">
              cracktrade
            </Text>
            <Text c="dimmed" size="xs">
              engine {meta.engine_version}
            </Text>
          </Group>
          <Group gap="md">
            <LiveIndicator />
            <ColorSchemeToggle />
          </Group>
        </Group>
      </MantineAppShell.Header>

      <MantineAppShell.Main>
        <Outlet />
      </MantineAppShell.Main>
    </MantineAppShell>
  )
}
