import { useState } from 'react'
import {
  Alert,
  Anchor,
  Breadcrumbs,
  Button,
  Center,
  Group,
  Loader,
  Stack,
  Tabs,
  Text,
  Title,
} from '@mantine/core'
import { IconAlertTriangle } from '@tabler/icons-react'
import { Link, Outlet, useLocation, useNavigate, useParams } from 'react-router'
import { ProblemAlert } from '../../components/ProblemAlert'
import { VerdictChip } from '../../components/VerdictChip'
import { useStrategy } from '../strategies/queries'
import { LaunchRunModal } from '../runs/LaunchRunModal'
import { StrategyContext } from './context'
import { dateOnly } from '../../lib/format'
import type { RunKind, StrategyDetail, VerdictState } from '../../api/types'

const TABS = [
  { value: 'config', label: 'Config' },
  { value: 'optimizations', label: 'Optimizations' },
  { value: 'backtests', label: 'Backtests' },
  { value: 'validation', label: 'Validation' },
  { value: 'charts', label: 'Charts' },
  { value: 'history', label: 'History' },
] as const

/**
 * The banner a promoted strategy carries.
 *
 * This is the single most important anti-footgun in the app: promotion is exactly the moment
 * a caveat gets lost, because the new strategy looks fresh while its parameters came out of
 * a search nobody validated. The text is the server's, not ours — it is derived state that
 * disappears on its own once this strategy's own walk-forward passes.
 */
function PromotedWarning({ strategy }: { strategy: StrategyDetail }) {
  const warning = strategy.promoted_warning
  if (!warning) return null

  return (
    <Alert color="orange" icon={<IconAlertTriangle size={18} />} variant="light">
      <Stack gap={4}>
        <Text size="sm">{warning.text}</Text>
        {warning.parent_name && (
          <Anchor
            component={Link}
            size="sm"
            to={`/strategies/${strategy.lineage.parent_strategy_id}/runs/${warning.origin_run_id}`}
          >
            See {warning.parent_name} #{warning.origin_run_number}
          </Anchor>
        )}
      </Stack>
    </Alert>
  )
}

export function StrategyLayout() {
  const { strategyId } = useParams()
  const navigate = useNavigate()
  const location = useLocation()
  const { data: strategy, error, isPending } = useStrategy(strategyId ?? '')
  const [launchKind, setLaunchKind] = useState<RunKind | null>(null)

  if (isPending) {
    return (
      <Center py="xl">
        <Loader />
      </Center>
    )
  }
  if (error) return <ProblemAlert error={error} />

  const segment = location.pathname.split('/')[3] ?? 'config'
  const activeTab = TABS.some((tab) => tab.value === segment) ? segment : 'config'
  const config = strategy.head.config as {
    universe?: { ticker?: string; start_date?: string; end_date?: string }
  }
  const universe = config.universe ?? {}

  return (
    <StrategyContext value={{ strategy }}>
      <Stack gap="md">
        <Stack gap={4}>
          <Breadcrumbs separator="›">
            <Anchor component={Link} size="sm" to="/strategies">
              Strategies
            </Anchor>
            {strategy.lineage.parent_strategy_id && (
              <Anchor
                component={Link}
                size="sm"
                to={`/strategies/${strategy.lineage.parent_strategy_id}`}
              >
                {strategy.promoted_warning?.parent_name ?? 'parent'}
              </Anchor>
            )}
            <Text size="sm">{strategy.name}</Text>
          </Breadcrumbs>

          <Group justify="space-between">
            <Group gap="sm">
              <Title order={2}>{strategy.name}</Title>
              <VerdictChip
                failureCount={strategy.verdict.failures.length}
                verdict={strategy.verdict.state as VerdictState}
              />
            </Group>
            <Group gap="xs">
              <Button onClick={() => setLaunchKind('backtest')} variant="default">
                Backtest
              </Button>
              <Button onClick={() => setLaunchKind('optimize')} variant="default">
                Optimize
              </Button>
              <Button onClick={() => setLaunchKind('walk_forward')}>Walk-forward</Button>
            </Group>
          </Group>

          <Text c="dimmed" size="sm">
            {universe.ticker ?? 'no ticker'} · {dateOnly(universe.start_date ?? null)} →{' '}
            {dateOnly(universe.end_date ?? null)} · v{strategy.head.version} ·{' '}
            {strategy.counts.versions} version{strategy.counts.versions === 1 ? '' : 's'}
          </Text>
        </Stack>

        <PromotedWarning strategy={strategy} />

        <Tabs
          onChange={(value) => value && void navigate(`/strategies/${strategy.id}/${value}`)}
          value={activeTab}
        >
          <Tabs.List>
            {TABS.map((tab) => (
              <Tabs.Tab key={tab.value} value={tab.value}>
                {tab.label}
              </Tabs.Tab>
            ))}
          </Tabs.List>
        </Tabs>

        <Outlet />

        {launchKind && (
          <LaunchRunModal
            kind={launchKind}
            onClose={() => setLaunchKind(null)}
            opened
            searchableParameters={1}
            strategyId={strategy.id}
          />
        )}
      </Stack>
    </StrategyContext>
  )
}
