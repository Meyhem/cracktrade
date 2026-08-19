import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ChartsTab } from './ChartsTab'
import { POOLING_MESSAGE } from './view'
import { StrategyContext } from '../strategy/context'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { run as runFixture, strategyDetail } from '../../test/fixtures'
import type { Run } from '../../api/types'

/**
 * The charts tab.
 *
 * These tests are almost entirely about what is *not* drawn. A chart is more persuasive than a
 * number, so §5.2's suppression rules and §12.9's refusal to pool folds are the parts of this
 * screen most worth defending — and both regress into silently rendering a figure, which is
 * invisible until it has misled someone.
 *
 * ECharts is stubbed for every test file (see `test/setup.ts`), so nothing here asserts about
 * pixels. What it asserts is which frame holds a chart and which holds a sentence.
 */

const RUN_ID = '33333333-3333-3333-3333-333333333333'

function metrics(overrides: Record<string, unknown> = {}) {
  return {
    total_trades: 48,
    total_return_pct: 31.4,
    has_enough_trades_to_judge: true,
    yearly_returns: [
      { year: 2023, return_pct: 12 },
      { year: 2024, return_pct: 19 },
    ],
    worst_rolling_12m_pct: -8.2,
    worst_rolling_12m_measurable: true,
    ...overrides,
  }
}

function trades(count: number) {
  return Array.from({ length: count }, (_unused, index) => ({
    entry_date: `2023-0${(index % 9) + 1}-03`,
    exit_date: `2023-0${(index % 9) + 1}-20`,
    entry_price: 100,
    exit_price: 110,
    size: 10,
    pnl: index === 0 ? 900 : -20,
    return_pct: index === 0 ? 90 : -2,
    fees: 1,
    holding_bars: 17,
    is_open: false,
    is_winner: index === 0,
  }))
}

const EQUITY = {
  dates: ['2023-01-03', '2023-06-01', '2023-12-29'],
  values: [10000, 10800, 13140],
}

function answering(options: {
  runs: Run[]
  result: Record<string, unknown> | null
  series?: Record<string, unknown>
}) {
  const series: Record<string, unknown> = {
    equity: EQUITY,
    benchmark_equity: EQUITY,
    drawdown: { dates: EQUITY.dates, values: [0, -4, 0] },
    close: { dates: EQUITY.dates, values: [100, 108, 131] },
    monthly_returns: { months: ['2023-01', '2023-02'], values: [2, -1], in_market: [true, false] },
    rolling_12m_return: { dates: EQUITY.dates, values: [0, 0, 9] },
    filled: { dates: [] },
    ...options.series,
  }

  return [
    http.get('*/api/v1/runs', () =>
      HttpResponse.json({ runs: options.runs, total: options.runs.length }),
    ),
    http.get('*/api/v1/runs/:runId', ({ params }) =>
      HttpResponse.json({
        run: options.runs.find((entry) => entry.id === params.runId) ?? options.runs[0],
        result: options.result,
        error: null,
        checks: [],
        config_diff: [],
        default_promote_name: null,
      }),
    ),
    http.get('*/api/v1/runs/:runId/series/:name', ({ params }) => {
      const points = series[String(params.name)]
      if (points === undefined) return HttpResponse.json({ detail: 'not found' }, { status: 404 })
      return HttpResponse.json({ name: String(params.name), fold: 0, points })
    }),
  ]
}

function open(route = '/strategies/x/charts') {
  return renderWithProviders(
    <StrategyContext value={{ strategy: strategyDetail() }}>
      <ChartsTab />
    </StrategyContext>,
    { route },
  )
}

