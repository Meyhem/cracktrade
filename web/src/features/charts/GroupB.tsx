import { Alert, Stack, Text } from '@mantine/core'
import { IconAlertTriangle } from '@tabler/icons-react'
import { ChartCard } from './ChartCard'
import { CROSSHAIR, GRID, SIGN, moneyAxis, percentValueAxis, zeroLine } from './options'
import { bestTradeEffect, cumulativeByTrade, wonAgainstLost } from './annotations'
import { requires, firstProblem, type ChartState } from './state'
import { money } from '../../lib/format'
import type { Trade } from '../../lib/result'
import type { EChartsOption } from 'echarts'

/**
 * Group B — where the money came from.
 *
 * One question: *is the profit a system, or is it one lucky trade?* Every chart here is an
 * answer to that and to nothing else, which is why none of them is a win-rate figure — a
 * 90%-win-rate strategy that loses money is easy to build, and a pie chart of win rate would
 * imply win rate is the answer.
 *
 * Open trades appear nowhere in this group. An unrealized gain is not a result (§5.2).
 */

/**
 * Below twenty or so trades, a histogram's bins are an artefact of the bin width rather than
 * a shape in the data. A dot strip — one dot per trade — encodes the same sample without
 * implying a distribution, and is the more honest picture near the floor (§5.4).
 */
const DOT_STRIP_BELOW = 40

export function GroupB({
  state,
  trades,
  closed,
}: {
  state: ChartState
  trades: Trade[]
  closed: Trade[]
}) {
  const present = firstProblem(state, requires(trades.length > 0, 'The trade list'))

  return (
    <Stack gap="md">
      <DistributionChart closed={closed} state={present} />
      <CumulativeChart closed={closed} state={present} />
      <WonAgainstLostChart closed={closed} state={present} />
    </Stack>
  )
}

function DistributionChart({ closed, state }: { closed: Trade[]; state: ChartState }) {
  const effect = bestTradeEffect(closed)
  const strip = closed.length < DOT_STRIP_BELOW

  const option: EChartsOption = strip
    ? {
        grid: GRID,
        tooltip: { trigger: 'item' },
        xAxis: { ...percentValueAxis(), name: 'return', nameLocation: 'middle', nameGap: 26 },
        yAxis: { type: 'value', min: -1, max: 1, show: false },
        series: [
          {
            type: 'scatter',
            symbolSize: 11,
            data: closed.map((trade, index) => ({
              // Jittered on a deliberately meaningless axis, so overlapping trades stay
              // countable. The y position carries nothing and the axis is hidden to say so.
              value: [trade.returnPct ?? 0, ((index % 7) - 3) / 5],
              itemStyle: {
                color: (trade.pnl ?? 0) > 0 ? SIGN.positive : SIGN.negative,
                opacity: 0.75,
              },
              symbol: (trade.pnl ?? 0) > 0 ? 'circle' : 'diamond',
            })),
            markLine: zeroLine('x'),
          },
        ],
      }
    : {
        grid: GRID,
        tooltip: CROSSHAIR,
        xAxis: { type: 'category', data: bins(closed).map((bin) => bin.label) },
        yAxis: { type: 'value', name: 'trades', minInterval: 1 },
        series: [
          {
            type: 'bar',
            data: bins(closed).map((bin) => ({
              value: bin.count,
              itemStyle: { color: bin.centre > 0 ? SIGN.positive : SIGN.negative },
            })),
          },
        ],
      }

  return (
    <ChartCard
      badPicture="one bar far out to the right carrying everything to its left."
      csv={{
        filename: 'trade_returns.csv',
        rows: [
          ['entry_date', 'exit_date', 'return_pct', 'pnl'],
          ...closed.map((trade) => [trade.entryDate, trade.exitDate, trade.returnPct, trade.pnl]),
        ],
      }}
      height={260}
      id="chart-distribution"
      option={option}
      question="How are the wins and losses actually distributed?"
      state={state}
      title="Trade return distribution"
      footer={effect && <BestTradeNote effect={effect} strip={strip} />}
    />
  )
}

function BestTradeNote({
  effect,
  strip,
}: {
  effect: NonNullable<ReturnType<typeof bestTradeEffect>>
  strip: boolean
}) {
  return (
    <Stack gap={6}>
      <Text c="dimmed" size="xs">
        {effect.closed} closed trades
        {strip && ', drawn one dot each — too few for bins to mean anything'}. Realised P&amp;L $
        {money(effect.totalPnl)}; without its single best trade (${money(effect.bestPnl)}), $
        {money(effect.withoutBest)}.
      </Text>
      {effect.losesMoneyWithoutBest && (
        <Alert color="orange" icon={<IconAlertTriangle size={16} />} variant="light">
          Without its best trade this strategy loses money.
        </Alert>
      )}
      <Text c="dimmed" size="xs">
        Both figures are sums of realised P&amp;L, not returns. Removing a trade from a compounded
        curve changes the capital every later trade was sized against, so a &ldquo;total return
        without the best trade&rdquo; can only come from re-running the simulation — and a
        percentage invented here would wear the headline return&rsquo;s label while measuring
        something else.
      </Text>
    </Stack>
  )
}

