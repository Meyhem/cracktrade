import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '../test/render'
import { Explain, ExplainedLabel } from './Explain'
import { GLOSSARY } from '../lib/glossary'

describe('Explain', () => {
  it('exposes the explanation to the keyboard, not only to the mouse', async () => {
    const user = userEvent.setup()
    renderWithProviders(<Explain term="sharpe" />)

    const trigger = screen.getByRole('button', { name: /what does sharpe mean/i })
    await user.tab()
    expect(trigger).toHaveFocus()
    // Focus alone must open it. A tooltip that only answers to hover is unreachable without a
    // pointing device, and invisible on a touch screen.
    expect(await screen.findByText(GLOSSARY.sharpe.plain)).toBeInTheDocument()
  })

  it('shows the caveat as well as the definition', async () => {
    const user = userEvent.setup()
    renderWithProviders(<Explain term="win_rate" />)

    await user.hover(screen.getByRole('button', { name: /what does win rate mean/i }))
    expect(await screen.findByText(GLOSSARY.win_rate.plain)).toBeInTheDocument()
    expect(screen.getByText(new RegExp(GLOSSARY.win_rate.catch))).toBeInTheDocument()
  })

  it('lets a screen say something shorter than the glossary title', () => {
    renderWithProviders(<ExplainedLabel label="Excess" term="excess" />)

    expect(screen.getByText('Excess')).toBeInTheDocument()
    // The full title still names the term in the button, so the short label and the
    // definition cannot read as two different things.
    expect(
      screen.getByRole('button', { name: /what does excess over buy-and-hold mean/i }),
    ).toBeInTheDocument()
  })
})
