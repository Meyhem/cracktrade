import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'
import { BacktestView } from './BacktestView'
import { OptimizationView } from './OptimizationView'
import { ValidationView } from './ValidationView'
import { EvolutionView } from './EvolutionView'
import { renderWithProviders } from '../../../test/render'
import type { VerdictCheck } from '../../../api/types'

/**
 * What the four run views refuse to say.
 *
 * The assertions here are mostly negative, and that is the point. Every one of them is a rule
 * about withholding a number, and a screen that starts rendering a figure it should not is not
 * a visual regression — it is the product failing at the one thing it exists to do.
 */

const TOO_FEW = /too few trades to draw a conclusion from/i

function metrics(overrides: Record<string, unknown> = {}) {
  return {
    total_trades: 48,
    total_return_pct: 31.4,
    cagr_pct: 12.2,
    max_drawdown_pct: -22.1,
    sharpe_ratio: 0.81,
    sortino_ratio: 1.1,
    calmar_ratio: 0.55,
    win_rate_pct: 52,
    profit_factor: 1.6,
    exposure_pct: 61,
    avg_holding_bars: 9,
    final_equity: 13140,
    has_enough_trades_to_judge: true,
    ...overrides,
  }
}

describe('the backtest view', () => {
  it('leads with the excess over buy-and-hold, not with the return', () => {
    // A strategy returning 31% where the ticker returned 149% is a failure. The headline is
    // the difference; a layout opening with the raw return reads as a success.
    renderWithProviders(
      <BacktestView
        result={{
          metrics: metrics(),
          benchmark: {
            benchmark: metrics({ total_return_pct: 149 }),
            excess_return_pct: -117.6,
            beats_buy_and_hold: false,
          },
          trades: [],
        }}
      />,
    )

    expect(screen.getByText('Excess over buy-and-hold')).toBeInTheDocument()
    expect(screen.getByText('-117.6pp')).toBeInTheDocument()
    expect(screen.getByText(/underperformed simply owning the ticker/i)).toBeInTheDocument()
  })

  it('withholds every performance figure below the trade floor', () => {
    renderWithProviders(
      <BacktestView
        result={{
          metrics: metrics({ total_trades: 6, has_enough_trades_to_judge: false }),
          benchmark: { benchmark: metrics(), excess_return_pct: -117.6 },
          trades: [],
        }}
      />,
    )

    expect(screen.getByText(TOO_FEW)).toBeInTheDocument()
    expect(screen.queryByText('31.4%')).not.toBeInTheDocument()
    expect(screen.queryByText('-117.6pp')).not.toBeInTheDocument()
  })

  it('names stops that were configured and did nothing', () => {
    renderWithProviders(
      <BacktestView
        result={{
          metrics: metrics(),
          trades: [],
          active_stop: 'atr_stop_multiplier',
          shadowed_stops: ['trailing_stop_pct', 'stop_loss_pct'],
        }}
      />,
    )

    expect(screen.getByText(/trailing_stop_pct, stop_loss_pct/)).toBeInTheDocument()
  })
})

describe('an intraday backtest', () => {
  it('warns about positions that were carried overnight, and only when there were some', () => {
    // Zero is the expected state on every daily run and every healthy intraday one. A permanent
    // "0 carried overnight" would be read as decoration within a day, and then not read at all.
    const result = (carries: number) => ({
      metrics: metrics(),
      benchmark: { benchmark: metrics(), excess_return_pct: 4, beats_buy_and_hold: true },
      trades: [],
      overnight_carries: carries,
      history: { interval: '30m', sessions: 37, bars: 625, limited: true, note: 'thin' },
    })

    const { unmount } = renderWithProviders(<BacktestView result={result(0)} />)
    expect(screen.queryByText(/carried overnight/i)).not.toBeInTheDocument()
    unmount()

    renderWithProviders(<BacktestView result={result(2)} />)
    expect(screen.getByText(/2 position\(s\) carried overnight/i)).toBeInTheDocument()
  })

  it('shows the time of day on trades, which a daily run does not', () => {
    // Two trades on one date are indistinguishable without it, and "held 3 bars" cannot be
    // checked against a table that only prints days.
    const trade = {
      entry_date: '2026-06-30T14:00:00',
      exit_date: '2026-06-30T15:30:00',
      entry_price: 100,
      exit_price: 101,
      size: 10,
      pnl: 10,
      return_pct: 1,
      fees: 0,
      holding_bars: 3,
      is_open: false,
      is_winner: true,
    }

    renderWithProviders(
      <BacktestView
        result={{
          metrics: metrics(),
          benchmark: { benchmark: metrics(), excess_return_pct: 4, beats_buy_and_hold: true },
          trades: [trade],
          history: { interval: '30m', sessions: 37, bars: 625, limited: false, note: null },
        }}
      />,
    )

    expect(screen.getByText('2026-06-30 14:00')).toBeInTheDocument()
    expect(screen.getByText('2026-06-30 15:30')).toBeInTheDocument()
  })

  it('prints dates alone on a daily run, where the time is always midnight', () => {
    const trade = {
      entry_date: '2026-06-30T00:00:00',
      exit_date: '2026-07-14T00:00:00',
      entry_price: 100,
      exit_price: 101,
      size: 10,
      pnl: 10,
      return_pct: 1,
      fees: 0,
      holding_bars: 10,
      is_open: false,
      is_winner: true,
    }

    renderWithProviders(
      <BacktestView
        result={{
          metrics: metrics(),
          benchmark: { benchmark: metrics(), excess_return_pct: 4, beats_buy_and_hold: true },
          trades: [trade],
          history: { interval: '1d', sessions: 500, bars: 500, limited: false, note: null },
        }}
      />,
    )

    expect(screen.getByText('2026-06-30')).toBeInTheDocument()
    expect(screen.queryByText(/00:00/)).not.toBeInTheDocument()
  })
})

