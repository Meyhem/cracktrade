import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '../../test/render'
import { StartSweepModal } from './StartSweepModal'

function open() {
  return renderWithProviders(<StartSweepModal onClose={() => {}} opened />, { route: '/prospect' })
}

describe('StartSweepModal', () => {
  it('explains why the search budget is small rather than inviting it to be raised', async () => {
    open()

    expect(await screen.findByText(/300 configurations per ticker/)).toBeInTheDocument()
    // The point a budget field alone would hide: a bigger search raises its own bar.
    expect(screen.getByText(/grows with the number of configurations tried/)).toBeInTheDocument()
  })

  it('will not start a sweep without a name', async () => {
    open()

    expect(await screen.findByRole('button', { name: 'Start' })).toBeDisabled()
  })

  it('normalises tickers as they are typed, so the sweep is what the user sees', async () => {
    const user = userEvent.setup()
    open()

    await user.type(await screen.findByLabelText('Tickers'), 'amd, nvda')

    expect(screen.getByText('AMD')).toBeInTheDocument()
    expect(screen.getByText('NVDA')).toBeInTheDocument()
  })

  it('says the default universe is the screened one when none is given', async () => {
    open()

    expect(
      await screen.findByText(/twenty day-trading instruments that were screened/),
    ).toBeInTheDocument()
  })

  it('warns that intraday intervals reach far less history', async () => {
    open()

    expect(
      await screen.findByText(/The provider serves far less intraday history/),
    ).toBeInTheDocument()
  })
})
