import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HistoryTab } from './HistoryTab'
import { StrategyContext } from '../strategy/context'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { run as runFixture, strategyDetail } from '../../test/fixtures'
import type { Run, VersionSummary } from '../../api/types'

/**
 * The version history and comparison tab.
 *
 * Like the charts tab, most of what matters here is what the screen *declines* to say. A
 * comparison table is read as an answer to "did my edit help", and the two ways it can lie —
 * comparing runs that measured different questions, and filling an absent figure with a
 * neighbour's — are both invisible once rendered.
 */

const STRATEGY = '11111111-1111-1111-1111-111111111111'

function version(overrides: Partial<VersionSummary> = {}): VersionSummary {
  return {
    version: 1,
    head: false,
    origin: 'created',
    restored_from: null,
    note: null,
    created_at: '2026-08-01T09:00:00Z',
    change_summary: [],
    flags: [],
    runs_against: 0,
    ...overrides,
  } as VersionSummary
}

function walkForward(result: Record<string, unknown>) {
  return {
    objective: 'calmar',
    scheme: 'rolling',
    folds: [{ metrics: { has_enough_trades_to_judge: true }, was_profitable: true }],
    fold_win_rate: 0.5,
    total_trades: 40,
    combined_return_pct: 10,
    is_credible: false,
    vintage: { frame_digest: 'frame-a' },
    ...result,
  }
}

function answering(options: {
  versions: VersionSummary[]
  runs: Run[]
  results?: Record<string, Record<string, unknown>>
}) {
  return [
    http.get('*/api/v1/strategies/:id/versions', () => HttpResponse.json(options.versions)),
    http.get('*/api/v1/runs', () =>
      HttpResponse.json({ runs: options.runs, total: options.runs.length }),
    ),
    http.get('*/api/v1/runs/:runId', ({ params }) =>
      HttpResponse.json({
        run: options.runs.find((entry) => entry.id === params.runId) ?? options.runs[0],
        result: options.results?.[String(params.runId)] ?? null,
        error: null,
        checks: [],
        config_diff: [],
        default_promote_name: null,
      }),
    ),
  ]
}

function open() {
  return renderWithProviders(
    <StrategyContext value={{ strategy: strategyDetail() }}>
      <HistoryTab />
    </StrategyContext>,
    { route: `/strategies/${STRATEGY}/history` },
  )
}

describe('the timeline', () => {
  it('lists versions newest first and marks the head', async () => {
    server.use(
      ...answering({
        versions: [
          version({ version: 2, head: true, note: 'widened the stop' }),
          version({ version: 1 }),
        ],
        runs: [],
      }),
    )
    open()

    const rows = await screen.findAllByText(/^v[12]$/)
    expect(rows.map((node) => node.textContent)).toEqual(['v2', 'v1'])
    expect(screen.getByText('head')).toBeInTheDocument()
  })

  it('summarises what each version changed', async () => {
    server.use(
      ...answering({
        versions: [
          version({
            version: 2,
            head: true,
            change_summary: [{ path: 'indicators.rsi_ind.window', old: 14, new: 21 }],
          }),
          version({ version: 1 }),
        ],
        runs: [],
      }),
    )
    open()

    expect(await screen.findByText(/rsi_ind\.window 14 → 21/)).toBeInTheDocument()
  })

  it('flags a version that moved the universe, because it invalidates every earlier run', async () => {
    server.use(
      ...answering({
        versions: [
          version({ version: 2, head: true, flags: ['universe_changed'] }),
          version({ version: 1 }),
        ],
        runs: [],
      }),
    )
    open()

    expect(await screen.findByText('Universe changed')).toBeInTheDocument()
  })

  it('says a version was created by a restore, and from where', async () => {
    server.use(
      ...answering({
        versions: [
          version({ version: 3, head: true, origin: 'restored', restored_from: 1 }),
          version({ version: 2 }),
          version({ version: 1 }),
        ],
        runs: [],
      }),
    )
    open()

    expect(await screen.findByText('restored from v1')).toBeInTheDocument()
  })

  it('will not offer to restore the head', async () => {
    server.use(...answering({ versions: [version({ version: 1, head: true })], runs: [] }))
    open()

    expect(await screen.findByLabelText('Restore v1')).toBeDisabled()
  })
})

