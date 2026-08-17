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

function openDialog(kind: RunKind = 'optimize') {
  return renderWithProviders(
    <StrategyContext value={{ strategy: strategyDetail() }}>
      <LaunchRunModal kind={kind} onClose={() => {}} opened />
    </StrategyContext>,
  )
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
