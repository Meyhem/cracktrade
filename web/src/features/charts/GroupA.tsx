import { useState } from 'react'
import { Group, Stack, Switch, Text } from '@mantine/core'
import { ChartCard } from './ChartCard'
import { explanationOf } from '../../lib/glossary'
import {
  CROSSHAIR,
  GRID,
  MARKABLE_FILLED_BARS,
  SIGN,
  filledMarkData,
  filledMarks,
  marks,
  moneyAxis,
  percentAxis,
  timeAxis,
} from './options'
import { longestUnderwater } from './annotations'
import { inPositionSpans, type Point } from './series'
import { READY, firstProblem, requires, type ChartState } from './state'
import type { Trade } from '../../lib/result'
import type { EChartsOption, YAXisComponentOption } from 'echarts'

/**
 * Group A — what the strategy actually did.
 *
 * Charts 1–3 share an x-axis, a crosshair and one zoom range: they are a single instrument,
 * and reading a drawdown against the wrong stretch of the equity curve is how a user concludes
 * the wrong thing from three correct charts.
 */
const SHARED = 'group-a'

export function GroupA({
  state,
  equity,
  benchmarkEquity,
  drawdown,
  close,
  filled,
  trades,
  csvHref,
}: {
  state: ChartState
  equity: Point[]
  benchmarkEquity: Point[]
  drawdown: Point[]
  close: Point[]
  filled: string[]
  trades: Trade[]
  csvHref: (name: string) => string
}) {
  const [logScale, setLogScale] = useState(false)

  return (
    <Stack gap="md">
      <EquityChart
        benchmarkEquity={benchmarkEquity}
        equity={equity}
        filled={filled}
        href={csvHref('equity')}
        logScale={logScale}
        onLogScale={setLogScale}
        state={firstProblem(state, requires(equity.length > 0, 'The equity curve'))}
      />
      <DrawdownChart
        drawdown={drawdown}
        filled={filled}
        href={csvHref('drawdown')}
        state={firstProblem(state, requires(drawdown.length > 0, 'The drawdown series'))}
      />
      <PriceChart
        close={close}
        filled={filled}
        href={csvHref('close')}
        state={firstProblem(
          exceptTradeFloor(state),
          requires(close.length > 0, 'The price series'),
        )}
        trades={trades}
      />
    </Stack>
  )
}

/**
 * The trade floor, and only the trade floor, waived.
 *
 * §5.2 makes the price chart the sole exception to the floor: it shows individual events
 * rather than an estimate, and looking at six trades one at a time is a reasonable thing to do.
 *
 * Every *other* refusal still applies to it. Ignoring the state wholesale — which this did
 * until a walk-forward's combined view was looked at — turned "these folds ran different
 * configurations, pick one" into "the price series was not recorded for this run", which is
 * not a milder way of saying the same thing. It is a false statement about the run, and it
 * points the reader at a missing feature instead of at the fold selector directly above.
 */
function exceptTradeFloor(state: ChartState): ChartState {
  return state.kind === 'too-few-trades' ? READY : state
}

function EquityChart({
  equity,
  benchmarkEquity,
  filled,
  logScale,
  onLogScale,
  state,
  href,
}: {
  equity: Point[]
  benchmarkEquity: Point[]
  filled: string[]
  logScale: boolean
  onLogScale: (next: boolean) => void
  state: ChartState
  href: string
}) {
  const filledLine = filledMarks(filled)
  // On a decade of history a linear axis makes the early years unreadable; the toggle is
  // labelled and defaults to linear, because a log axis flatters a compounding series to
  // anyone who has not noticed which one they are looking at.
  const logMoneyAxis = (): YAXisComponentOption => ({
    type: 'log',
    axisLabel: { formatter: (value: number) => `$${Math.round(value).toLocaleString()}` },
  })

  const option: EChartsOption = {
    grid: GRID,
    tooltip: CROSSHAIR,
    legend: { data: ['Strategy', 'Buy and hold'], top: 0 },
    xAxis: timeAxis(),
    // One axis, both series, same starting capital. On separate axes a strategy that
    // returned 80% while the ticker returned 200% looks like a success.
    yAxis: logScale ? logMoneyAxis() : moneyAxis(),
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 18, bottom: 8 }],
    series: [
      {
        name: 'Strategy',
        type: 'line',
        showSymbol: false,
        lineStyle: { width: 2, color: SIGN.strategy },
        itemStyle: { color: SIGN.strategy },
        data: equity.map((point) => [point.date, point.value]),
        ...(filledLine ? { markLine: filledLine } : {}),
      },
      {
        name: 'Buy and hold',
        type: 'line',
        showSymbol: false,
        // Dashed, not merely a different colour: the two series are told apart by line style
        // as well as by hue, and the legend labels both.
        lineStyle: { width: 2, type: 'dashed', color: SIGN.benchmark },
        itemStyle: { color: SIGN.benchmark },
        data: benchmarkEquity.map((point) => [point.date, point.value]),
      },
    ],
  }

  return (
    <ChartCard
      badPicture="the dashed benchmark line finishing above the solid one."
      csv={{ href }}
      group={SHARED}
      height={340}
      id="chart-equity"
      option={option}
      question="Did this strategy beat simply owning the ticker over the same bars?"
      state={state}
      title="Equity against buy-and-hold"
      term="equity_curve"
      footer={
        <Group justify="space-between">
          <Switch
            checked={logScale}
            description={explanationOf('log_scale')}
            label="Logarithmic scale"
            onChange={(event) => onLogScale(event.currentTarget.checked)}
            size="xs"
          />
          <FilledNote count={filled.length} />
        </Group>
      }
    />
  )
}