function CumulativeChart({ closed, state }: { closed: Trade[]; state: ChartState }) {
  const steps = cumulativeByTrade(closed)

  const option: EChartsOption = {
    grid: GRID,
    tooltip: CROSSHAIR,
    legend: { data: ['Trade P&L', 'Cumulative'], top: 0 },
    // Trade sequence, not time. Labelled on the axis itself, because the shape resembles an
    // equity curve closely enough to be read as one.
    xAxis: {
      type: 'category',
      name: 'trade number (not time)',
      nameLocation: 'middle',
      nameGap: 26,
      data: steps.map((step) => String(step.sequence)),
    },
    yAxis: moneyAxis(),
    series: [
      {
        name: 'Trade P&L',
        type: 'bar',
        data: steps.map((step) => ({
          value: step.pnl,
          itemStyle: { color: step.pnl > 0 ? SIGN.positive : SIGN.negative },
        })),
        markLine: zeroLine(),
      },
      {
        name: 'Cumulative',
        type: 'line',
        showSymbol: false,
        lineStyle: { width: 2, color: SIGN.strategy },
        itemStyle: { color: SIGN.strategy },
        data: steps.map((step) => step.cumulative),
      },
    ],
  }

  return (
    <ChartCard
      badPicture="a flat line with one cliff in it — the whole result is one bet."
      csv={{
        filename: 'cumulative_by_trade.csv',
        rows: [
          ['sequence', 'date', 'pnl', 'cumulative'],
          ...steps.map((step) => [step.sequence, step.date, step.pnl, step.cumulative]),
        ],
      }}
      height={280}
      id="chart-cumulative"
      option={option}
      question="Did the profit accumulate steadily, or arrive all at once?"
      state={state}
      title="Cumulative P&L by trade"
      footer={
        <Text c="dimmed" size="xs">
          The x-axis is trade sequence, not time. A staircase is a system; a cliff is one bet.
        </Text>
      }
    />
  )
}

function WonAgainstLostChart({ closed, state }: { closed: Trade[]; state: ChartState }) {
  const totals = wonAgainstLost(closed)

  const option: EChartsOption = {
    grid: GRID,
    tooltip: { trigger: 'item' },
    xAxis: { type: 'category', data: ['Won', 'Lost'] },
    yAxis: moneyAxis('gross'),
    series: [
      {
        type: 'bar',
        barWidth: '45%',
        // The count and the average sit *inside* each bar rather than on hover: nothing
        // important on this tab is available only to a mouse.
        label: {
          show: true,
          position: 'inside',
          formatter: (params: { dataIndex: number }) =>
            params.dataIndex === 0
              ? `${totals.winners} trades\navg $${money(totals.averageWin)}`
              : `${totals.losers} trades\navg $${money(totals.averageLoss)}`,
        },
        data: [
          { value: totals.grossProfit, itemStyle: { color: SIGN.positive } },
          { value: totals.grossLoss, itemStyle: { color: SIGN.negative } },
        ],
      },
    ],
  }

  const factor = totals.grossLoss === 0 ? null : totals.grossProfit / totals.grossLoss

  return (
    <ChartCard
      badPicture="the loss bar taller than the win bar."
      csv={{
        filename: 'won_against_lost.csv',
        rows: [
          ['side', 'gross', 'trades', 'average'],
          ['won', totals.grossProfit, totals.winners, totals.averageWin],
          ['lost', totals.grossLoss, totals.losers, totals.averageLoss],
        ],
      }}
      height={260}
      id="chart-won-lost"
      option={option}
      question="How much did the winners make against what the losers cost?"
      state={state}
      title="Won against lost"
      footer={
        <Text c="dimmed" size="xs">
          The ratio of these two bars is the profit factor
          {factor === null
            ? ' — undefined here, because nothing lost money.'
            : `: ${factor.toFixed(2)}. Below 1.00 the right bar is taller and the strategy loses money.`}
        </Text>
      }
    />
  )
}

/** Fixed-count bins across the observed range. Only used above `DOT_STRIP_BELOW`. */
function bins(closed: Trade[]): { label: string; centre: number; count: number }[] {
  const returns = closed.map((trade) => trade.returnPct ?? 0)
  if (returns.length === 0) return []
  const low = Math.min(...returns, 0)
  const high = Math.max(...returns, 0)
  const width = (high - low) / 20 || 1

  return Array.from({ length: 20 }, (_unused, index) => {
    const from = low + index * width
    const to = from + width
    return {
      label: `${from.toFixed(1)}%`,
      centre: (from + to) / 2,
      count: returns.filter((value) => value >= from && (index === 19 ? value <= to : value < to))
        .length,
    }
  })
}