describe('the comparison table', () => {
  it('explains itself when no run has succeeded', async () => {
    server.use(...answering({ versions: [version({ head: true })], runs: [] }))
    open()

    expect(await screen.findByText(/No succeeded runs yet/i)).toBeInTheDocument()
  })

  it('leaves a version with no run of the selected kind empty rather than zeroed', async () => {
    server.use(
      ...answering({
        versions: [version({ version: 2, head: true }), version({ version: 1 })],
        runs: [runFixture({ id: 'a', version: 1, kind: 'backtest' })],
        results: {
          a: {
            metrics: {
              total_return_pct: 12,
              max_drawdown_pct: -8,
              sharpe_ratio: 1,
              total_trades: 40,
              has_enough_trades_to_judge: true,
            },
            vintage: { frame_digest: 'frame-a' },
          },
        },
      }),
    )
    open()

    const row = await screen.findByRole('row', { name: /v2/ })
    expect(within(row).queryByText(/0\.0%/)).not.toBeInTheDocument()
    expect(within(row).getAllByText('—').length).toBeGreaterThan(0)
  })

  it('shows the trade count but no figures below the trade floor', async () => {
    server.use(
      ...answering({
        versions: [version({ version: 1, head: true })],
        runs: [runFixture({ id: 'a', version: 1, kind: 'backtest' })],
        results: {
          a: {
            metrics: {
              total_return_pct: 300,
              total_trades: 6,
              has_enough_trades_to_judge: false,
            },
          },
        },
      }),
    )
    open()

    expect(await screen.findByText(/below the trade floor/i)).toBeInTheDocument()
    expect(screen.queryByText('+300.0%')).not.toBeInTheDocument()
  })

  it('refuses a Δ against a run that maximised something else', async () => {
    server.use(
      ...answering({
        versions: [version({ version: 2, head: true }), version({ version: 1 })],
        runs: [
          runFixture({ id: 'a', version: 1, kind: 'walk_forward' }),
          runFixture({ id: 'b', version: 2, kind: 'walk_forward' }),
        ],
        results: {
          a: walkForward({ objective: 'sharpe', combined_return_pct: 4 }),
          b: walkForward({ objective: 'calmar', combined_return_pct: 10 }),
        },
      }),
    )
    open()

    // Both runs reported a return, six points apart, so there is an arithmetic difference to be
    // had. It must not be offered: the two searches were maximising different things.
    const row = await screen.findByRole('row', { name: /v2/ })
    expect(await within(row).findByText('+10.0%')).toBeInTheDocument()
    expect(within(row).queryByText(/^return \+/)).not.toBeInTheDocument()
    expect(within(row).queryByText(/vs v1/)).not.toBeInTheDocument()
  })

  it('does compute the Δ when the two runs were measuring the same thing', async () => {
    server.use(
      ...answering({
        versions: [version({ version: 2, head: true }), version({ version: 1 })],
        runs: [
          runFixture({ id: 'a', version: 1, kind: 'walk_forward' }),
          runFixture({ id: 'b', version: 2, kind: 'walk_forward' }),
        ],
        results: {
          a: walkForward({ combined_return_pct: 4 }),
          b: walkForward({ combined_return_pct: 10 }),
        },
      }),
    )
    open()

    const row = await screen.findByRole('row', { name: /v2/ })
    expect(await within(row).findByText('return +6.0pp')).toBeInTheDocument()
    expect(within(row).getByText('vs v1')).toBeInTheDocument()
  })

  it('caveats a Δ between runs that read different price frames', async () => {
    server.use(
      ...answering({
        versions: [version({ version: 2, head: true }), version({ version: 1 })],
        runs: [
          runFixture({ id: 'a', version: 1, kind: 'walk_forward' }),
          runFixture({ id: 'b', version: 2, kind: 'walk_forward' }),
        ],
        results: {
          a: walkForward({ combined_return_pct: 4, vintage: { frame_digest: 'older-frame' } }),
          b: walkForward({ combined_return_pct: 10 }),
        },
      }),
    )
    open()

    const row = await screen.findByRole('row', { name: /v2/ })
    expect(await within(row).findByText('return +6.0pp')).toBeInTheDocument()
    // The movement is still shown — it is honest arithmetic on two real runs — but it carries
    // the mark saying it may be the history that moved rather than the strategy.
    expect(row.querySelector('svg')).not.toBeNull()
  })

  it('defaults to walk-forward, the only kind that issues a verdict', async () => {
    server.use(
      ...answering({
        versions: [version({ version: 1, head: true })],
        runs: [
          runFixture({ id: 'a', version: 1, kind: 'backtest' }),
          runFixture({ id: 'b', version: 1, kind: 'walk_forward' }),
        ],
        results: { b: walkForward({}) },
      }),
    )
    open()

    expect(await screen.findByText('not credible')).toBeInTheDocument()
  })

  it('says why a walk-forward has no drawdown or Sharpe rather than leaving it a mystery', async () => {
    server.use(
      ...answering({
        versions: [version({ version: 1, head: true })],
        runs: [runFixture({ id: 'b', version: 1, kind: 'walk_forward' })],
        results: { b: walkForward({}) },
      }),
    )
    open()

    expect(await screen.findByText(/Every fold re-optimizes from scratch/i)).toBeInTheDocument()
  })

  it('notes that the latest run is reported when a version has several', async () => {
    server.use(
      ...answering({
        versions: [version({ version: 1, head: true })],
        runs: [
          runFixture({ id: 'a', version: 1, kind: 'backtest', queued_at: '2026-01-01T00:00:00Z' }),
          runFixture({ id: 'b', version: 1, kind: 'backtest', queued_at: '2026-02-01T00:00:00Z' }),
        ],
        results: { b: { metrics: { has_enough_trades_to_judge: true, total_trades: 40 } } },
      }),
    )
    open()

    expect(await screen.findByText('latest shown')).toBeInTheDocument()
  })
})

describe('the permanent warnings', () => {
  it('states both, on a screen with no runs at all', async () => {
    server.use(...answering({ versions: [version({ head: true })], runs: [] }))
    open()

    expect(
      await screen.findByText(/Editing and re-running is itself a search/i),
    ).toBeInTheDocument()
    expect(screen.getByText(/chosen knowing how earlier ones did/i)).toBeInTheDocument()
  })
})

describe('restoring', () => {
  it('says it appends rather than rewinds, and names the version it will create', async () => {
    server.use(
      ...answering({
        versions: [version({ version: 2, head: true }), version({ version: 1, runs_against: 3 })],
        runs: [],
      }),
      http.get('*/api/v1/strategies/:id/diff', () =>
        HttpResponse.json({
          from_version: { version: 2, head: true },
          to_version: { version: 1, head: false },
          groups: [],
          from_yaml: 'exit:\n  stop_loss_pct: 9.0\n',
          to_yaml: 'exit:\n  stop_loss_pct: 5.0\n',
        }),
      ),
    )
    open()

    await userEvent.click(await screen.findByLabelText('Restore v1'))

    expect(await screen.findByText(/Nothing is rewound/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Restore as v3/ })).toBeInTheDocument()
  })
})
