import { Button, Stack, Text } from '@mantine/core'
import { ChartCard } from './ChartCard'
import { EmptyState } from '../../components/EmptyState'
import { CROSSHAIR, GRID, SIGN, marks, percentAxis, percentValueAxis, zeroLine } from './options'
import { parameterDrift } from './annotations'
import { requires } from './state'
import { useMeta } from '../../api/metaContext'
import type { Interval, ValidationResult } from '../../lib/result'
import type { EChartsOption, LineSeriesOption } from 'echarts'

/**
 * Group D — would it survive contact with reality.
 *
 * Validation runs only, and **never hidden** for the others. A backtest's chart page ending
 * one group early would let an unvalidated strategy present as a validated one that simply had
 * less to say. The group is always on the page; on a backtest or optimization it holds the
 * explanation and the button.
 *
 * Nothing here recomputes a pass or a fail. The engine's checks are the verdict (spec §12.8),
 * and these charts draw the numbers underneath them.
 */
export function GroupD({
  report,
  onLaunchWalkForward,
}: {
  report: ValidationResult | null
  onLaunchWalkForward: () => void
}) {
  const meta = useMeta()

  if (report === null) {
    return (
      <EmptyState
        action={<Button onClick={onLaunchWalkForward}>Run a walk-forward</Button>}
        title="These checks need a walk-forward run"
      >
        A backtest says what one configuration did over one stretch of history. It cannot say
        whether the result survives being re-optimized on data it never saw, whether the Sharpe
        beats what a search of that many trials produces from nothing, or whether the optimum is a
        plateau or a spike. Only a walk-forward answers those, and this group stays here rather than
        disappearing so that a page without them does not look complete.
      </EmptyState>
    )
  }

  return (
    <Stack gap="md">
      <FoldReturnsChart report={report} />
      <ParameterDriftChart report={report} />
      <StabilityChart report={report} threshold={meta.meta.instability_threshold} />
      <CostLadderChart report={report} />
      <LuckChart report={report} significance={meta.meta.significance} />
      <IntervalsChart report={report} />
    </Stack>
  )
}

function FoldReturnsChart({ report }: { report: ValidationResult }) {
  const folds = report.folds
  const test = folds.map((fold) => fold.metrics?.totalReturnPct ?? null)
  const train = folds.map((fold) => fold.trainMetrics?.totalReturnPct ?? null)
  const present = test.filter((value): value is number => value !== null)
  const median = [...present].sort((left, right) => left - right)[present.length >> 1]

  const option: EChartsOption = {
    grid: GRID,
    tooltip: CROSSHAIR,
    legend: { data: ['Out of sample', 'In sample (train)'], top: 0 },
    xAxis: { type: 'category', data: folds.map((fold) => `Fold ${fold.index + 1}`) },
    yAxis: percentAxis('return'),
    series: [
      {
        name: 'In sample (train)',
        type: 'bar',
        barGap: '-100%',
        // Hollow and behind: the train bar is context, not a result, and it must not be
        // mistakable for the number that matters.
        itemStyle: { color: 'transparent', borderColor: SIGN.train, borderWidth: 2 },
        data: train,
      },
      {
        name: 'Out of sample',
        type: 'bar',
        barWidth: '40%',
        data: test.map((value) => ({
          value,
          itemStyle: { color: (value ?? 0) > 0 ? SIGN.positive : SIGN.negative },
        })),
        markLine: marks(median === undefined ? [{ yAxis: 0 }] : [{ yAxis: 0 }, { yAxis: median }]),
      },
    ],
  }

  return (
    <ChartCard
      badPicture="tall hollow train bars over short or red solid test bars — that is a curve fit, drawn."
      csv={{
        filename: 'fold_returns.csv',
        rows: [
          ['fold', 'test_return_pct', 'train_return_pct', 'trades'],
          ...folds.map((fold) => [
            fold.index + 1,
            fold.metrics?.totalReturnPct ?? null,
            fold.trainMetrics?.totalReturnPct ?? null,
            fold.metrics?.totalTrades ?? null,
          ]),
        ],
      }}
      height={280}
      id="chart-folds"
      option={option}
      question="Did it make money on the slices it was never fitted to?"
      state={requires(folds.length > 0, 'The fold results')}
      title="Fold returns"
      term="fold_returns"
      footer={
        <Text c="dimmed" size="xs">
          Profitable in {report.profitableFolds ?? 0} of {folds.length} folds. The hollow bar behind
          each is that fold&rsquo;s training return — the gap between them is the cheapest
          overfitting diagnostic there is.
        </Text>
      }
    />
  )
}

