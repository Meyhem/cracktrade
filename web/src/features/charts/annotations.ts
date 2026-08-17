/**
 * The numbers a chart carries that no endpoint publishes.
 *
 * Everything here is derived from data already on screen, and each one exists because the
 * picture alone leaves a question open that the answer closes: is the profit a system or one
 * bet, and how long would you have spent underwater.
 *
 * The rule they all obey: **derive only what can be derived exactly.** Where an honest figure
 * would need the engine's simulation — a compounded return recomputed with a trade removed,
 * say — this module reports the quantity it *can* compute and labels it as that quantity,
 * rather than reporting a plausible-looking version of the one it cannot.
 */

import type { Point } from './series'
import type { Trade } from '../../lib/result'

/**
 * What the single best trade was carrying.
 *
 * Reported in **currency**, as sums of realised P&L, and deliberately not as a recomputed
 * total return. Removing a trade from a compounded equity curve changes the capital every
 * later trade was sized against, so a "total return without the best trade" can only come from
 * re-running the simulation. A percentage computed here by subtracting one P&L from another
 * and dividing by starting capital would be a different quantity wearing the label of the
 * headline return — precisely the authoritative-and-wrong output this project is organised
 * against.
 *
 * Two sums of the same kind answer the question the chart asks. Open trades are excluded: an
 * unrealized gain is not a result (§5.2).
 */
export type BestTradeEffect = {
  closed: number
  totalPnl: number
  bestPnl: number
  withoutBest: number
  /** True when the strategy's entire realised profit rests on one trade. */
  losesMoneyWithoutBest: boolean
}

export function bestTradeEffect(closed: Trade[]): BestTradeEffect | null {
  if (closed.length === 0) return null
  const pnls = closed.map((trade) => trade.pnl ?? 0)
  const totalPnl = pnls.reduce((sum, pnl) => sum + pnl, 0)
  const bestPnl = Math.max(...pnls)
  const withoutBest = totalPnl - bestPnl

  return {
    closed: closed.length,
    totalPnl,
    bestPnl,
    withoutBest,
    losesMoneyWithoutBest: totalPnl > 0 && withoutBest <= 0,
  }
}

/**
 * Running P&L in trade order.
 *
 * The x-axis is **trade sequence, not time**. Labelled as such wherever it is drawn, because
 * the shape resembles an equity curve closely enough to be read as one, and a staircase over
 * forty trades and a cliff at trade nine are the two answers this chart exists to separate.
 */
export type TradeStep = { sequence: number; date: string | null; pnl: number; cumulative: number }

export function cumulativeByTrade(closed: Trade[]): TradeStep[] {
  let cumulative = 0
  return closed.map((trade, index) => {
    const pnl = trade.pnl ?? 0
    cumulative += pnl
    return { sequence: index + 1, date: trade.exitDate ?? trade.entryDate, pnl, cumulative }
  })
}

/** Gross profit against gross loss. Their ratio is `profit_factor`, made physical. */
export type WonAgainstLost = {
  grossProfit: number
  grossLoss: number
  winners: number
  losers: number
  averageWin: number
  averageLoss: number
}

export function wonAgainstLost(closed: Trade[]): WonAgainstLost {
  const wins = closed.filter((trade) => (trade.pnl ?? 0) > 0)
  const losses = closed.filter((trade) => (trade.pnl ?? 0) <= 0)
  const sum = (trades: Trade[]) => trades.reduce((total, trade) => total + (trade.pnl ?? 0), 0)
  const grossProfit = sum(wins)
  const grossLoss = Math.abs(sum(losses))

  return {
    grossProfit,
    grossLoss,
    winners: wins.length,
    losers: losses.length,
    averageWin: wins.length > 0 ? grossProfit / wins.length : 0,
    averageLoss: losses.length > 0 ? grossLoss / losses.length : 0,
  }
}

/**
 * The longest stretch spent below a previous peak.
 *
 * `max_drawdown_pct` is the *deepest* valley. This is the *widest* one, and it is the figure
 * that decides whether a strategy is actually holdable: people abandon a system that has been
 * flat for three years far more often than one that fell 30% and recovered in a month.
 *
 * A drawdown still open at the last bar is reported with `recovered: false`. Its length is a
 * lower bound — it has not ended — and calling it a completed recovery would understate it.
 */
export type Underwater = { from: string; to: string; days: number; recovered: boolean }

export function longestUnderwater(drawdown: Point[]): Underwater | null {
  let longest: Underwater | null = null
  let start: string | null = null

  const close = (from: string, to: string, recovered: boolean) => {
    const days = Math.round(
      (new Date(to).getTime() - new Date(from).getTime()) / (24 * 60 * 60 * 1000),
    )
    if (longest === null || days > longest.days) longest = { from, to, days, recovered }
  }

  for (const point of drawdown) {
    if (point.value < 0) {
      start ??= point.date
      continue
    }
    if (start !== null) {
      close(start, point.date, true)
      start = null
    }
  }

  const last = drawdown.at(-1)
  if (start !== null && last) close(start, last.date, false)
  return longest
}

/**
 * Each parameter's optimum across folds, rescaled onto a shared axis.
 *
 * The parameters have different units — a 200-bar window and a 2.5× ATR multiple cannot share
 * a y-axis in their own terms — so each line is min-max normalised across the folds. What
 * survives the rescaling is the *shape*, which is the whole question: a real optimum is picked
 * roughly consistently, and a lookback that goes 5 → 60 → 12 is noise.
 *
 * A parameter the search settled on identically in every fold has no range to normalise
 * against. It is drawn flat at the middle and flagged `constant`, rather than divided by zero
 * and drawn as whatever that produces.
 */
export type ParameterDrift = {
  path: string
  values: (number | null)[]
  normalised: (number | null)[]
  low: number
  high: number
  constant: boolean
}

export function parameterDrift(folds: Record<string, number | null>[]): ParameterDrift[] {
  const paths = [...new Set(folds.flatMap((fold) => Object.keys(fold)))].sort()

  return paths.map((path) => {
    const values = folds.map((fold) => fold[path] ?? null)
    const present = values.filter((value): value is number => value !== null)
    const low = present.length > 0 ? Math.min(...present) : 0
    const high = present.length > 0 ? Math.max(...present) : 0
    const span = high - low

    return {
      path,
      values,
      normalised: values.map((value) => {
        if (value === null) return null
        return span === 0 ? 0.5 : (value - low) / span
      }),
      low,
      high,
      constant: span === 0,
    }
  })
}
