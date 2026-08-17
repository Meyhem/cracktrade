import { describe, expect, it } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { renderWithProviders } from '../../test/render'
import { server, problem } from '../../test/server'
import { strategyRow } from '../../test/fixtures'
import { StrategyListPage } from './StrategyListPage'

describe('StrategyListPage', () => {
  it('shows a row per strategy with its verdict and run counts', async () => {
    renderWithProviders(<StrategyListPage />)

    const row = await screen.findByRole('row', { name: /rsi_pullback/ })
    expect(within(row).getByText('NVDA')).toBeInTheDocument()
    expect(within(row).getByText('2 opt · 1 bt')).toBeInTheDocument()
    expect(within(row).getByText('Unvalidated')).toBeInTheDocument()
  })

  it('says a strategy has never run rather than showing an empty cell', async () => {
    server.use(
      http.get('*/api/v1/strategies', () =>
        HttpResponse.json({
          strategies: [
            strategyRow({
              name: 'untouched',
              verdict: 'never_run',
              optimize_runs: 0,
              backtest_runs: 0,
              walk_forward_runs: 0,
              last_run_id: null,
              last_run_kind: null,
              last_run_status: null,
              last_run_at: null,
            }),
          ],
          totals: { all: 1, credible: 0, not_credible: 0, unvalidated: 0, never_run: 1 },
        }),
      ),
    )

    renderWithProviders(<StrategyListPage />)

    const row = await screen.findByRole('row', { name: /untouched/ })
    expect(within(row).getByText('never run')).toBeInTheDocument()
    expect(within(row).getByText('none')).toBeInTheDocument()
    expect(within(row).getByText('Never run')).toBeInTheDocument()
  })

  it('marks a promoted strategy with a link back to the parent it came from', async () => {
    server.use(
      http.get('*/api/v1/strategies', () =>
        HttpResponse.json({
          strategies: [
            strategyRow({
              name: 'promoted_child',
              lineage: {
                origin: 'promoted',
                parent_strategy_id: '99999999-9999-9999-9999-999999999999',
                parent_version: 2,
                origin_run_id: '88888888-8888-8888-8888-888888888888',
              },
              origin_not_credible: true,
            }),
          ],
          totals: { all: 1, credible: 0, not_credible: 0, unvalidated: 1, never_run: 0 },
        }),
      ),
    )

    renderWithProviders(<StrategyListPage />)

    const marker = await screen.findByRole('link', { name: 'promoted' })
    expect(marker).toHaveAttribute('href', '/strategies/99999999-9999-9999-9999-999999999999')
  })

  it('opens the create form and posts what was typed', async () => {
    const posted: unknown[] = []
    server.use(
      http.post('*/api/v1/strategies', async ({ request }) => {
        posted.push(await request.json())
        return HttpResponse.json(
          {
            strategy: {
              id: '33333333-3333-3333-3333-333333333333',
              name: 'new_one',
              created_at: '2026-08-17T10:00:00Z',
              lineage: { origin: 'authored' },
              origin_not_credible: false,
              head: {
                version: 1,
                origin: 'authored',
                restored_from: null,
                note: null,
                created_at: '2026-08-17T10:00:00Z',
                config: {},
                yaml: 'strategy:\n  name: new_one\n',
              },
              counts: { optimize: 0, backtest: 0, walk_forward: 0, versions: 1 },
              verdict: { state: 'never_run', failures: [], checks: [] },
              verdict_run_id: null,
              promoted_warning: null,
            },
            warnings: [],
          },
          { status: 201 },
        )
      }),
    )

    const user = userEvent.setup()
    renderWithProviders(<StrategyListPage />)

    await user.click(await screen.findByRole('button', { name: 'New strategy' }))

    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Name'), 'new_one')
    await user.type(within(dialog).getByLabelText('Ticker'), 'msft')
    await user.click(within(dialog).getByRole('button', { name: 'Create' }))

    await waitFor(() => expect(posted).toHaveLength(1))
    expect(posted[0]).toMatchObject({
      name: 'new_one',
      // Typed lower case; the ticker is a symbol, and the engine expects it upper case.
      ticker: 'MSFT',
      seed: 'minimal',
    })
  })

  it('refuses to submit a date range that runs backwards, and says why', async () => {
    const user = userEvent.setup()
    renderWithProviders(<StrategyListPage />)

    await user.click(await screen.findByRole('button', { name: 'New strategy' }))
    const dialog = await screen.findByRole('dialog')

    await user.type(within(dialog).getByLabelText('Name'), 'backwards')
    await user.type(within(dialog).getByLabelText('Ticker'), 'NVDA')
    const endDate = within(dialog).getByLabelText('End date')
    await user.clear(endDate)
    await user.type(endDate, '2000-01-01')
    await user.click(within(dialog).getByRole('button', { name: 'Create' }))

    expect(
      await within(dialog).findByText('The end date must be after the start date.'),
    ).toBeInTheDocument()
  })

  it("shows the server's own message when a name is already taken", async () => {
    server.use(
      http.post('*/api/v1/strategies', () =>
        problem({
          status: 409,
          slug: 'conflict',
          title: 'Conflict',
          detail: 'a strategy named rsi_pullback already exists',
        }),
      ),
    )

    const user = userEvent.setup()
    renderWithProviders(<StrategyListPage />)

    await user.click(await screen.findByRole('button', { name: 'New strategy' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Name'), 'rsi_pullback')
    await user.type(within(dialog).getByLabelText('Ticker'), 'NVDA')
    await user.click(within(dialog).getByRole('button', { name: 'Create' }))

    expect(
      await within(dialog).findByText('a strategy named rsi_pullback already exists'),
    ).toBeInTheDocument()
  })

  it('explains an empty list instead of showing a bare table', async () => {
    server.use(
      http.get('*/api/v1/strategies', () =>
        HttpResponse.json({
          strategies: [],
          totals: { all: 0, credible: 0, not_credible: 0, unvalidated: 0, never_run: 0 },
        }),
      ),
    )

    renderWithProviders(<StrategyListPage />)

    expect(await screen.findByText('No strategies yet')).toBeInTheDocument()
    expect(screen.getByText(/walk-forward it before believing anything/)).toBeInTheDocument()
  })
})
