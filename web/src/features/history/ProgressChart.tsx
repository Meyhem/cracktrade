import { Card, Group, SegmentedControl, Stack, Text } from '@mantine/core'
import { EChart } from '../charts/EChart'
import { CROSSHAIR, GRID, SIGN, marks, percentAxis } from '../charts/options'
import type { MetricKey } from './delta'
import type { BuiltRow } from './table'
import type { EChartsOption } from 'echarts'

/**
 * The selected metric across versions — the "am I making progress or wandering" view.
 *
 * Drawn as a **step** line, not a smooth one. A smooth line between v2 and v5 implies the
 * strategy passed through the values in between, and it did not: there were three edits, each
 * discrete, some with no run at all. A step says "it was this, then it was that", which is what
 * happened.
 *
 * Versions with no run of the selected kind are gaps, not interpolated points. ECharts joins
 * across nulls only if asked to, and it is not asked to here: a line drawn through a version
 * that was never run asserts a measurement nobody made.
 */
const METRICS: { key: MetricKey; label: string; unit: 'pct' | 'ratio' }[] = [
  { key: 'returnPct', label: 'Return', unit: 'pct' },
  { key: 'excessPp', label: 'vs buy-and-hold', unit: 'pct' },
  { key: 'maxDrawdownPct', label: 'Max drawdown', unit: 'pct' },
  { key: 'sharpe', label: 'Sharpe', unit: 'ratio' },
  { key: 'foldWinRate', label: 'Folds won', unit: 'pct' },
]

export function ProgressChart({
  rows,
  metric,
  onMetric,
}: {
  rows: BuiltRow[]
  metric: MetricKey
  onMetric: (next: MetricKey) => void
}) {
  const chosen = METRICS.find((entry) => entry.key === metric) ?? METRICS[0]!
  const values = rows.map((row) => readValue(row, metric))
  const drawable = values.filter((value) => value !== null).length

  const option: EChartsOption = {
    grid: { ...GRID, left: 56 },
    tooltip: CROSSHAIR,
    xAxis: {
      type: 'category',
      data: rows.map((row) => `v${row.version}`),
      axisLabel: { interval: 0 },
    },
    yAxis:
      chosen.unit === 'pct'
        ? percentAxis()
        : {
            type: 'value',
            min: (value: { min: number; max: number }) => Math.min(0, value.min),
            max: (value: { min: number; max: number }) => Math.max(0, value.max),
          },
    series: [
      {
        name: chosen.label,
        type: 'line',
        step: 'end',
        // No `connectNulls`: a version with no run is a hole in the record, and drawing
        // through it would claim a measurement that does not exist.
        connectNulls: false,
        symbolSize: 8,
        lineStyle: { width: 2, color: SIGN.strategy },
        itemStyle: { color: SIGN.strategy },
        data: values,
        markLine: marks([{ yAxis: 0 }]),
      },
    ],
  }

  return (
    <Card padding="md" withBorder>
      <Stack gap="xs">
        <Group align="flex-start" justify="space-between" wrap="wrap">
          <Stack gap={2}>
            <Text fw={600}>{chosen.label} across versions</Text>
            <Text c="dimmed" size="xs">
              Am I making progress, or wandering? A line that jumps around says the edits are
              finding noise rather than an edge.
            </Text>
          </Stack>
          <SegmentedControl
            data={METRICS.map((entry) => ({ label: entry.label, value: entry.key }))}
            onChange={(value) => onMetric(value as MetricKey)}
            size="xs"
            value={metric}
          />
        </Group>

        {drawable === 0 ? (
          <Text c="dimmed" py="xl" size="sm" ta="center">
            No version has a run of this kind reporting {chosen.label.toLowerCase()}.
          </Text>
        ) : (
          <EChart ariaLabel={`${chosen.label} across versions`} height={200} option={option} />
        )}
      </Stack>
    </Card>
  )
}

function readValue(row: BuiltRow, metric: MetricKey): number | null {
  const figures = row.run?.figures
  if (!figures) return null
  const value = figures[metric]
  if (value === null) return null
  return metric === 'foldWinRate' ? value * 100 : value
}
