import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '../../test/render'
import { GlossaryPage } from './GlossaryPage'
import { GLOSSARY } from '../../lib/glossary'

describe('GlossaryPage', () => {
  it('lists every term the app can explain', () => {
    renderWithProviders(<GlossaryPage />, { route: '/glossary' })

    for (const term of Object.values(GLOSSARY)) {
      expect(screen.getByText(term.title)).toBeInTheDocument()
    }
  })

  it('anchors each entry so one definition can be linked to directly', () => {
    const { container } = renderWithProviders(<GlossaryPage />, { route: '/glossary' })

    expect(container.querySelector('#max_drawdown')).not.toBeNull()
  })

  it('scrolls a linked definition into view, which the browser cannot do for it', () => {
    // The hash is applied before this page renders, so without the effect `/glossary#sharpe`
    // silently lands at the top of the list.
    const scrolled: string[] = []
    window.location.hash = '#max_drawdown'
    Element.prototype.scrollIntoView = function scrollIntoView(this: Element) {
      scrolled.push(this.id)
    }

    renderWithProviders(<GlossaryPage />, { route: '/glossary' })

    expect(scrolled).toContain('max_drawdown')
    window.location.hash = ''
  })

  it('filters on the word the reader actually saw on screen', async () => {
    const user = userEvent.setup()
    renderWithProviders(<GlossaryPage />, { route: '/glossary' })

    await user.type(screen.getByPlaceholderText('Search terms'), 'drawdown')

    expect(screen.getByText('Max drawdown')).toBeInTheDocument()
    expect(screen.queryByText('Ticker')).not.toBeInTheDocument()
  })

  it('says so rather than showing an empty page when nothing matches', async () => {
    const user = userEvent.setup()
    renderWithProviders(<GlossaryPage />, { route: '/glossary' })

    await user.type(screen.getByPlaceholderText('Search terms'), 'zzzz')

    expect(screen.getByText(/Nothing matches/)).toBeInTheDocument()
  })
})
