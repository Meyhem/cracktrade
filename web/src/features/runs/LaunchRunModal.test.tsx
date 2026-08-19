import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { screen, waitFor } from '@testing-library/react'
import { LaunchRunModal } from './LaunchRunModal'
import { StrategyContext } from '../strategy/context'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { strategyDetail } from '../../test/fixtures'
import type { RunKind } from '../../api/types'

/**
 * The launch dialog, and specifically the guard that says there is nothing to search.
 *
 * Worth its own file because that guard shipped unreachable: the parameter count was passed as
 * a literal `1` from both call sites, so the warning was written, rendered nowhere, and would
 * have stayed that way until a user pinned every field and watched the engine refuse a run the
 * dialog had just told them was fine.
 */

const NOTHING_TO_SEARCH = /nothing for the search to move/i

function validateWith(paths: string[]) {
  return http.post('*/api/v1/config/validate', () =>
    HttpResponse.json({
      valid: true,
      errors: [],
      warnings: [],
      canonical_yaml: 'strategy:\n  name: rsi_pullback\n',
      config: {},
      namespace: ['close', 'rsi_ind'],
      searchable_parameters: paths.map((path) => ({ path, value: 14, low: 7, high: 21 })),
    }),
  )
}

function openDialog(kind: RunKind = 'optimize', strategy = strategyDetail()) {
  return renderWithProviders(
    <StrategyContext value={{ strategy }}>
      <LaunchRunModal kind={kind} onClose={() => {}} opened />
    </StrategyContext>,
  )
}

/** A chassis whose date range is far too short for the block library's warm-up. */
function shortHistory() {
  return withUniverse({ ticker: 'NVDA', start_date: '2025-01-01', end_date: '2025-12-31' })
}

/**
 * Seven weeks of 30-minute bars — the whole reach the provider serves at that interval, and
 * about 629 Xetra bars, comfortably enough to divide.
 */
function halfHourly() {
  return withUniverse({
    ticker: 'SAP.DE',
    start_date: '2026-06-29',
    end_date: '2026-08-18',
    interval: '30m',
  })
}

function withUniverse(universe: Record<string, string>) {
  const base = strategyDetail()
  return {
    ...base,
    head: { ...base.head, config: { strategy: { name: 'rsi_pullback' }, universe } },
  }
}

describe('the nothing-to-search guard', () => {
  it('warns and blocks the run when every field is pinned', async () => {
    server.use(validateWith([]))
    openDialog('optimize')

    expect(await screen.findByText(NOTHING_TO_SEARCH)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run' })).toBeDisabled()
  })

  it('stays quiet when the search has something to move', async () => {
    server.use(validateWith(['indicators.rsi_ind.window']))
    openDialog('optimize')

    await waitFor(() => expect(screen.getByRole('button', { name: 'Run' })).toBeEnabled())
    expect(screen.queryByText(NOTHING_TO_SEARCH)).not.toBeInTheDocument()
  })

  it('does not warn while the count is still unknown', async () => {
    // A dialog that says "nothing to search" because a request is in flight has told the user
    // something false about their configuration. Unknown and zero are different facts.
    server.use(http.post('*/api/v1/config/validate', () => new Promise(() => {})))
    openDialog('optimize')

    expect(await screen.findByRole('button', { name: 'Run' })).toBeEnabled()
    expect(screen.queryByText(NOTHING_TO_SEARCH)).not.toBeInTheDocument()
  })

  it('never applies to a backtest, which searches nothing by definition', async () => {
    server.use(validateWith([]))
    openDialog('backtest')

    expect(await screen.findByText(/exactly as written/i)).toBeInTheDocument()
    expect(screen.queryByText(NOTHING_TO_SEARCH)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run' })).toBeEnabled()
  })
})

describe('the evolution branch', () => {
  it('states the search budget as a cost in credibility, not only in minutes', async () => {
    // The reason this dialog differs from the other three. Every other budget control trades
    // time for thoroughness; here a bigger number also raises the bar the answer must clear,
    // and a form that showed only the runtime would invite the user to turn it up.
    server.use(validateWith(['indicators.rsi_ind.window']))
    openDialog('evolve')

    expect(await screen.findByText(/Up to 1,000 strategies will be tried/i)).toBeInTheDocument()
    expect(
      screen.getByText(/divides the winner’s result by how many attempts/i),
    ).toBeInTheDocument()
  })

  it('is not blocked by a fully pinned configuration', async () => {
    // Evolution composes its own indicators, so there is nothing of the base strategy for the
    // nothing-to-search guard to be about.
    server.use(validateWith([]))
    openDialog('evolve')

    expect(await screen.findByRole('button', { name: 'Compose' })).toBeEnabled()
    expect(screen.queryByText(NOTHING_TO_SEARCH)).not.toBeInTheDocument()
  })

  it('refuses a date range too short to divide, before the run is queued', async () => {
    server.use(validateWith(['indicators.rsi_ind.window']))
    openDialog('evolve', shortHistory())

    expect(await screen.findByText(/date range is about/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Compose' })).toBeDisabled()
  })

  it('does not measure an intraday chassis in trading days', async () => {
    // The guard compares a *day* count against a floor counted in *bars*, which is only the
    // same thing on daily data. Seven weeks of 30-minute bars is 37 trading days and 629 bars;
    // read as days it looked like a twentieth of what it is, and the dialog disabled Compose on
    // a run the engine accepts. How many bars a session holds belongs to the exchange — 17 on
    // Xetra, 13 in New York — so the client does not guess, and the server refuses with the
    // exact arithmetic if the range really is too short.
    server.use(validateWith(['indicators.rsi_ind.window']))
    openDialog('evolve', halfHourly())

    expect(await screen.findByRole('button', { name: 'Compose' })).toBeEnabled()
    expect(screen.queryByText(/date range is about/i)).not.toBeInTheDocument()
  })

  it('offers no epoch count, which evolution does not have', async () => {
    server.use(validateWith(['indicators.rsi_ind.window']))
    openDialog('evolve')

    expect(await screen.findByLabelText('Population')).toBeInTheDocument()
    expect(screen.queryByLabelText('Epochs')).not.toBeInTheDocument()
  })
})