function DrawdownChart({
  drawdown,
  filled,
  state,
  href,
}: {
  drawdown: Point[]
  filled: string[]
  state: ChartState
  href: string
}) {
  const worst = longestUnderwater(drawdown)
  const option: EChartsOption = {
    grid: GRID,
    tooltip: CROSSHAIR,
    xAxis: timeAxis(),
    yAxis: percentAxis('below peak'),
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 18, bottom: 8 }],
    series: [
      {
        name: 'Drawdown',
        type: 'line',
        showSymbol: false,
        areaStyle: { color: SIGN.negative, opacity: 0.2 },
        lineStyle: { width: 1, color: SIGN.negative },
        itemStyle: { color: SIGN.negative },
        data: drawdown.map((point) => [point.date, point.value]),
        // Zero, the filled bars, and the edges of the longest valley — the widest drawdown
        // matters more than the deepest, and it is invisible without its own marks.
        markLine: marks([
          { yAxis: 0 },
          ...filledMarkData(filled),
          ...(worst ? [{ xAxis: worst.from }, { xAxis: worst.to }] : []),
        ]),
      },
    ],
  }

  return (
    <ChartCard
      badPicture="a wide flat valley — years spent below a previous high is what makes people quit."
      csv={{ href }}
      group={SHARED}
      height={220}
      id="chart-drawdown"
      option={option}
      question="How far below its previous best did the account sit, and for how long?"
      state={state}
      title="Drawdown"
      term="drawdown"
      footer={
        worst && (
          <Text c="dimmed" size="xs">
            Longest stretch under water: {worst.days.toLocaleString()} days, {worst.from} to{' '}
            {worst.to}
            {worst.recovered
              ? '.'
              : ' — still under water at the last bar, so that length is a floor, not a total.'}
          </Text>
        )
      }
    />
  )
}

function PriceChart({
  close,
  filled,
  trades,
  state,
  href,
}: {
  close: Point[]
  filled: string[]
  trades: Trade[]
  state: ChartState
  href: string
}) {
  const spans = inPositionSpans(trades)
  const last = close.at(-1)?.date
  const filledLine = filledMarks(filled)

  const option: EChartsOption = {
    grid: GRID,
    tooltip: CROSSHAIR,
    xAxis: timeAxis(),
    yAxis: moneyAxis(),
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 18, bottom: 8 }],
    series: [
      {
        name: 'Close',
        type: 'line',
        showSymbol: false,
        lineStyle: { width: 1.5, color: SIGN.neutral },
        itemStyle: { color: SIGN.neutral },
        data: close.map((point) => [point.date, point.value]),
        ...(filledLine ? { markLine: filledLine } : {}),
        // Shading every in-position stretch turns `exposure_pct` into something you can see
        // rather than a percentage you have to trust. An open position has no right edge, so
        // its band runs to the last bar and is drawn at a different opacity.
        markArea: {
          silent: true,
          data: spans.map((span) => [
            {
              xAxis: span.from,
              itemStyle: { color: SIGN.strategy, opacity: span.to === null ? 0.06 : 0.12 },
            },
            { xAxis: span.to ?? last ?? span.from },
          ]),
        },
      },
      {
        name: 'Entries',
        type: 'scatter',
        symbol: 'triangle',
        symbolSize: 9,
        itemStyle: { color: SIGN.positive },
        data: trades
          .filter((trade) => trade.entryDate && trade.entryPrice !== null)
          .map((trade) => [trade.entryDate, trade.entryPrice]),
      },
      {
        name: 'Exits',
        type: 'scatter',
        symbol: 'diamond',
        symbolSize: 9,
        itemStyle: { color: SIGN.negative },
        data: trades
          .filter((trade) => !trade.isOpen && trade.exitDate && trade.exitPrice !== null)
          .map((trade) => [trade.exitDate, trade.exitPrice]),
      },
    ],
  }

  const open = trades.filter((trade) => trade.isOpen).length

  return (
    <ChartCard
      badPicture="entries clustered in one stretch — an edge that only existed in one regime."
      csv={{ href }}
      group={SHARED}
      height={300}
      id="chart-price"
      option={option}
      question="Where did it buy and sell, and how much of the time was it holding anything?"
      state={state}
      title="Price with trade markers"
      term="price_with_markers"
      footer={
        <Text c="dimmed" size="xs">
          Entries are triangles, exits are diamonds, shaded bands are time in the market.
          {open > 0 &&
            ` ${open} position${open === 1 ? ' is' : 's are'} still open: drawn with no right edge, and counted in no aggregate on this page.`}
        </Text>
      }
    />
  )
}

function FilledNote({ count }: { count: number }) {
  if (count === 0) return null
  return (
    <Text c="dimmed" size="xs">
      {count > MARKABLE_FILLED_BARS
        ? `${count} bars were forward-filled — too many to mark individually. This history repeats itself often enough that the shape of these curves is not trustworthy.`
        : `${count} forward-filled bar${count === 1 ? '' : 's'}, marked with dotted lines — those prices were repeated, not observed.`}
    </Text>
  )
}
