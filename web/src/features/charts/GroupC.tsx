import { Stack, Text } from '@mantine/core'
import { ChartCard } from './ChartCard'
import { CROSSHAIR, GRID, SIGN, percentAxis, timeAxis, zeroLine } from './options'
import { yearsOf, type MonthlyCell, type Point } from './series'
import { firstProblem, requires, type ChartState } from './state'
import type { YearReturn } from '../../lib/result'
import type { EChartsOption } from 'echarts'

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/**
 * Group C — did it hold up over time.
 *
 * On a validation run in Combined view this group is where the brief and the engine's own
 * discipline meet. §5.1 says Group C stitches fold windows chronologically, and for the
 * monthly grid it does: the windows are contiguous and non-overlapping, so a month belongs to
 * one fold, and the handful of boundary months are marked rather than combined.
 *
 * The other two charts aggregate over twelve months and over a calendar year, and *every* such
 * window crosses a fold boundary in a typical six-fold run. Compounding across one would state
 * a yearly return for a strategy that never existed — the folds ran different parameters. They
 * are refused in Combined view and point at the fold selector, which is a narrower reading of
 * §5.1 than its wording, and the only one that does not invent a number.
 */
export function GroupC({
  state,
  monthly,
  yearly,
  benchmarkYearly,
  rolling12m,
  worstRolling12m,
  combined,
  csvHref,
}: {
  state: ChartState
  monthly: MonthlyCell[]
  yearly: YearReturn[]
  benchmarkYearly: YearReturn[] | null
  rolling12m: Point[]
  worstRolling12m: number | null
  /** True for a walk-forward showing every fold at once. */
  combined: boolean
  csvHref: (name: string) => string
}) {
  const acrossFolds: ChartState = {
    kind: 'needs-a-fold',
    what:
      'This chart aggregates over a year, and every year in a walk-forward crosses a fold boundary — ' +
      'the folds ran different parameters, so compounding across one would describe a strategy that ' +
      'was never traded. Select a fold above.',
  }

  return (
    <Stack gap="md">
      <MonthlyHeatmap
        combined={combined}
        href={csvHref('monthly_returns')}
        monthly={monthly}
        state={firstProblem(state, requires(monthly.length > 0, 'Monthly returns'))}
      />
      <YearlyChart
        benchmarkYearly={benchmarkYearly}
        state={
          combined
            ? acrossFolds
            : firstProblem(state, requires(yearly.length > 0, 'Yearly returns'))
        }
        yearly={yearly}
      />
      <RollingChart
        href={csvHref('rolling_12m_return')}
        rolling12m={rolling12m}
        state={
          combined
            ? {
                kind: 'needs-a-fold',
                what:
                  'A trailing twelve-month window crosses fold boundaries by construction, and the ' +
                  'folds ran different parameters. Select a fold above.',
              }
            : firstProblem(
                state,
                requires(rolling12m.length > 0, 'The rolling twelve-month series'),
                rolling12m.some((point) => point.value !== 0)
                  ? { kind: 'ready' }
                  : {
                      kind: 'too-short',
                      what: 'This window is under twelve months, so no trailing year has completed',
                    },
              )
        }
        worst={worstRolling12m}
      />
    </Stack>
  )
}

function MonthlyHeatmap({
  monthly,
  combined,
  state,
  href,
}: {
  monthly: MonthlyCell[]
  combined: boolean
  state: ChartState
  href: string
}) {
  const years = yearsOf(monthly)
  const drawn = monthly.filter((cell) => cell.inMarket && !cell.seam)
  const idle = monthly.filter((cell) => !cell.inMarket && !cell.seam)
  const seams = monthly.filter((cell) => cell.seam)
  const extent = Math.max(1, ...drawn.map((cell) => Math.abs(cell.value)))

  const option: EChartsOption = {
    grid: { ...GRID, left: 56, right: 90 },
    tooltip: { trigger: 'item' },
    xAxis: { type: 'category', data: MONTHS, splitArea: { show: true } },
    yAxis: { type: 'category', data: years.map(String), splitArea: { show: true } },
    visualMap: {
      min: -extent,
      max: extent,
      calculable: true,
      orient: 'vertical',
      right: 0,
      top: 'middle',
      // White at exactly zero, so a flat month is not coloured as a small win.
      inRange: { color: [SIGN.negative, '#ffffff', SIGN.positive] },
      text: ['gain', 'loss'],
    },
    series: [
      {
        type: 'heatmap',
        data: drawn.map((cell) => [
          cell.monthIndex - 1,
          years.indexOf(cell.year),
          Number(cell.value.toFixed(2)),
        ]),
        label: { show: false },
      },
      {
        // Held nothing, which is a different fact from "ended flat". Drawn as a marked cell
        // rather than as a zero, because a heatmap that colours both the same overstates how
        // consistently the strategy was working.
        type: 'heatmap',
        data: idle.map((cell) => [cell.monthIndex - 1, years.indexOf(cell.year), 0]),
        itemStyle: { color: '#f1f3f5', borderColor: '#dee2e6', borderWidth: 1 },
        label: { show: true, formatter: '·', color: '#adb5bd' },
        tooltip: { formatter: 'no position held this month' },
        silent: false,
      },
      ...(seams.length > 0
        ? [
            {
              type: 'heatmap' as const,
              data: seams.map((cell) => [cell.monthIndex - 1, years.indexOf(cell.year), 0]),
              itemStyle: { color: '#fff4e6', borderColor: SIGN.benchmark, borderWidth: 1 },
              label: { show: true, formatter: '⁄', color: SIGN.benchmark },
              tooltip: { formatter: 'split across two folds — no single return for this month' },
            },
          ]
        : []),
    ],
  }

  return (
    <ChartCard
      badPicture="two dark green cells carrying a decade of pale ones."
      csv={{ href }}
      height={Math.max(200, years.length * 34 + 90)}
      id="chart-monthly"
      option={option}
      question="Was the strategy working consistently, or only in a few months?"
      state={state}
      title="Monthly returns"
      footer={
        <Text c="dimmed" size="xs">
          A month with no position is marked · rather than coloured 0% — flat and absent are
          different facts.
          {combined &&
            seams.length > 0 &&
            ` ${seams.length} month${seams.length === 1 ? '' : 's'} marked ⁄ fall across a fold boundary: two folds each contributed part of them, under different parameters, so no single return exists for those months.`}
        </Text>
      }
    />
  )
}

