import { describe, expect, it } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { candidate, sweep } from '../../test/fixtures'
import { SessionPage } from './SessionPage'

const SESSION_ID = '55555555-5555-5555-5555-555555555555'

function serve(session: Record<string, unknown>, candidates: Record<string, unknown>[]) {
  server.use(
    http.get('*/api/v1/prospect/sessions/:id', () => HttpResponse.json(session)),
    http.get('*/api/v1/prospect/candidates', () =>
      HttpResponse.json({ candidates, total: candidates.length }),
    ),
  )
}

function renderPage() {
  return renderWithProviders(<SessionPage />, { route: `/prospect/${SESSION_ID}` })
}

describe('SessionPage', () => {
  it('states progress as counts, never as a percentage of something endless', async () => {
    serve(sweep(), [candidate()])
    renderPage()

    // The two numbers §19.9 says are the honest statement of where a sweep has got to.
    // "Candidates" appears twice — the stat and the section heading — so this asserts on the
    // count beside it rather than on the word.
    expect(await screen.findByText('6 (+1 failed)')).toBeInTheDocument()
    expect(screen.getAllByText('Candidates').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Survived transfer').length).toBeGreaterThan(0)

    // The only bar on the page covers the current lap, which has a denominator. Nothing
    // claims a percentage for the sweep as a whole.
    expect(screen.getByText('2 of 4 tickers this lap')).toBeInTheDocument()
    expect(screen.queryByText(/% complete/)).not.toBeInTheDocument()
  })

  it('labels the backtest and forward columns and never pools them', async () => {
    serve(sweep(), [candidate()])
    renderPage()

    expect(await screen.findByText('Transfer (backtest)')).toBeInTheDocument()
    expect(screen.getByText('Forward (live bars)')).toBeInTheDocument()
  })

  it('says "not yet" for a candidate with no forward evidence, never a zero', async () => {
    serve(sweep(), [candidate({ forward: null, forward_scores: 0 })])
    renderPage()

    const row = await screen.findByRole('row', { name: /MU/ })
    expect(within(row).getByText('not yet')).toBeInTheDocument()
    // "measured and flat" and "not measured" must not render the same.
    expect(within(row).queryByText('+0.0%')).not.toBeInTheDocument()
  })

  it('shows a measured forward result with the bars behind it', async () => {
    serve(sweep(), [
      candidate({
        forward: {
          scored_at: '2026-09-01T00:00:00Z',
          first_bar: '2026-08-20',
          last_bar: '2026-08-31',
          bars: 70,
          return_pct: 3.5,
          sharpe: 0.9,
          trades: 6,
        },
        forward_scores: 1,
      }),
    ])
    renderPage()

    const row = await screen.findByRole('row', { name: /MU/ })
    expect(within(row).getByText('+3.5%')).toBeInTheDocument()
    // The sample size travels with the figure, so 70 bars cannot be read as a year.
    expect(within(row).getByText('70 bars, 6 trades')).toBeInTheDocument()
  })

  it('keeps the holdout figure out of the table entirely', async () => {
    serve(sweep(), [candidate()])
    renderPage()

    await screen.findByRole('row', { name: /MU/ })
    // 23.9% is the number the search selected on and the one a reader would rank by. It is
    // not a column, so it cannot be scanned down or sorted.
    expect(screen.queryByText('+23.9%')).not.toBeInTheDocument()
    expect(screen.queryByText(/Holdout/)).not.toBeInTheDocument()
  })

  it('marks a rejected candidate as rejected rather than omitting the reason', async () => {
    serve(sweep(), [candidate({ survived_transfer: false })])
    renderPage()

    const row = await screen.findByRole('row', { name: /MU/ })
    expect(within(row).getByText('rejected')).toBeInTheDocument()
  })

  it('offers a stop that says it is stopping rather than pretending it stopped', async () => {
    serve(sweep({ stop_requested: true }), [])
    renderPage()

    expect(await screen.findByText('Stopping')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Stopping/ })).toBeDisabled()
  })

  it('offers resume on a stopped sweep, not stop', async () => {
    serve(sweep({ status: 'stopped', stopped_at: '2026-08-21T09:33:17Z', claimed_by: null }), [])
    renderPage()

    expect(await screen.findByRole('button', { name: /Resume/ })).toBeEnabled()
    expect(screen.queryByRole('button', { name: /Stop/ })).not.toBeInTheDocument()
  })

  it('offers resume on a failed sweep too, so an outage is recoverable', async () => {
    serve(
      sweep({
        status: 'failed',
        stopped_at: '2026-08-21T09:33:17Z',
        claimed_by: null,
        error: { message: 'the provider refused every ticker' },
      }),
      [],
    )
    renderPage()

    expect(await screen.findByRole('button', { name: /Resume/ })).toBeEnabled()
    expect(screen.getByText('the provider refused every ticker')).toBeInTheDocument()
  })

  it('resumes the sweep it is showing when resume is pressed', async () => {
    let resumed: string | null = null
    serve(sweep({ status: 'stopped', stopped_at: '2026-08-21T09:33:17Z', claimed_by: null }), [])
    server.use(
      http.post('*/api/v1/prospect/sessions/:id/resume', ({ params }) => {
        resumed = String(params.id)
        return HttpResponse.json(sweep())
      }),
    )
    renderPage()

    await userEvent.click(await screen.findByRole('button', { name: /Resume/ }))

    await waitFor(() => expect(resumed).toBe(SESSION_ID))
  })

  it('says a sweep is waiting when no worker has picked it up', async () => {
    serve(sweep({ claimed_by: null, heartbeat_at: null }), [])
    renderPage()

    expect(await screen.findByText('Waiting for a worker')).toBeInTheDocument()
  })

  it('explains an empty survivor list instead of looking broken', async () => {
    serve(sweep({ survivors: 0 }), [])
    renderPage()

    expect(await screen.findByText('Nothing has survived transfer yet')).toBeInTheDocument()
  })
})