describe('choosing a run', () => {
  it('explains itself when nothing has finished', () => {
    server.use(...answering({ runs: [], result: null }))
    open()
    return expect(screen.findByText(/No run has finished yet/i)).resolves.toBeInTheDocument()
  })

  it('says which run is on screen and why', async () => {
    server.use(
      ...answering({
        runs: [
          runFixture({ id: RUN_ID, kind: 'walk_forward', number: 7 }),
          runFixture({ id: 'bt', kind: 'backtest', number: 6 }),
        ],
        result: { folds: [] },
      }),
    )
    open()

    expect(await screen.findByText(/the most serious run available/i)).toBeInTheDocument()
  })

  it('switches the charts when another run is picked', async () => {
    // The selection is two query parameters, and writing them with two separate navigations
    // meant the second rebuilt from the pre-navigation URL and discarded the first. The symptom
    // was a query string that flickered and a run selector that snapped back.
    server.use(
      ...answering({
        runs: [
          runFixture({ id: RUN_ID, kind: 'walk_forward', number: 7 }),
          runFixture({ id: 'bt', kind: 'backtest', number: 6 }),
        ],
        result: { metrics: metrics(), trades: trades(48), folds: [] },
      }),
    )
    open()

    const selector = await screen.findByRole('combobox', { name: /Showing/ })
    await userEvent.click(selector)
    await userEvent.click(await screen.findByRole('option', { name: /Backtest #6/ }))

    await waitFor(() => {
      expect((selector as HTMLInputElement).value).toMatch(/Backtest #6/)
    })
  })

  it('warns when the selected run describes a configuration that has since changed', async () => {
    server.use(
      ...answering({
        runs: [runFixture({ id: RUN_ID, stale: true, version: 2 })],
        result: { metrics: metrics(), trades: trades(48) },
      }),
    )
    open()

    expect(await screen.findByText(/describes the older configuration/i)).toBeInTheDocument()
  })
})

describe('the trade floor', () => {
  it('refuses every per-trade and per-period chart below it', async () => {
    // §5.2: below the floor, no per-trade or per-period chart at all. The engine's own
    // `has_enough_trades_to_judge` is the authority — this side never recomputes it.
    server.use(
      ...answering({
        runs: [runFixture({ id: RUN_ID })],
        result: {
          metrics: metrics({ total_trades: 6, has_enough_trades_to_judge: false }),
          trades: trades(6),
        },
      }),
    )
    open()

    const refusals = await screen.findAllByText(
      /too few trades to draw a conclusion from \(6 of 20/,
    )
    expect(refusals.length).toBeGreaterThan(1)
  })

  it('still draws the price chart, which shows events rather than an estimate', async () => {
    server.use(
      ...answering({
        runs: [runFixture({ id: RUN_ID })],
        result: {
          metrics: metrics({ total_trades: 6, has_enough_trades_to_judge: false }),
          trades: trades(6),
        },
      }),
    )
    open()

    // The sole exception in §5.2: looking at six trades one at a time is a reasonable thing
    // to do, so this chart renders where the estimates do not.
    await screen.findAllByText(/too few trades/)
    expect(await screen.findByLabelText('Price with trade markers')).toBeInTheDocument()
  })
})

describe('a missing series', () => {
  it('names what is absent instead of substituting a chart that has data', async () => {
    server.use(
      ...answering({
        runs: [runFixture({ id: RUN_ID })],
        result: { metrics: metrics(), trades: trades(48) },
        series: { equity: undefined },
      }),
    )
    open()

    expect(
      await screen.findByText(/The equity curve was not recorded for this run/i),
    ).toBeInTheDocument()
  })
})

describe('a walk-forward', () => {
  const report = {
    folds: [
      {
        index: 0,
        metrics: metrics({ total_trades: 24 }),
        train_metrics: metrics(),
        parameters: { 'indicators.sma_long.window': 150 },
        trades: trades(24),
        first_test_bar: '2023-01-03',
        last_test_bar: '2023-06-30',
      },
      {
        index: 1,
        metrics: metrics({ total_trades: 22 }),
        train_metrics: metrics(),
        parameters: { 'indicators.sma_long.window': 210 },
        trades: trades(22),
        first_test_bar: '2023-07-01',
        last_test_bar: '2023-12-29',
      },
    ],
    profitable_folds: 2,
    trials: 400,
  }

  it('refuses to pool trades across folds, and says why', async () => {
    server.use(
      ...answering({ runs: [runFixture({ id: RUN_ID, kind: 'walk_forward' })], result: report }),
    )
    open()

    // Combined is the default, and in it the trade-level groups explain rather than combine.
    // Pooling would chart a configuration that was never run (spec §12.9).
    const refusals = await screen.findAllByText(POOLING_MESSAGE)
    expect(refusals.length).toBeGreaterThan(1)
  })

  it('does not blame a missing series for a refusal that is about the folds', async () => {
    // The price chart waives the *trade floor*, and nothing else. Waiving the state wholesale
    // made it announce "the price series was not recorded for this run" in the combined view —
    // a false statement about the run, pointing at a missing feature rather than at the fold
    // selector directly above it.
    server.use(
      ...answering({ runs: [runFixture({ id: RUN_ID, kind: 'walk_forward' })], result: report }),
    )
    open()

    await screen.findAllByText(POOLING_MESSAGE)
    expect(screen.queryByText(/price series was not recorded/i)).not.toBeInTheDocument()
  })

  it('draws that fold’s trades once a fold is selected', async () => {
    server.use(
      ...answering({ runs: [runFixture({ id: RUN_ID, kind: 'walk_forward' })], result: report }),
    )
    open()

    await screen.findAllByText(POOLING_MESSAGE)
    const user = userEvent.setup()
    await user.click(screen.getByRole('combobox', { name: 'Fold' }))
    await user.click(await screen.findByText(/Fold 1 ·/))

    await waitFor(() => expect(screen.queryByText(POOLING_MESSAGE)).not.toBeInTheDocument())
    // The parameters that fold settled on, shown beside the selector: switching folds is
    // visibly switching strategies, not switching views of one.
    expect(await screen.findByText('window 150')).toBeInTheDocument()
  })

  it('refuses the yearly and rolling charts in the combined view', async () => {
    server.use(
      ...answering({ runs: [runFixture({ id: RUN_ID, kind: 'walk_forward' })], result: report }),
    )
    open()

    // Both aggregate over twelve months or more, and every such window crosses a fold
    // boundary — compounding across one would describe a strategy that was never traded.
    expect(
      await screen.findByText(/every year in a walk-forward crosses a fold boundary/i),
    ).toBeInTheDocument()
    expect(
      await screen.findByText(/trailing twelve-month window crosses fold boundaries/i),
    ).toBeInTheDocument()
  })
})

describe('the robustness group', () => {
  it('stays on the page for a backtest rather than shortening it', async () => {
    // An unvalidated strategy must never present as a validated one with a shorter page.
    server.use(
      ...answering({
        runs: [runFixture({ id: RUN_ID })],
        result: { metrics: metrics(), trades: trades(48) },
      }),
    )
    open()

    expect(await screen.findByText(/These checks need a walk-forward run/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run a walk-forward' })).toBeInTheDocument()
  })
})

describe('an evolution run', () => {
  const evolveRun = runFixture({
    id: 'e0000000-0000-0000-0000-000000000000',
    kind: 'evolve',
    number: 9,
  })

  const evolveResult = {
    holdout_metrics: metrics({ total_trades: 31, total_return_pct: 9.6 }),
    benchmark: { benchmark: metrics({ total_return_pct: 5.7 }) },
    composition: 'Enter when RSI(14) is below 32.',
    segments: [],
  }

  it('says the curve is the holdout of a strategy this one does not contain', async () => {
    // Without this the equity curve is a fifth of the date range drawn in the frame that means
    // "the whole backtest" on every other run — and it belongs to a composition the strategy
    // in the page header does not have.
    server.use(...answering({ runs: [evolveRun], result: evolveResult }))
    open()

    expect(
      await screen.findByText(/holdout only, for a strategy this one does not contain/i),
    ).toBeInTheDocument()
  })

  it('is never the run the tab opens on', async () => {
    // A walk-forward measures this strategy; an evolution run measures a different one. The
    // default has to land on the former whichever finished more recently.
    const validation = runFixture({
      id: 'a0000000-0000-0000-0000-000000000000',
      kind: 'walk_forward',
      number: 3,
      finished_at: '2020-01-01T00:00:00Z',
    })
    server.use(
      ...answering({
        runs: [evolveRun, validation],
        result: { folds: [], ...evolveResult },
      }),
    )
    open()

    await waitFor(() => expect(screen.getByDisplayValue(/Walk-forward #3/)).toBeInTheDocument())
  })
})
