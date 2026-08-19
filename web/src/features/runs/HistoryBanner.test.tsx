import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'
import { HistoryBanner } from './HistoryBanner'
import { renderWithProviders } from '../../test/render'

/**
 * The one screen element standing between a user and this project's worst possible output.
 *
 * A thin result looks exactly like a sound one — same layout, same decimal places, same
 * confident percentages — until something says otherwise. These tests are about *when* it says
 * so and what it says, because a banner that renders on healthy runs stops being read, and one
 * that softens the engine's own sentence is a second opinion nobody asked for.
 */

const NOTE =
  'This result covers 37 sessions of 30m bars (625 bars), below the 60 that a backtest or ' +
  'optimization needs before its numbers mean much.'

describe('the limited-history banner', () => {
  it('renders the engine’s own note, verbatim', () => {
    // Not paraphrased here. The note already names what is thin, why it matters for this kind
    // of run, and the way out — which at 30m is not "widen the date range", because there is no
    // wider range to be had. Restating it in TypeScript is how those two drift apart.
    renderWithProviders(
      <HistoryBanner
        result={{
          history: { interval: '30m', sessions: 37, bars: 625, limited: true, note: NOTE },
        }}
      />,
    )

    expect(screen.getByText(NOTE)).toBeInTheDocument()
    expect(screen.getByText(/limited history/i)).toBeInTheDocument()
  })

  it('says nothing when the history is sufficient', () => {
    renderWithProviders(
      <HistoryBanner
        result={{
          history: { interval: '1h', sessions: 480, bars: 4320, limited: false, note: null },
        }}
      />,
    )

    expect(screen.queryByText(/limited history/i)).not.toBeInTheDocument()
  })

  it('says nothing for a run stored before the scope existed', () => {
    // Absent is not the same as "the history was fine", but it is also not evidence that it was
    // thin — and a banner with no note to show would be a warning with no content.
    renderWithProviders(<HistoryBanner result={{ metrics: { total_trades: 40 } }} />)

    expect(screen.queryByText(/limited history/i)).not.toBeInTheDocument()
  })
})
