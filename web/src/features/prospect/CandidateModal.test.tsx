import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { candidate } from '../../test/fixtures'
import { CandidateModal } from './CandidateModal'
import type { ProspectCandidate } from '../../api/types'

function open(detail: Record<string, unknown> = {}) {
  const row = candidate({ forward_history: [], ...detail })
  server.use(http.get('*/api/v1/prospect/candidates/:id', () => HttpResponse.json(row)))
  return renderWithProviders(
    <CandidateModal candidate={row as unknown as ProspectCandidate} onClose={() => {}} />,
  )
}

describe('CandidateModal', () => {
  it('labels the holdout figures as what the search selected on, not as evidence', async () => {
    open()

    expect(await screen.findByText(/What the search selected on/)).toBeInTheDocument()
    expect(screen.getByText('+23.9%')).toBeInTheDocument()
    // The caption has to carry the reason, not just the label: 302 configurations were tried,
    // and the best of that many looks good on its own sample whether or not it works.
    expect(screen.getByText(/302 configurations were tried/)).toBeInTheDocument()
  })

  it('puts the two deciding rungs above the one that decides nothing', async () => {
    const { container } = open()
    await screen.findByText(/What the search selected on/)

    const headings = [...container.querySelectorAll('h5')].map((node) => node.textContent ?? '')
    const transfer = headings.findIndex((text) => text.startsWith('Transfer'))
    const forward = headings.findIndex((text) => text.startsWith('Forward'))
    const selected = headings.findIndex((text) => text.startsWith('What the search'))

    expect(transfer).toBeGreaterThanOrEqual(0)
    expect(forward).toBeGreaterThan(transfer)
    expect(selected).toBeGreaterThan(forward)
  })

  it('gives the reason a candidate was rejected rather than only the fact', async () => {
    open({
      survived_transfer: false,
      transfer: {
        ...candidate().transfer,
        median_sibling_sharpe: -0.4,
        survives: false,
      },
    })

    expect(await screen.findByText(/Across its family it lost money/)).toBeInTheDocument()
  })

  it('distinguishes losing across the family from merely being long the market', async () => {
    open({
      survived_transfer: false,
      transfer: {
        ...candidate().transfer,
        median_sibling_sharpe: 0.5,
        beats_controls: false,
        survives: false,
      },
    })

    expect(await screen.findByText(/no more than on instruments sharing none/)).toBeInTheDocument()
  })

  it('warns when the holdout figures rest on too few trades', async () => {
    open({ selected_on: { ...candidate().selected_on, holdout_trades: 3 } })

    expect(await screen.findByText(/3 closed trades is too few/)).toBeInTheDocument()
  })

  it('says forward evidence is still accumulating rather than showing nothing', async () => {
    open({ forward: null, forward_history: [] })

    expect(await screen.findByText(/Not measured yet/)).toBeInTheDocument()
  })

  it('shows the whole forward series, because the decay is only visible as a sequence', async () => {
    open({
      forward_history: [
        {
          scored_at: '2026-08-27T00:00:00Z',
          first_bar: '2026-08-20',
          last_bar: '2026-08-26',
          bars: 35,
          return_pct: 4.0,
          sharpe: 1.0,
          trades: 4,
        },
        {
          scored_at: '2026-09-03T00:00:00Z',
          first_bar: '2026-08-20',
          last_bar: '2026-09-02',
          bars: 70,
          return_pct: -1.2,
          sharpe: -0.3,
          trades: 9,
        },
      ],
    })

    expect(await screen.findByText('+4.0%')).toBeInTheDocument()
    expect(screen.getByText('-1.2%')).toBeInTheDocument()
  })

  it('names the siblings that could not be measured at all', async () => {
    open({
      transfer: {
        ...candidate().transfer,
        failures: ['TLT (no usable Sharpe over 7 trades)'],
      },
    })

    expect(await screen.findByText(/TLT \(no usable Sharpe over 7 trades\)/)).toBeInTheDocument()
  })
})
