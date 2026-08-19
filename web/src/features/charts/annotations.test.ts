import { describe, expect, it } from 'vitest'
import {
  bestTradeEffect,
  cumulativeByTrade,
  longestUnderwater,
  parameterDrift,
  wonAgainstLost,
} from './annotations'
import type { Trade } from '../../lib/result'

/**
 * The derived numbers.
 *
 * Every one of these is printed as an annotation on a chart, which makes it more persuasive
 * than the same figure in a table and therefore worth more care. These tests are mostly about
 * the cases where an obvious implementation is confidently wrong.
 */

function trade(pnl: number, overrides: Partial<Trade> = {}): Trade {
  return {
    entryDate: '2021-01-04',
    exitDate: '2021-02-01',
    entryPrice: 100,
    exitPrice: 110,
    size: 10,
    pnl,
    returnPct: pnl / 10,
    fees: 1,
    holdingBars: 28,
    isOpen: false,
    isWinner: pnl > 0,
    ...overrides,
  }
}

describe('the best trade', () => {
  it('says so when the whole result rests on one trade', () => {
    const effect = bestTradeEffect([trade(900), trade(-100), trade(-120), trade(-80)])
    expect(effect?.totalPnl).toBe(600)
    expect(effect?.withoutBest).toBe(-300)
    expect(effect?.losesMoneyWithoutBest).toBe(true)
  })

  it('does not claim a losing strategy is carried by its best trade', () => {
    // Removing the best trade from something already unprofitable makes it more unprofitable,
    // which is not the finding this annotation exists to report.
    const effect = bestTradeEffect([trade(50), trade(-200)])
    expect(effect?.withoutBest).toBeLessThan(0)
    expect(effect?.losesMoneyWithoutBest).toBe(false)
  })

  it('reports nothing at all when no trade closed', () => {
    expect(bestTradeEffect([])).toBeNull()
  })
})

describe('cumulative P&L', () => {
  it('accumulates in the order the trades were taken', () => {
    const steps = cumulativeByTrade([trade(100), trade(-40), trade(30)])
    expect(steps.map((step) => step.cumulative)).toEqual([100, 60, 90])
    expect(steps.map((step) => step.sequence)).toEqual([1, 2, 3])
  })
})

describe('won against lost', () => {
  it('reports the loss side as a positive magnitude', () => {
    // Both bars are drawn upward and compared by height; a negative gross loss would draw the
    // loss bar below the axis and invert the comparison the chart exists to make.
    const totals = wonAgainstLost([trade(100), trade(50), trade(-30)])
    expect(totals.grossProfit).toBe(150)
    expect(totals.grossLoss).toBe(30)
    expect(totals.averageWin).toBe(75)
  })

  it('counts a break-even trade as a loss rather than a win', () => {
    const totals = wonAgainstLost([trade(0)])
    expect(totals.winners).toBe(0)
    expect(totals.losers).toBe(1)
  })
})

describe('the longest time under water', () => {
  it('measures width rather than depth', () => {
    // The deeper valley is shorter. `max_drawdown_pct` already reports depth; what makes
    // people abandon a strategy is how long it stayed down.
    const drawdown = [
      { date: '2020-01-01', value: 0 },
      { date: '2020-02-01', value: -30 },
      { date: '2020-03-01', value: 0 },
      { date: '2021-01-01', value: -5 },
      { date: '2021-06-01', value: -4 },
      { date: '2022-01-01', value: 0 },
    ]
    expect(longestUnderwater(drawdown)?.from).toBe('2021-01-01')
    expect(longestUnderwater(drawdown)?.recovered).toBe(true)
  })

  it('marks a drawdown still open at the last bar as unrecovered', () => {
    const found = longestUnderwater([
      { date: '2020-01-01', value: 0 },
      { date: '2020-02-01', value: -12 },
      { date: '2020-06-01', value: -8 },
    ])
    expect(found?.recovered).toBe(false)
  })

  it('reports nothing for a curve that never fell below its peak', () => {
    expect(longestUnderwater([{ date: '2020-01-01', value: 0 }])).toBeNull()
  })
})

describe('parameter drift', () => {
  it('rescales each parameter within its own range', () => {
    const drift = parameterDrift([
      { 'indicators.sma.window': 10, 'exit.stop_loss_pct': 2 },
      { 'indicators.sma.window': 60, 'exit.stop_loss_pct': 3 },
      { 'indicators.sma.window': 35, 'exit.stop_loss_pct': 4 },
    ])
    const window = drift.find((line) => line.path === 'indicators.sma.window')
    expect(window?.normalised).toEqual([0, 1, 0.5])
    expect(window?.constant).toBe(false)
  })

  it('draws a parameter every fold agreed on flat instead of dividing by zero', () => {
    const drift = parameterDrift([{ 'exit.stop_loss_pct': 5 }, { 'exit.stop_loss_pct': 5 }])
    expect(drift[0]?.normalised).toEqual([0.5, 0.5])
    expect(drift[0]?.constant).toBe(true)
  })

  it('keeps a gap where a fold did not report a parameter', () => {
    // A fold missing a parameter is a gap in the line, not a zero. Zero would read as an
    // optimum at the bottom of the range, which is a claim nobody made.
    const drift = parameterDrift([{ 'a.b': 4 }, {}, { 'a.b': 6 }])
    expect(drift[0]?.normalised).toEqual([0, null, 1])
  })
})