function ParameterDriftChart({ report }: { report: ValidationResult }) {
  const drift = parameterDrift(report.folds.map((fold) => fold.parameters))
  const labels = report.folds.map((fold) => `Fold ${fold.index + 1}`)

  const option: EChartsOption = {
    grid: GRID,
    tooltip: { trigger: 'axis' },
    legend: { data: drift.map((line) => line.path), top: 0, type: 'scroll' },
    xAxis: { type: 'category', data: labels },
    yAxis: {
      type: 'value',
      min: 0,
      max: 1,
      name: 'normalised within each parameter’s own range',
      nameLocation: 'middle',
      nameGap: 46,
      axisLabel: {
        formatter: (value: number) => (value === 0 ? 'low' : value === 1 ? 'high' : ''),
      },
    },
    series: drift.map((line) => ({
      name: line.path,
      type: 'line' as const,
      data: line.normalised,
      symbolSize: 8,
      lineStyle: line.constant ? { type: 'dotted' as const } : {},
      connectNulls: false,
    })),
  }

  return (
    <ChartCard
      badPicture="a line zig-zagging from top to bottom — that parameter is fitting noise, not finding an optimum."
      csv={{
        filename: 'parameter_drift.csv',
        rows: [['parameter', ...labels], ...drift.map((line) => [line.path, ...line.values])],
      }}
      height={300}
      id="chart-drift"
      option={option}
      question="Did the folds agree on where the optimum is?"
      state={requires(drift.length > 0, 'Per-fold parameters')}
      title="Parameter drift across folds"
      term="parameter_drift"
      footer={
        <Text c="dimmed" size="xs">
          Each parameter is rescaled within its own range across the folds, because a 200-bar window
          and a 2.5× multiplier cannot share an axis in their own units — what survives the
          rescaling is the shape. Dotted lines are parameters every fold settled on identically.
          This is the only chart in the app that says whether the folds agreed.
        </Text>
      }
    />
  )
}

function StabilityChart({ report, threshold }: { report: ValidationResult; threshold: number }) {
  const stability = report.stability
  const points = stability?.points ?? []
  const paths = [...new Set(points.map((point) => point.path))]
  const multipliers = [...new Set(points.map((point) => point.multiplier))]
    .filter((value): value is number => value !== null)
    .sort((left, right) => left - right)

  const baseline = stability?.baselineScore ?? null
  const fragile = new Set(stability?.fragileParameters ?? [])

  const option: EChartsOption = {
    grid: GRID,
    tooltip: { trigger: 'axis' },
    legend: { data: paths, top: 0, type: 'scroll' },
    xAxis: {
      type: 'category',
      // `StabilityPoint.multiplier` is a *fractional change*, applied as `value * (1 + m)`, so
      // -0.2 is a 20% reduction. Read as a multiple instead, it labelled that column "-120%":
      // an axis stating a perturbation five times larger than the one the engine performed,
      // on the one chart whose whole subject is how far a parameter can move.
      data: multipliers.map((value) => `${value > 0 ? '+' : ''}${Math.round(value * 100)}%`),
      name: 'perturbation',
      nameLocation: 'middle',
      nameGap: 26,
    },
    yAxis: { type: 'value', name: 'objective', nameLocation: 'middle', nameGap: 46, scale: true },
    series: paths.map((path) => {
      const line: LineSeriesOption = {
        name: path,
        type: 'line',
        symbolSize: 8,
        data: multipliers.map(
          (multiplier) =>
            points.find((point) => point.path === path && point.multiplier === multiplier)?.score ??
            null,
        ),
      }
      // Fragile parameters are labelled on the chart itself, not only in the legend: a legend
      // entry is a lookup, and the point of this chart is what you see without performing one.
      if (fragile.has(path)) {
        line.endLabel = { show: true, formatter: `${path} — fragile`, color: SIGN.negative }
      }
      if (baseline !== null) {
        line.markLine = marks([{ yAxis: baseline }, { yAxis: baseline * (1 - threshold) }], {
          dashed: true,
          label: 'baseline',
        })
      }
      return line
    }),
  }

  return (
    <ChartCard
      badPicture="a peak at the centre falling away on both sides — a needle that will not exist next year."
      csv={{
        filename: 'stability.csv',
        rows: [
          ['parameter', 'multiplier', 'value', 'score', 'degradation'],
          ...points.map((point) => [
            point.path,
            point.multiplier,
            point.value,
            point.score,
            point.degradation,
          ]),
        ],
      }}
      height={300}
      id="chart-stability"
      option={option}
      question="Is the optimum a plateau you could land near, or a spike you had to hit exactly?"
      state={requires(points.length > 0, 'The stability surface')}
      title="Parameter stability plateau"
      term="stability_plateau"
      footer={
        <Text c="dimmed" size="xs">
          The lower dashed line is {Math.round(threshold * 100)}% below the baseline objective — a
          parameter dipping under it on a 10% nudge is flagged fragile. In a point estimate a
          plateau and a spike look identical, which is why this chart exists.
        </Text>
      }
    />
  )
}

