import { useState } from 'react'
import {
  Alert,
  Anchor,
  Badge,
  Box,
  Card,
  Center,
  Group,
  Loader,
  NavLink,
  Select,
  Stack,
  Text,
  Title,
} from '@mantine/core'
import { IconAlertTriangle } from '@tabler/icons-react'
import { Link, useSearchParams } from 'react-router'
import { EmptyState } from '../../components/EmptyState'
import { ProblemAlert } from '../../components/ProblemAlert'
import { StaleBadge } from '../../components/StaleBadge'
import { LaunchRunModal } from '../runs/LaunchRunModal'
import { useRun, useRuns } from '../runs/queries'
import { useStrategyContext } from '../strategy/context'
import { useMeta } from '../../api/metaContext'
import { GroupA } from './GroupA'
import { GroupB } from './GroupB'
import { GroupC } from './GroupC'
import { GroupD } from './GroupD'
import { SERIES, filledDatesOf, monthlyOf, pointsOf, stitchMonthly } from './series'
import { seriesCsvHref, useSeriesAcrossFolds, useSeriesBundle } from './queries'
import { chartableRuns, defaultRun, foldLabel, seriesFold, whyThisRun } from './selection'
import { READY, tradeFloor } from './state'
import { ALL_SERIES, GROUPS, viewOf } from './view'
import { absolute, percent, runKindLabel } from '../../lib/format'
import { closedTrades, validationResult, type ValidationResult } from '../../lib/result'
import type { Run } from '../../api/types'

/**
 * The charts tab.
 *
 * The run selector at the top is not decoration and it is deliberately not a chart: charts
 * describe one run, and a tab that quietly drew the newest one is how a user ends up judging a
 * configuration they have since edited. Which run is on screen, why it is the default, and
 * whether it is stale are stated in words above every picture on the page.
 *
 * The selection lives in the query string. A chart shared as a screenshot has already lost the
 * one thing that says which configuration it describes; a link should not lose it too.
 */
export function ChartsTab() {
  const strategy = useStrategyContext()
  const meta = useMeta()
  const [params, setParams] = useSearchParams()
  const [launchOpen, setLaunchOpen] = useState(false)

  const { data, error, isPending } = useRuns({ strategyId: strategy.id, limit: 200 })

  if (isPending) {
    return (
      <Center py="xl">
        <Loader />
      </Center>
    )
  }
  if (error) return <ProblemAlert error={error} />

  const chartable = chartableRuns(data.runs)
  if (chartable.length === 0) {
    return (
      <EmptyState title="No run has finished yet">
        Charts describe a run, not a configuration — there is nothing to draw until one has landed.
        Start with a backtest to see what this strategy did, then a walk-forward to find out whether
        that result survives data it never saw.
      </EmptyState>
    )
  }

  const requested = params.get('run')
  const selected = chartable.find((run) => run.id === requested) ?? defaultRun(chartable)
  if (!selected) return null

  const foldParam = params.get('fold')
  const fold = foldParam === null || foldParam === 'combined' ? null : Number(foldParam)

  // One navigation per interaction. Two sequential calls would each rebuild from this render's
  // `params`, so the second would silently undo the first — which is how switching runs used to
  // flash the query string and leave the old chart on screen.
  const set = (updates: Record<string, string | null>) => {
    const next = new URLSearchParams(params)
    for (const [key, value] of Object.entries(updates)) {
      if (value === null) next.delete(key)
      else next.set(key, value)
    }
    setParams(next, { replace: true })
  }

  return (
    <Stack gap="lg">
      <Card padding="md" withBorder>
        <Stack gap="sm">
          <Group align="flex-end" gap="md">
            <Select
              allowDeselect={false}
              data={chartable.map((run) => ({
                value: run.id,
                label: `${runKindLabel(run.kind)} #${run.number} · v${run.version} · ${absolute(run.finished_at)}${run.stale ? ' · stale' : ''}`,
              }))}
              description="Charts describe one run."
              label="Showing"
              onChange={(value) => {
                set({ run: value, fold: null })
              }}
              value={selected.id}
              w={460}
            />
            {selected.stale && <StaleBadge version={selected.version} />}
            <Text c="dimmed" pb={8} size="xs">
              {whyThisRun(selected, chartable)}
            </Text>
          </Group>

          {selected.stale && (
            <Alert color="orange" variant="light">
              This run used v{selected.version}; the strategy is now on v{strategy.head.version}.
              Every chart below describes the older configuration, not the one you would run today.{' '}
              <Anchor component={Link} size="sm" to={`/strategies/${strategy.id}/config`}>
                See the current config
              </Anchor>
            </Alert>
          )}
        </Stack>
      </Card>

      <Group align="flex-start" gap="xl" wrap="nowrap">
        <Box style={{ position: 'sticky', top: 16, minWidth: 190 }} visibleFrom="md">
          {GROUPS.map((group) => (
            <NavLink href={`#${group.id}`} key={group.id} label={group.label} />
          ))}
        </Box>

        <Box style={{ flex: 1, minWidth: 0 }}>
          <RunCharts
            fold={fold}
            onFold={(next) => set({ fold: next === null ? 'combined' : String(next) })}
            onLaunchWalkForward={() => setLaunchOpen(true)}
            run={selected}
            tradeFloorValue={meta.meta.trade_floor}
          />
        </Box>
      </Group>

      {launchOpen && (
        <LaunchRunModal kind="walk_forward" onClose={() => setLaunchOpen(false)} opened />
      )}
    </Stack>
  )
}

