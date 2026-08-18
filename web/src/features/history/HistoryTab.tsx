import { useState } from 'react'
import { Alert, Loader, SegmentedControl, Stack, Text, Title } from '@mantine/core'
import { IconAlertTriangle } from '@tabler/icons-react'
import { useStrategyContext } from '../strategy/context'
import { useRuns } from '../runs/queries'
import { useDraft } from '../config/draft'
import { runKindLabel } from '../../lib/format'
import { EmptyState } from '../../components/EmptyState'
import { Timeline } from './Timeline'
import { ComparisonTable } from './ComparisonTable'
import { ProgressChart } from './ProgressChart'
import { useRunDetails, useVersions } from './queries'
import { availableKinds, buildRows, defaultKind, speakingRun } from './table'
import type { MetricKey } from './delta'
import type { RunDetailNarrowed } from '../../api/types'
import type { ComparableKind } from './delta'

/**
 * Version history and comparison (brief §2.6).
 *
 * Two things live here: a timeline of what was saved, and a table comparing what each save
 * achieved. The second is the one that can mislead, and the two warnings at the bottom are
 * permanent for that reason — they are not a disclaimer to be dismissed but the context that
 * makes the table readable at all.
 */
export function HistoryTab() {
  const strategy = useStrategyContext()
  const versions = useVersions(strategy.id)
  const runs = useRuns({ strategyId: strategy.id, limit: 200 })
  const draft = useDraft(strategy.id, strategy.head)

  const [chosenKind, setChosenKind] = useState<ComparableKind | null>(null)
  const [metric, setMetric] = useState<MetricKey>('returnPct')

  if (versions.isPending || runs.isPending) return <Loader size="sm" />

  if (versions.isError || !versions.data) {
    return (
      <Alert color="red" title="The history could not be loaded">
        {versions.error instanceof Error ? versions.error.message : 'The server did not answer.'}
      </Alert>
    )
  }

  const allRuns = runs.data?.runs ?? []
  const kinds = availableKinds(allRuns)
  const kind = chosenKind && kinds.includes(chosenKind) ? chosenKind : defaultKind(allRuns)

  return (
    <Stack gap="xl">
      <Stack gap="sm">
        <Title order={4}>Versions</Title>
        <Text c="dimmed" size="sm">
          Newest first. Nothing here is ever deleted or rewritten — this is the record of what you
          believed at each point, and restoring appends rather than rewinds.
        </Text>
        <Timeline editorDirty={draft.dirty} strategyId={strategy.id} versions={versions.data} />
      </Stack>

      <Stack gap="sm">
        <Title order={4}>Did the edits help?</Title>
        {kind === null ? (
          <EmptyState title="No succeeded runs yet">
            Nothing can be compared until at least one run has finished. Launch a backtest or a
            walk-forward and this table fills in, one row per version.
          </EmptyState>
        ) : (
          <Comparison
            kind={kind}
            kinds={kinds}
            metric={metric}
            onKind={setChosenKind}
            onMetric={setMetric}
            runs={allRuns}
            versions={versions.data}
          />
        )}
      </Stack>

      <Warnings />
    </Stack>
  )
}

function Comparison({
  versions,
  runs,
  kind,
  kinds,
  onKind,
  metric,
  onMetric,
}: {
  versions: ReturnType<typeof useVersions>['data'] & object
  runs: Parameters<typeof speakingRun>[0]
  kind: ComparableKind
  kinds: ComparableKind[]
  onKind: (next: ComparableKind) => void
  metric: MetricKey
  onMetric: (next: MetricKey) => void
}) {
  // One detail query per version's reporting run. The list endpoint's headline carries no
  // Sharpe, no fold win rate and no frame digest, and the last of those is what the
  // data-vintage caveat is built on.
  const selected = versions
    .map((version) => speakingRun(runs, version.version, kind)?.id)
    .filter((id): id is string => id !== undefined)

  const details = useRunDetails(selected)
  const byId = new Map<string, RunDetailNarrowed>()
  details.forEach((query, index) => {
    const id = selected[index]
    if (id && query.data) byId.set(id, query.data)
  })

  const rows = buildRows({ versions, runs, kind, details: byId })

  return (
    <Stack gap="md">
      <SegmentedControl
        data={kinds.map((entry) => ({ label: runKindLabel(entry), value: entry }))}
        onChange={(value) => onKind(value as ComparableKind)}
        value={kind}
        w="fit-content"
      />
      <ProgressChart metric={metric} onMetric={onMetric} rows={rows} />
      <ComparisonTable kind={kind} rows={rows} versions={versions} />
    </Stack>
  )
}

/**
 * The two permanent warnings (§2.6).
 *
 * Permanent, and not dismissible. Both describe biases that grow as the user uses this screen
 * more, so the moment they would be dismissed is the moment they start mattering. The second is
 * the reason the verdict and fold-win-rate columns exist at all: without them the table is a
 * leaderboard for overfitting, and a well-designed one.
 */
function Warnings() {
  return (
    <Stack gap="sm">
      <Alert
        color="orange"
        icon={<IconAlertTriangle size={16} />}
        title="Editing and re-running is itself a search"
      >
        <Text size="sm">
          Every version you tried and abandoned was a trial, and none of them are counted in the
          trial count that deflates the Sharpe ratio. A strategy on its twelfth version has been
          optimized far harder than its last run reports.
        </Text>
      </Alert>
      <Alert
        color="orange"
        icon={<IconAlertTriangle size={16} />}
        title="Later versions were chosen knowing how earlier ones did"
      >
        <Text size="sm">
          Every version here was tested on the same ticker over the same dates, so the edits were
          made with knowledge of how the previous ones performed on exactly that data. Walk-forward
          validation is the only thing on this screen that pushes back on that, which is why its
          verdict has a column of its own.
        </Text>
      </Alert>
    </Stack>
  )
}