function CostLadderChart({ report }: { report: ValidationResult }) {
  const scenarios = report.costs?.scenarios ?? []
  const breakEven = report.costs?.breakEvenMultiple ?? null

  const option: EChartsOption = {
    grid: GRID,
    tooltip: CROSSHAIR,
    xAxis: {
      type: 'category',
      data: scenarios.map((scenario) => `${scenario.multiple ?? '?'}×`),
      name: 'cost multiple',
      nameLocation: 'middle',
      nameGap: 26,
    },
    yAxis: percentAxis('return'),
    series: [
      {
        type: 'line',
        symbolSize: 9,
        lineStyle: { width: 2, color: SIGN.strategy },
        itemStyle: { color: SIGN.strategy },
        data: scenarios.map((scenario) => scenario.metrics?.totalReturnPct ?? null),
        markLine: marks([{ yAxis: 0 }], { label: 'break even' }),
      },
    ],
  }

  return (
    <ChartCard
      badPicture="the line crossing zero before 2× — that edge belongs to the broker."
      csv={{
        filename: 'cost_ladder.csv',
        rows: [
          ['multiple', 'slippage_pct', 'commission_pct', 'return_pct'],
          ...scenarios.map((scenario) => [
            scenario.multiple,
            scenario.slippagePct,
            scenario.commissionPct,
            scenario.metrics?.totalReturnPct ?? null,
          ]),
        ],
      }}
      height={260}
      id="chart-costs"
      option={option}
      question="How much more could trading cost before this stops making money?"
      state={requires(scenarios.length > 0, 'The cost sensitivity scenarios')}
      title="Cost ladder"
      term="cost_ladder"
      footer={
        <Text c="dimmed" size="xs">
          {breakEven === null
            ? 'The engine reported no break-even multiple: the return did not cross zero within the range it tested.'
            : `Break-even at ${breakEven.toFixed(2)}× the configured costs. Below 2× means the result does not survive costs being twice what you assumed.`}
        </Text>
      }
    />
  )
}

function LuckChart({ report, significance }: { report: ValidationResult; significance: number }) {
  const deflated = report.deflated
  const overfitting = report.overfitting
  const observed = deflated?.observed ?? null
  const luck = deflated?.threshold ?? null

  const option: EChartsOption = {
    grid: { ...GRID, top: 40 },
    tooltip: { trigger: 'item' },
    xAxis: {
      type: 'value',
      name: 'Sharpe',
      nameLocation: 'middle',
      nameGap: 26,
      min: 0,
      // The axis has to contain the threshold, and ECharts does not extend an axis to fit a
      // mark line. Left to the data it stopped just past the observed value and drew the
      // threshold off-chart — deleting the one comparison the chart exists to make while
      // leaving a confident red bar behind, which reads as a result rather than as a failure.
      max: Math.max(observed ?? 0, luck ?? 0) * 1.15,
    },
    yAxis: { type: 'category', data: ['Sharpe'], axisLabel: { show: false } },
    series: [
      {
        type: 'bar',
        barWidth: 22,
        itemStyle: {
          color:
            observed !== null && luck !== null && observed > luck ? SIGN.positive : SIGN.negative,
        },
        data: [observed],
        ...(luck === null
          ? {}
          : {
              markLine: marks([{ xAxis: luck }], {
                dashed: true,
                label: `luck threshold ${luck.toFixed(2)}`,
              }),
            }),
      },
    ],
  }

  const probability = deflated?.probability ?? null
  const pbo = overfitting?.probability ?? null

  return (
    <ChartCard
      badPicture="the bar ending to the left of the dashed line — the observed Sharpe is inside what luck produces."
      height={140}
      id="chart-luck"
      option={option}
      question="Is this Sharpe better than what a search of this many trials produces from no edge at all?"
      state={requires(observed !== null, 'The deflated Sharpe')}
      title="Deflated Sharpe"
      term="deflated_sharpe"
      footer={
        <Stack gap={4}>
          <Text size="xs">
            P = {probability === null ? 'n/a' : probability.toFixed(2)} against a bar of{' '}
            {significance.toFixed(2)}, over {report.trials ?? 0} trials.{' '}
            {deflated?.isSignificant === true
              ? 'The engine judged this significant.'
              : 'The engine did not judge this significant.'}
          </Text>
          <Text size="xs">
            Probability of backtest overfitting:{' '}
            {pbo === null ? 'n/a' : `${pbo.toFixed(2)} against a hard line at 0.50`}.{' '}
            {pbo !== null &&
              (pbo > 0.5
                ? 'Above 0.50 the selection procedure is doing worse than picking at random.'
                : 'Below 0.50 the selection procedure beats picking at random.')}
          </Text>
        </Stack>
      }
    />
  )
}