function RunCharts({
  run,
  fold,
  onFold,
  onLaunchWalkForward,
  tradeFloorValue,
}: {
  run: Run
  fold: number | null
  onFold: (fold: number | null) => void
  onLaunchWalkForward: () => void
  tradeFloorValue: number
}) {
  const { data, error, isPending } = useRun(run.id)
  const seriesFoldIndex = seriesFold(run.kind, fold)
  const { bundle, isPending: seriesPending } = useSeriesBundle(run.id, seriesFoldIndex, ALL_SERIES)

  const report: ValidationResult | null =
    data?.result && run.kind === 'walk_forward' ? validationResult(data.result) : null
  const combined = run.kind === 'walk_forward' && fold === null
  const foldCount = report?.folds.length ?? 0

  // Read across every fold only for the stitched monthly grid — the one Group C chart whose
  // windows can be laid end to end without compounding across a boundary.
  const { perFold } = useSeriesAcrossFolds(
    combined ? run.id : null,
    SERIES.monthly,
    combined ? Array.from({ length: foldCount }, (_unused, index) => index + 1) : [],
  )

  if (isPending) {
    return (
      <Center py="xl">
        <Loader />
      </Center>
    )
  }
  if (error) return <ProblemAlert error={error} />
  if (!data.result) {
    return (
      <EmptyState title="This run recorded no result">
        It landed as succeeded with nothing stored. Treat it as unusable rather than as a result of
        zero.
      </EmptyState>
    )
  }

  const view = viewOf(run.kind, data.result, fold)
  const csvHref = (name: string) => seriesCsvHref(run.id, name, seriesFoldIndex ?? 0)

  const monthly = combined
    ? stitchMonthly(
        perFold.map((entry) => ({ fold: entry.fold, cells: monthlyOf(entry.points, entry.fold) })),
      )
    : monthlyOf(bundle[SERIES.monthly], seriesFoldIndex)

  const floor = view.perFoldChartsAvailable
    ? tradeFloor(
        view.metrics?.hasEnoughTradesToJudge ?? null,
        view.metrics?.totalTrades ?? null,
        tradeFloorValue,
      )
    : view.unavailable

  return (
    <Stack gap="xl">
      {run.kind === 'walk_forward' && (
        <FoldSelector count={foldCount} onChange={onFold} report={report} value={fold} />
      )}

      {/* Two things are true of these charts that are not true of any other run's, and both
          would otherwise be invisible: the curve covers the holdout rather than the whole date
          range, and the strategy it belongs to is not the one named at the top of this page.
          Unlabelled, an evolution equity curve is a short window presented in a frame that
          means "the whole backtest" everywhere else on this tab. */}
      {run.kind === 'evolve' && (
        <Alert color="orange" icon={<IconAlertTriangle size={18} />} variant="light">
          <Stack gap={4}>
            <Text fw={600} size="sm">
              These charts cover the holdout only, for a strategy this one does not contain.
            </Text>
            <Text size="sm">
              An evolution run composed its own entry and exit conditions using this
              strategy&rsquo;s ticker and costs, then drew the curve below on the final stretch of
              history it had never seen — not the full date range every other run here charts.
              Promote the run to get a strategy whose charts describe itself.
            </Text>
          </Stack>
        </Alert>
      )}

      <GroupHeading group={GROUPS[0]} />
      {seriesPending ? (
        <Center py="xl">
          <Loader size="sm" />
        </Center>
      ) : (
        <GroupA
          benchmarkEquity={pointsOf(bundle[SERIES.benchmarkEquity])}
          close={pointsOf(bundle[SERIES.close])}
          csvHref={csvHref}
          drawdown={pointsOf(bundle[SERIES.drawdown])}
          equity={pointsOf(bundle[SERIES.equity])}
          filled={filledDatesOf(bundle[SERIES.filled])}
          state={floor}
          trades={view.trades}
        />
      )}

      <GroupHeading group={GROUPS[1]} />
      <GroupB closed={closedTrades(view.trades)} state={floor} trades={view.trades} />

      <GroupHeading group={GROUPS[2]} />
      <GroupC
        benchmarkYearly={view.benchmarkYearly}
        combined={combined}
        csvHref={csvHref}
        monthly={monthly}
        rolling12m={pointsOf(bundle[SERIES.rolling12m])}
        // Group C is not scoped to trades in Combined view: the monthly grid stitches, and the
        // other two refuse on their own grounds.
        state={combined ? READY : floor}
        worstRolling12m={view.metrics?.worstRolling12mPct ?? null}
        yearly={view.metrics?.yearlyReturns ?? []}
      />

      <GroupHeading group={GROUPS[3]} />
      <GroupD onLaunchWalkForward={onLaunchWalkForward} report={report} />
    </Stack>
  )
}