describe('the optimization view', () => {
  const result = (overrides: Record<string, unknown> = {}) => ({
    test_metrics: metrics(),
    train_metrics: metrics({ cagr_pct: 40 }),
    baseline_test_metrics: metrics({ total_return_pct: 20 }),
    changes: [],
    trades: [],
    overfitting_gap_pct: 27.8,
    improvement_pct: 11.4,
    parameters_at_bound: [],
    ...overrides,
  })

  it('always carries the one-draw banner', () => {
    renderWithProviders(<OptimizationView result={result()} />)
    expect(screen.getByText(/a single train\/test split is one draw/i)).toBeInTheDocument()
  })

  it('says in words when the overfitting gap exceeds the result itself', () => {
    renderWithProviders(<OptimizationView result={result()} />)
    expect(screen.getByText(/more of this result was left behind/i)).toBeInTheDocument()
  })

  it('warns when a parameter finished on the edge of its range', () => {
    // The engine serialises this property as a list of `ParameterChange` *objects*, not of
    // names. The fixture used to say strings, which is what let a reader that filtered them
    // out pass: the banner never rendered against a real payload, while the per-row badge
    // beside it did, so nothing looked broken.
    renderWithProviders(
      <OptimizationView
        result={result({
          parameters_at_bound: [
            {
              path: 'indicators.sma.window',
              old_value: 20,
              new_value: 30,
              low: 10,
              high: 30,
              moved: true,
              at_bound: true,
            },
          ],
        })}
      />,
    )
    expect(screen.getByText(/the range was the binding constraint/i)).toBeInTheDocument()
    expect(screen.getByText(/indicators\.sma\.window/)).toBeInTheDocument()
  })

  it('still shows what the search did when the figures are suppressed', () => {
    // The parameter moves describe what the search did, not how well it did it, so they are
    // not suppressed alongside the performance figures.
    renderWithProviders(
      <OptimizationView
        result={result({
          test_metrics: metrics({ total_trades: 6, has_enough_trades_to_judge: false }),
          changes: [
            {
              path: 'indicators.sma.window',
              old_value: 20,
              new_value: 18,
              low: 10,
              high: 30,
              moved: true,
              at_bound: false,
            },
          ],
        })}
      />,
    )

    expect(screen.getAllByText(TOO_FEW).length).toBeGreaterThan(0)
    expect(screen.getByText('indicators.sma.window')).toBeInTheDocument()
    expect(screen.queryByText('31.4%')).not.toBeInTheDocument()
  })
})

describe('the validation view', () => {
  const checks: VerdictCheck[] = [
    {
      name: 'deflated_sharpe',
      label: 'Deflated Sharpe',
      passed: false,
      plain:
        'Whether the Sharpe survives being deflated for the number of trials that produced it.',
      stat: 'P=0.00 for 4800 trials',
      detail: 'deflated Sharpe P=0.00, below the 0.95 bar for 4800 trials',
    },
  ]

  it('states the verdict as a boolean with its reasons', () => {
    renderWithProviders(
      <ValidationView
        checks={checks}
        result={{
          is_credible: false,
          failures: ['only 16 out-of-sample trades in total'],
          folds: [],
        }}
      />,
    )

    expect(screen.getByText('NOT CREDIBLE')).toBeInTheDocument()
    expect(screen.getByText(/only 16 out-of-sample trades in total/)).toBeInTheDocument()
  })

  it('renders the engine’s own explanation of each check', () => {
    renderWithProviders(<ValidationView checks={checks} result={{ folds: [] }} />)

    expect(screen.getByText('Deflated Sharpe')).toBeInTheDocument()
    expect(screen.getByText(/survives being deflated/i)).toBeInTheDocument()
    expect(screen.getByText('P=0.00 for 4800 trials')).toBeInTheDocument()
  })

  it('never presents an absent verdict as credible', () => {
    // `is_credible` missing must not fall through to the passing branch.
    renderWithProviders(<ValidationView checks={[]} result={{ folds: [] }} />)
    expect(screen.getByText('NOT CREDIBLE')).toBeInTheDocument()
    expect(screen.queryByText('CREDIBLE')).not.toBeInTheDocument()
  })

  it('labels a confidence interval that straddles zero', () => {
    renderWithProviders(
      <ValidationView
        checks={[]}
        result={{
          folds: [],
          mean_return_interval: { point: 0.1, low: -0.4, high: 0.6, excludes_zero: false },
        }}
      />,
    )

    expect(screen.getAllByText(/not distinguishable from luck/i).length).toBeGreaterThan(0)
  })
})

