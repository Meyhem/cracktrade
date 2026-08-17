import { afterEach, describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ConfigTab } from './ConfigTab'
import { resetDrafts } from './draft'
import { StrategyContext } from '../strategy/context'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/server'
import { strategyDetail } from '../../test/fixtures'
import type { Issue, ValidateResponse } from '../../api/types'

/**
 * The config editor.
 *
 * Most of these are about what the editor refuses to do: save on an answer it has not received,
 * decide validity for itself, or present a number as searchable when it is not. The editor is
 * the only screen where a user changes what will be run, so a control here that is confidently
 * wrong produces a run that is confidently wrong.
 */

const YAML = `# my notes
strategy:
  name: rsi_pullback
universe:
  ticker: NVDA
  start_date: '2023-01-01'
  end_date: '2025-12-31'
execution:
  initial_capital: 10000.0
  slippage_pct: 0.1
  commission_pct: 0.05
  risk_free_rate: 0.04
indicators:
  - name: sma_long
    type: sma
    window: 200
entry:
  signal: (close > sma_long) & (rsi_ind < 35)
exit:
  atr_stop_multiplier: 2.5
  stop_loss_pct: 5
`

function validation(overrides: Partial<ValidateResponse> = {}): ValidateResponse {
  return {
    valid: true,
    errors: [],
    warnings: [],
    canonical_yaml: YAML,
    config: {},
    namespace: ['open', 'high', 'low', 'close', 'volume', 'sma_long'],
    searchable_parameters: [
      { path: 'indicators.sma_long.window', value: 200, low: 100, high: 300 },
      { path: 'exit.stop_loss_pct', value: 5, low: 2.5, high: 7.5 },
      { path: 'exit.atr_stop_multiplier', value: 2.5, low: 1.25, high: 3.75 },
    ],
    ...overrides,
  }
}

function answering(response: ValidateResponse) {
  return http.post('*/api/v1/config/validate', () => HttpResponse.json(response))
}

function issue(path: string, message: string, line?: number): Issue {
  return { path, message, line: line ?? null, suggestion: null }
}

function open(yaml = YAML) {
  const strategy = strategyDetail({
    head: { ...strategyDetail().head, yaml },
  })
  return renderWithProviders(
    <StrategyContext value={{ strategy }}>
      <ConfigTab />
    </StrategyContext>,
  )
}

afterEach(() => {
  resetDrafts()
})

describe('the save control', () => {
  it('states why it is disabled rather than sitting dead', async () => {
    server.use(answering(validation()))
    open()

    const save = await screen.findByRole('button', { name: /Save as v4/ })
    expect(save).toBeDisabled()
    // Nothing has been typed, so there is no version to create — the brief's "a save that
    // changes nothing creates no version", said before the user presses anything.
    expect(await screen.findByText(/Nothing has changed yet/i)).toBeInTheDocument()
  })

  it('stays disabled while the configuration has errors', async () => {
    server.use(
      answering(
        validation({
          valid: false,
          errors: [issue('execution.initial_capital', 'Input should be greater than 0', 9)],
        }),
      ),
    )
    open()

    await screen.findAllByText(/Input should be greater than 0/)
    const user = userEvent.setup()
    await user.clear(await screen.findByLabelText('Ticker'))

    await waitFor(() => expect(screen.getByRole('button', { name: /Save as v4/ })).toBeDisabled())
    expect(await screen.findByText(/1 error to fix first/i)).toBeInTheDocument()
  })
})

describe('validation', () => {
  it('attaches an error to the field the server named', async () => {
    server.use(
      answering(
        validation({
          valid: false,
          errors: [issue('execution.initial_capital', 'Input should be greater than 0', 9)],
        }),
      ),
    )
    open()

    const field = document.getElementById('field-execution-initial_capital')
    await waitFor(() => expect(field).toHaveTextContent('Input should be greater than 0'))
  })

  it('collects every issue in a summary as well as on the field', async () => {
    server.use(
      answering(
        validation({
          valid: false,
          errors: [issue('exit', 'min_holding_days (15) must be less than max_holding_days (10)')],
        }),
      ),
    )
    open()

    expect(
      await screen.findByText(/Please fix the following issues in your strategy file/i),
    ).toBeInTheDocument()
  })

  it('renders a warning without blocking the save', async () => {
    server.use(
      answering(
        validation({
          warnings: [issue('exit.stop_loss_pct', 'stop_loss_pct is set but has no effect')],
        }),
      ),
    )
    open()

    expect(await screen.findByText(/never block a save or a run/i)).toBeInTheDocument()
    // Twice over, and deliberately: once on the field it is about and once in the summary.
    // Neither replaces the other — the summary is what a user scans, the attachment is what
    // makes it actionable.
    expect(screen.getAllByText(/stop_loss_pct is set but has no effect/)).toHaveLength(2)
  })

  it('hides the form when the draft is not YAML at all', async () => {
    server.use(answering(validation({ valid: false, errors: [issue('', 'not valid YAML')] })))
    open('strategy:\n  name: [unclosed\n')

    expect(await screen.findByText(/does not parse as YAML/i)).toBeInTheDocument()
    expect(screen.queryByLabelText('Ticker')).not.toBeInTheDocument()
  })
})

