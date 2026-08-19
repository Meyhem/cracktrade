import { describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { DeleteStrategyModal } from './DeleteStrategyModal'
import { StrategyContext } from './context'
import { renderWithProviders } from '../../test/render'
import { problem, server } from '../../test/server'
import { strategyDetail } from '../../test/fixtures'
import type { StrategyDetail } from '../../api/types'

/**
 * The one destructive dialog.
 *
 * A plain confirm, so what is tested is what the user is told before they press it — the
 * counts and the word "permanently" — and what happens to the two server refusals, which are
 * the only outcomes the user can act on.
 */

const DELETE_URL = '*/api/v1/strategies/:id'

function open(overrides: Partial<StrategyDetail> = {}) {
  const strategy = strategyDetail(overrides)
  const onClose = vi.fn()
  const rendered = renderWithProviders(
    <StrategyContext value={{ strategy }}>
      <DeleteStrategyModal onClose={onClose} opened />
    </StrategyContext>,
  )
  return { ...rendered, onClose, strategy }
}

function confirmButton() {
  return screen.getByRole('button', { name: 'Delete permanently' })
}

describe('what it says before the click', () => {
  it('counts what will be destroyed, so the decision is made against the number', () => {
    // The fixture carries 3 versions and 1 + 2 + 0 runs.
    open()

    expect(screen.getByText(/3 configuration versions/)).toBeInTheDocument()
    expect(screen.getByText(/3 runs/)).toBeInTheDocument()
  })

  it('says permanently rather than leaving it to be inferred', () => {
    open()
    // Both the warning and the button say it. "Cannot be undone" reads as boilerplate and is
    // deliberately not the wording used.
    expect(screen.getAllByText(/permanently/)).not.toHaveLength(0)
    expect(screen.queryByText(/cannot be undone/i)).not.toBeInTheDocument()
  })

  it('leaves focus on Cancel, so a reflexive Enter deletes nothing', async () => {
    open()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus())
  })
})

describe('when the server refuses', () => {
  it('shows which strategy is in the way rather than a generic failure', async () => {
    const user = userEvent.setup()
    server.use(
      http.delete(DELETE_URL, () =>
        problem({
          status: 409,
          slug: 'conflict',
          title: 'Conflict',
          detail: 'rsi_pullback cannot be deleted while momentum_v3 descends from it.',
        }),
      ),
    )
    const { onClose } = open()

    await user.click(confirmButton())

    expect(await screen.findByText(/momentum_v3 descends from it/)).toBeInTheDocument()
    // The dialog stays open: the refusal is actionable, and closing would hide it.
    expect(onClose).not.toHaveBeenCalled()
  })

  it('leaves the dialog usable after a refusal', async () => {
    const user = userEvent.setup()
    server.use(
      http.delete(DELETE_URL, () =>
        problem({
          status: 409,
          slug: 'conflict',
          title: 'Conflict',
          detail: 'rsi_pullback has 1 run still queued or running. Cancel them, then delete.',
        }),
      ),
    )
    open()

    await user.click(confirmButton())

    expect(await screen.findByText(/still queued or running/)).toBeInTheDocument()
    expect(confirmButton()).toBeEnabled()
  })
})

describe('when it succeeds', () => {
  it('closes, and reports what actually went rather than what was predicted', async () => {
    const user = userEvent.setup()
    server.use(
      http.delete(DELETE_URL, () =>
        HttpResponse.json({ name: 'rsi_pullback', versions: 3, runs: 8, series: 24 }),
      ),
    )
    const { onClose } = open()

    await user.click(confirmButton())

    await waitFor(() => expect(onClose).toHaveBeenCalled())
    // 8 runs, from the server's receipt -- the header only knew about 3.
    expect(await screen.findByText(/3 versions and 8 runs/)).toBeInTheDocument()
  })

  it('drops the deleted strategy from the cache instead of refetching a 404', async () => {
    const user = userEvent.setup()
    server.use(
      http.delete(DELETE_URL, () =>
        HttpResponse.json({ name: 'rsi_pullback', versions: 3, runs: 0, series: 0 }),
      ),
    )
    const { queryClient, strategy } = open()
    queryClient.setQueryData(['strategies', 'detail', strategy.id], strategy)

    await user.click(confirmButton())

    await waitFor(() =>
      expect(queryClient.getQueryData(['strategies', 'detail', strategy.id])).toBeUndefined(),
    )
  })
})