describe('the evolution view', () => {
  const composed = {
    is_credible: false,
    failures: ['a 10% parameter nudge destroys 70% of the objective'],
    composition:
      'Enter when RSI(14) is below 32 and price is above its 50-day EMA; exit on a 6% stop.',
    blocks: ['rsi_below_level', 'price_above_ma'],
    strategy_yaml: 'strategy:\n  name: evolved_nvda\n',
    holdout_metrics: metrics({ total_trades: 31, total_return_pct: 9.6 }),
    benchmark: { benchmark: metrics({ total_return_pct: 5.7 }) },
    distinct_configurations: 812,
    genomes_evaluated: 1000,
    segments: [
      {
        index: 0,
        first_bar: '2016-01-04',
        last_bar: '2017-06-30',
        metrics: metrics({ total_return_pct: 44.2 }),
        was_profitable: true,
      },
    ],
  }

  it('separates the holdout from the segments that chose the strategy', () => {
    // The single most important distinction on this screen. The segments are the selection
    // criterion; presenting them as four more results would make a search of 812 candidates
    // look like five independent confirmations.
    renderWithProviders(<EvolutionView checks={[]} result={composed} />)

    expect(screen.getByText('The segments that chose it')).toBeInTheDocument()
    expect(screen.getByText(/none of these are evidence/i)).toBeInTheDocument()
    expect(screen.getByText('Holdout return')).toBeInTheDocument()
  })

  it('says what it composed, in words as well as YAML', () => {
    renderWithProviders(<EvolutionView checks={[]} result={composed} />)

    expect(screen.getByText(/RSI\(14\) is below 32/)).toBeInTheDocument()
    expect(screen.getByText(/nobody chose these conditions/i)).toBeInTheDocument()
  })

  it('reports the trial count the deflation divides by', () => {
    // Buried, this is just a statistic. Stated, it is the reason a good-looking holdout might
    // still fail its own check.
    renderWithProviders(<EvolutionView checks={[]} result={composed} />)

    expect(screen.getByText('812')).toBeInTheDocument()
    expect(screen.getByText(/812 distinct strategies were scored/i)).toBeInTheDocument()
  })

  it('withholds the holdout figures below the trade floor', () => {
    renderWithProviders(
      <EvolutionView
        checks={[]}
        result={{
          ...composed,
          holdout_metrics: metrics({
            total_trades: 9,
            total_return_pct: 41.7,
            has_enough_trades_to_judge: false,
          }),
        }}
      />,
    )

    expect(screen.getByText(TOO_FEW)).toBeInTheDocument()
    expect(screen.queryByText('41.7%')).not.toBeInTheDocument()
    // The composition is not a measurement, so suppression does not reach it.
    expect(screen.getByText(/RSI\(14\) is below 32/)).toBeInTheDocument()
  })

  it('never presents an absent verdict as credible', () => {
    renderWithProviders(<EvolutionView checks={[]} result={{ segments: [] }} />)

    expect(screen.getByText('NOT CREDIBLE')).toBeInTheDocument()
    expect(screen.queryByText('CREDIBLE')).not.toBeInTheDocument()
  })

  it('warns that the holdout has now been spent', () => {
    // The caveat most likely to be lost between one run and the next: re-running evolution on
    // the same ticker does not get a fresh holdout.
    renderWithProviders(<EvolutionView checks={[]} result={composed} />)

    expect(screen.getByText(/does not get a fresh one/i)).toBeInTheDocument()
  })

  it('says when the search stopped improving before its budget ran out', () => {
    renderWithProviders(
      <EvolutionView
        checks={[]}
        result={{ ...composed, best_score_by_generation: [-2, -1, -1, -1, -1, -1] }}
      />,
    )

    expect(screen.getByText(/stopped improving at generation 2 of 6/i)).toBeInTheDocument()
  })
})
