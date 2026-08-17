import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'
import { BacktestView } from './BacktestView'
import { OptimizationView } from './OptimizationView'
import { ValidationView } from './ValidationView'
import { renderWithProviders } from '../../../test/render'
import type { VerdictCheck } from '../../../api/types'

/**
 * What the three run views refuse to say.
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
    avg_holding_days: 9,
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
    renderWithProviders(
      <OptimizationView result={result({ parameters_at_bound: ['indicators.sma.window'] })} />,
    )
    expect(screen.getByText(/the range was the binding constraint/i)).toBeInTheDocument()
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
