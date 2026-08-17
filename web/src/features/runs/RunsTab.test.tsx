import { describe, expect, it } from 'vitest'
import { screen, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { run } from '../../test/fixtures'
import { StrategyContext } from '../strategy/context'
import { RunsTab } from './RunsTab'
import type { StrategyDetail } from '../../api/types'

const strategy = {
  id: '11111111-1111-1111-1111-111111111111',
  name: 'rsi_pullback',
  created_at: '2026-08-01T09:00:00Z',
  lineage: { origin: 'authored' },
  origin_not_credible: false,
  head: {
    version: 3,
    origin: 'edited',
    restored_from: null,
    note: null,
    created_at: '2026-08-10T09:00:00Z',
    config: {},
    yaml: '',
  },
  counts: { optimize: 1, backtest: 1, walk_forward: 0, versions: 3 },
  verdict: { state: 'unvalidated', failures: [], checks: [] },
  verdict_run_id: null,
  promoted_warning: null,
} as unknown as StrategyDetail

function renderTab(kind: 'backtest' | 'optimize' | 'walk_forward') {
  return renderWithProviders(
    <StrategyContext value={{ strategy }}>
      <RunsTab kind={kind} />
    </StrategyContext>,
  )
}

describe('RunsTab', () => {
  it('renders the figures a run cleared the floor to report', async () => {
    server.use(http.get('*/api/v1/runs', () => HttpResponse.json({ runs: [run()], total: 1 })))

    renderTab('backtest')

    const row = await screen.findByRole('row', { name: /#4/ })
    expect(within(row).getByText('+31.4%')).toBeInTheDocument()
    expect(within(row).getByText('+149.0%')).toBeInTheDocument()
    // The excess is negative and stays negative: beating nothing is not a result.
    expect(within(row).getByText('-117.6pp')).toBeInTheDocument()
  })

  it('says too few trades instead of printing figures the server withheld', async () => {
    server.use(
      http.get('*/api/v1/runs', () =>
        HttpResponse.json({
          runs: [
            run({
              // What the server actually sends below the floor: the counts, the flag, and
              // none of the performance keys at all.
              headline: {
                trades: 6,
                entry_defined_pct: 41.0,
                suppressed: true,
                trade_floor: 20,
              },
            }),
          ],
          total: 1,
        }),
      ),
    )

    renderTab('backtest')

    const row = await screen.findByRole('row', { name: /#4/ })
    expect(within(row).getAllByText('too few trades').length).toBeGreaterThan(0)
    expect(within(row).queryByText(/%/)).not.toBeInTheDocument()
    // The trade count itself is still shown: it is the evidence for the suppression.
    expect(within(row).getByText('6')).toBeInTheDocument()
  })

  it('marks a run made against an older version as stale', async () => {
    server.use(
      http.get('*/api/v1/runs', () =>
        HttpResponse.json({ runs: [run({ version: 1, stale: true })], total: 1 }),
      ),
    )

    renderTab('backtest')

    const row = await screen.findByRole('row', { name: /#4/ })
    expect(within(row).getByText('stale')).toBeInTheDocument()
  })

  it('offers to cancel a running run and not a finished one', async () => {
    server.use(
      http.get('*/api/v1/runs', () =>
        HttpResponse.json({
          runs: [
            run({
              id: '55555555-5555-5555-5555-555555555555',
              number: 5,
              status: 'running',
              headline: null,
              finished_at: null,
            }),
            run({ id: '44444444-4444-4444-4444-444444444444', number: 4, status: 'succeeded' }),
          ],
          total: 2,
        }),
      ),
    )

    renderTab('backtest')

    const running = await screen.findByRole('row', { name: /#5/ })
    expect(within(running).getByRole('button', { name: 'Cancel' })).toBeInTheDocument()

    const finished = screen.getByRole('row', { name: /#4/ })
    expect(within(finished).queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument()
  })

  it('does not offer to cancel again once cancellation was asked for', async () => {
    server.use(
      http.get('*/api/v1/runs', () =>
        HttpResponse.json({
          runs: [run({ status: 'running', cancel_requested: true, headline: null })],
          total: 1,
        }),
      ),
    )

    renderTab('backtest')

    const row = await screen.findByRole('row', { name: /#4/ })
    expect(within(row).queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument()
    expect(within(row).getByText('Cancelling')).toBeInTheDocument()
  })

  it('explains what a walk-forward is for rather than shrugging at an empty tab', async () => {
    server.use(http.get('*/api/v1/runs', () => HttpResponse.json({ runs: [], total: 0 })))

    renderTab('walk_forward')

    expect(await screen.findByText('Not validated')).toBeInTheDocument()
    expect(screen.getByText(/only run that issues a verdict/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run a walk-forward' })).toBeInTheDocument()
  })
})