function YearlyChart({
  yearly,
  benchmarkYearly,
  state,
}: {
  yearly: YearReturn[]
  benchmarkYearly: YearReturn[] | null
  state: ChartState
}) {
  const years = [
    ...new Set([...yearly, ...(benchmarkYearly ?? [])].map((entry) => entry.year)),
  ].sort((left, right) => left - right)

  const at = (source: YearReturn[], year: number) =>
    source.find((entry) => entry.year === year)?.returnPct ?? null

  const option: EChartsOption = {
    grid: GRID,
    tooltip: CROSSHAIR,
    legend: { data: ['Strategy', 'Buy and hold'], top: 0 },
    xAxis: { type: 'category', data: years.map(String) },
    yAxis: percentAxis('return'),
    series: [
      {
        name: 'Strategy',
        type: 'bar',
        itemStyle: { color: SIGN.strategy },
        data: years.map((year) => at(yearly, year)),
        markLine: zeroLine(),
      },
      {
        name: 'Buy and hold',
        type: 'bar',
        // Hollow rather than a second solid colour: sign is read off the zero line, and the
        // two series are told apart by fill as well as by hue.
        itemStyle: { color: 'transparent', borderColor: SIGN.benchmark, borderWidth: 2 },
        data: years.map((year) => (benchmarkYearly ? at(benchmarkYearly, year) : null)),
      },
    ],
  }

  return (
    <ChartCard
      badPicture="one tall bar and the rest near zero — all the profit came from one year."
      csv={{
        filename: 'yearly_returns.csv',
        rows: [
          ['year', 'strategy_pct', 'benchmark_pct'],
          ...years.map((year) => [
            year,
            at(yearly, year),
            benchmarkYearly ? at(benchmarkYearly, year) : null,
          ]),
        ],
      }}
      height={280}
      id="chart-yearly"
      option={option}
      question="Was the return spread across years, or did one year carry it?"
      state={state}
      title="Yearly returns against buy-and-hold"
      footer={
        benchmarkYearly === null && (
          <Text c="dimmed" size="xs">
            This run recorded no per-year benchmark, so only the strategy is drawn. A hollow bar is
            not missing — there is nothing to put in it.
          </Text>
        )
      }
    />
  )
}

function RollingChart({
  rolling12m,
  worst,
  state,
  href,
}: {
  rolling12m: Point[]
  worst: number | null
  state: ChartState
  href: string
}) {
  // The leading year is structurally zero — no trailing twelve months has completed yet — and
  // plotting it would draw a flat run that reads as twelve months of no return.
  const drawn = rolling12m.filter((point) => point.value !== 0)
  const minimum = drawn.reduce<Point | null>(
    (lowest, point) => (lowest === null || point.value < lowest.value ? point : lowest),
    null,
  )

  const option: EChartsOption = {
    grid: GRID,
    tooltip: CROSSHAIR,
    xAxis: timeAxis(),
    yAxis: percentAxis('trailing 12m'),
    series: [
      {
        name: 'Trailing 12 months',
        type: 'line',
        showSymbol: false,
        lineStyle: { width: 2, color: SIGN.strategy },
        itemStyle: { color: SIGN.strategy },
        data: drawn.map((point) => [point.date, point.value]),
        markLine: zeroLine(),
        ...(minimum
          ? {
              markPoint: {
                symbolSize: 46,
                itemStyle: { color: SIGN.negative },
                data: [
                  {
                    name: 'worst twelve months',
                    coord: [minimum.date, minimum.value],
                    value: `${minimum.value.toFixed(1)}%`,
                  },
                ],
              },
            }
          : {}),
      },
    ],
  }

  return (
    <ChartCard
      badPicture="a curve drifting toward zero over the years — an edge that decayed."
      csv={{ href }}
      height={280}
      id="chart-rolling"
      option={option}
      question="If you had started at the worst possible moment, what would your first year have looked like?"
      state={state}
      title="Rolling twelve-month return"
      footer={
        <Text c="dimmed" size="xs">
          The marked minimum is the worst twelve months this strategy had
          {worst !== null && `: ${worst.toFixed(1)}%`}. Every aggregate on the other screens
          averages that away.
        </Text>
      }
    />
  )
}