describe('the exit rule', () => {
  it('names the stop that is configured and does nothing', async () => {
    server.use(answering(validation()))
    open()

    // atr_stop_multiplier outranks stop_loss_pct, so the second is set and never fires. The
    // chain comes from `/meta`, not from a list of names repeated in the client.
    expect(await screen.findByText(/configured and will never fire/i)).toBeInTheDocument()
    expect(
      screen.getByText(/atr_stop_multiplier › trailing_stop_pct › stop_loss_pct/),
    ).toBeInTheDocument()
  })

  it('says that a stop is not suppressed by the minimum holding period', async () => {
    server.use(answering(validation()))
    open()
    expect(await screen.findByText(/Stops always fire/i)).toBeInTheDocument()
  })
})

describe('the search space', () => {
  it('shows the engine’s range, not one it computed', async () => {
    // 200 with an explicit 100–300 from the server. A client applying ±50% itself would agree
    // here and diverge on the cases the engine treats specially, such as a baseline of zero.
    server.use(
      answering(
        validation({
          searchable_parameters: [
            { path: 'indicators.sma_long.window', value: 200, low: 7, high: 999 },
          ],
        }),
      ),
    )
    open()

    expect(await screen.findByText('search 7.00–999.00')).toBeInTheDocument()
  })
})

describe('the signal editor', () => {
  it('names the literals an optimization cannot reach', async () => {
    server.use(answering(validation()))
    open()

    expect(
      await screen.findByText(
        /35 is written into this expression, so an optimization cannot reach/i,
      ),
    ).toBeInTheDocument()
  })

  it('offers the parenthesisation fix against the expression the user wrote', async () => {
    server.use(
      answering(
        validation({
          valid: false,
          errors: [
            issue('entry.signal', "invalid signal: '&' and '|' bind more tightly than comparisons"),
          ],
        }),
      ),
    )
    open(YAML.replace('(close > sma_long) & (rsi_ind < 35)', 'close > sma_long & rsi_ind < 35'))

    expect(await screen.findByText('(close > sma_long) & (rsi_ind < 35)')).toBeInTheDocument()

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Apply this' }))

    await waitFor(() =>
      expect(screen.getByLabelText('Signal')).toHaveValue('(close > sma_long) & (rsi_ind < 35)'),
    )
  })

  it('lists the names in scope, taken from the engine', async () => {
    server.use(answering(validation()))
    open()

    await screen.findAllByText('sma_long')
    expect(screen.getAllByText('volume').length).toBeGreaterThan(0)
  })
})

describe('an unsaved edit', () => {
  it('survives the tab unmounting and coming back', async () => {
    server.use(answering(validation()))
    const first = open()

    const user = userEvent.setup()
    const ticker = await screen.findByLabelText('Ticker')
    await user.clear(ticker)
    await user.type(ticker, 'MSFT')
    await waitFor(() => expect(screen.getByText('unsaved')).toBeInTheDocument())

    first.unmount()
    open()

    // The brief: ten minutes of editing must not evaporate because someone clicked a run.
    expect(await screen.findByLabelText('Ticker')).toHaveValue('MSFT')
    expect(screen.getByText('unsaved')).toBeInTheDocument()
  })

  it('can be discarded back to the head', async () => {
    server.use(answering(validation()))
    open()

    const user = userEvent.setup()
    const ticker = await screen.findByLabelText('Ticker')
    await user.clear(ticker)
    await user.type(ticker, 'MSFT')
    await screen.findByText('unsaved')

    await user.click(screen.getByRole('button', { name: 'Discard changes' }))

    await waitFor(() => expect(screen.getByLabelText('Ticker')).toHaveValue('NVDA'))
    expect(screen.queryByText('unsaved')).not.toBeInTheDocument()
  })
})