/**
 * The two bootstrap intervals, on **separate** scales.
 *
 * They are not comparable quantities. `mean_return_interval` is the mean return of a single
 * *bar* and lands around ±0.1%; `total_return_interval` is the compounded return over the whole
 * out-of-sample stretch and lands in the hundreds of percent. Sharing an axis collapses the
 * first to an invisible tick beside the second, and §5.7 forbids the usual escape of a second
 * y-axis. Two charts, each with its own axis and its own unit named in the title.
 *
 * Both arrive as **fractions**, not percentages — `block_bootstrap_interval` works on raw
 * per-bar returns and the CLI multiplies by 100 to print them. Plotted raw against an axis
 * formatted with `%`, a total return of 209% read as "2%", which is the same order-of-magnitude
 * error the config editor's commission hint exists to prevent, in the chart §5.6 calls one of
 * the most decisive on the page.
 */
function IntervalsChart({ report }: { report: ValidationResult }) {
  return (
    <>
      <OneInterval
        digits={1}
        id="chart-interval-total"
        interval={report.totalReturnInterval}
        question="How much of the out-of-sample return could be chance?"
        title="Total return, 95% interval"
      />
      <OneInterval
        digits={3}
        id="chart-interval-mean"
        interval={report.meanReturnInterval}
        question="Is the average bar’s return distinguishable from zero?"
        title="Mean return per bar, 95% interval"
      />
    </>
  )
}

function OneInterval({
  id,
  title,
  question,
  interval,
  digits,
}: {
  id: string
  title: string
  question: string
  interval: Interval | null
  /** Decimals on the axis and in the sentence. A per-bar return needs more than a total does. */
  digits: number
}) {
  const say = (value: number) => `${value > 0 ? '+' : ''}${value.toFixed(digits)}%`
  const pct = (value: number | null) => (value === null ? 0 : value * 100)
  const low = pct(interval?.low ?? null)
  const high = pct(interval?.high ?? null)
  const point = pct(interval?.point ?? null)
  const straddles = interval?.excludesZero === false

  // A thick two-point line, not a stacked bar. The invisible-offset-plus-span trick breaks the
  // moment `low` is negative — which is exactly when this chart matters — and drew an interval
  // running from −0.33 to +2.09 entirely to the right of zero, directly above a caption saying
  // it straddled zero. The words were right and the picture contradicted them, and a picture is
  // the more persuasive of the two.
  const option: EChartsOption = {
    grid: { ...GRID, left: 70, top: 12, bottom: 34 },
    tooltip: { trigger: 'item' },
    xAxis: percentValueAxis(digits),
    yAxis: { type: 'category', data: [''], axisLabel: { show: false } },
    series: [
      {
        type: 'line',
        symbol: 'none',
        lineStyle: { width: 18, color: straddles ? SIGN.negative : SIGN.strategy, opacity: 0.3 },
        data: [
          [low, 0],
          [high, 0],
        ],
        markLine: zeroLine('x'),
      },
      {
        type: 'scatter',
        symbol: 'rect',
        symbolSize: [4, 28],
        itemStyle: { color: straddles ? SIGN.negative : SIGN.strategy },
        data: [[point, 0]],
      },
    ],
  }

  return (
    <ChartCard
      badPicture="the bar crossing the zero line — the figure is not distinguishable from luck."
      csv={{
        filename: `${id}.csv`,
        rows: [
          ['low_pct', 'point_pct', 'high_pct', 'confidence', 'excludes_zero'],
          [low, point, high, interval?.confidence ?? null, String(interval?.excludesZero ?? '')],
        ],
      }}
      height={150}
      id={id}
      option={option}
      question={question}
      state={requires(interval !== null, 'The bootstrap interval')}
      title={title}
      footer={
        <Text c={straddles ? 'orange' : 'dimmed'} size="xs">
          {say(point)}, somewhere between {say(low)} and {say(high)}.{' '}
          {straddles
            ? 'The interval contains zero, so this figure is not distinguishable from luck.'
            : 'The interval excludes zero.'}
        </Text>
      }
    />
  )
}