function GroupHeading({ group }: { group: (typeof GROUPS)[number] }) {
  return (
    <Stack gap={2} id={group.id} style={{ scrollMarginTop: 16 }}>
      <Title order={4}>{group.label}</Title>
      <Text c="dimmed" size="sm">
        {group.question}
      </Text>
    </Stack>
  )
}

/**
 * The fold selector, for validation runs only.
 *
 * A backtest or an optimization is one simulation of one entry rule and one exit rule, so the
 * run selector alone determines what is drawn. A walk-forward is not: every fold re-optimized
 * independently, and the parameters it settled on are shown beside the selector so that
 * switching folds is visibly switching strategies.
 */
function FoldSelector({
  count,
  value,
  onChange,
  report,
}: {
  count: number
  value: number | null
  onChange: (fold: number | null) => void
  report: ValidationResult | null
}) {
  const chosen = value === null ? null : report?.folds[value]

  return (
    <Card padding="sm" withBorder>
      <Group align="flex-end" gap="md">
        <Select
          allowDeselect={false}
          data={[
            { value: 'combined', label: foldLabel(null) },
            ...Array.from({ length: count }, (_unused, index) => ({
              value: String(index),
              label: `${foldLabel(index)} · ${percent(
                report?.folds[index]?.metrics?.totalReturnPct ?? null,
                { signed: true },
              )} out of sample`,
            })),
          ]}
          description="Every fold re-optimized independently, so each has its own parameters."
          label="Fold"
          onChange={(next) => onChange(next === 'combined' || next === null ? null : Number(next))}
          value={value === null ? 'combined' : String(value)}
          w={340}
        />
        {chosen && (
          <Group gap="xs" pb={8}>
            {Object.entries(chosen.parameters).map(([path, parameter]) => (
              <Badge key={path} size="sm" variant="light">
                {path.split('.').at(-1)} {parameter}
              </Badge>
            ))}
          </Group>
        )}
      </Group>
    </Card>
  )
}
