import { describe, expect, it } from 'vitest'
import { screen, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { sweep } from '../../test/fixtures'
import { ProspectPage } from './ProspectPage'

function serve(sessions: Record<string, unknown>[]) {
  server.use(
    http.get('*/api/v1/prospect/sessions', () =>
      HttpResponse.json({ sessions, total: sessions.length }),
    ),
  )
}

describe('ProspectPage', () => {
  it('says what a sweep publishes before it lists any', async () => {
    serve([])
    renderWithProviders(<ProspectPage />, { route: '/prospect' })

    // The word is "candidates", and the page says what that excludes.
    expect(await screen.findByText(/It publishes/)).toBeInTheDocument()
    expect(screen.getByText(/never verdicts/)).toBeInTheDocument()
  })

  it('explains what a sweep does when there are none, rather than shrugging', async () => {
    serve([])
    renderWithProviders(<ProspectPage />, { route: '/prospect' })

    expect(await screen.findByText('No sweeps yet')).toBeInTheDocument()
    expect(screen.getByText(/rejected there and then/)).toBeInTheDocument()
  })

  it('reports a sweep by laps and counts, not by percentage', async () => {
    serve([sweep()])
    renderWithProviders(<ProspectPage />, { route: '/prospect' })

    const row = await screen.findByRole('row', { name: /overnight semis/ })
    expect(within(row).getByText('SOXL')).toBeInTheDocument()
    expect(within(row).getByText('4 tickers')).toBeInTheDocument()
    expect(within(row).getByText(/\(\+1 failed\)/)).toBeInTheDocument()
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
  })

  it('shows no current ticker for a sweep that has stopped', async () => {
    serve([sweep({ status: 'stopped', stopped_at: '2026-08-20T20:10:00Z', claimed_by: null })])
    renderWithProviders(<ProspectPage />, { route: '/prospect' })

    const row = await screen.findByRole('row', { name: /overnight semis/ })
    expect(within(row).getByText('Stopped')).toBeInTheDocument()
    expect(within(row).getByText('—')).toBeInTheDocument()
  })
})
