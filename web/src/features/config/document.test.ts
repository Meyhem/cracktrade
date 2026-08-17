import { describe, expect, it } from 'vitest'
import { numberAt, putAt, readConfig, removeAt, setAt, stringAt } from './document'

/**
 * What an edit through the form must not cost.
 *
 * The draft is the user's own text. A form control that rewrites the file to change one number
 * has taken something away — a comment they wrote, the order they chose — in exchange for
 * nothing, and they will not find out until they open the YAML pane.
 */

const FILE = `# a strategy I am still thinking about
strategy:
  name: rsi_pullback
universe:
  ticker: NVDA          # the only one worth trading
  start_date: '2023-01-01'
execution:
  initial_capital: 10000.0
  commission_pct: 0.05
indicators:
  - name: sma_long
    type: sma
    window: 200
entry:
  signal: (close > sma_long) & (rsi_ind < 35)
exit:
  stop_loss_pct: 5
`

describe('editing a draft', () => {
  it('leaves comments and formatting it did not touch alone', () => {
    const edited = setAt(FILE, ['indicators', 0, 'window'], 150)

    expect(edited).toContain('# a strategy I am still thinking about')
    expect(edited).toContain('# the only one worth trading')
    // Untouched, so it keeps the `.0` that says the author wrote a float.
    expect(edited).toContain('initial_capital: 10000.0')
    expect(edited).toContain('window: 150')
  })

  it('creates the parents a nested write needs', () => {
    const edited = setAt(FILE, ['indicators', 0, 'optimize', 'window', 'min'], 100)
    const parsed = readConfig(setAt(edited, ['indicators', 0, 'optimize', 'window', 'max'], 300))

    expect(parsed.parsed).toBe(true)
    if (!parsed.parsed) return
    expect(numberAt(parsed.value, ['indicators', 0, 'optimize', 'window', 'min'])).toBe(100)
    expect(numberAt(parsed.value, ['indicators', 0, 'optimize', 'window', 'max'])).toBe(300)
  })

  it('removes a key rather than writing an empty one', () => {
    // Absent and blank are different to this schema: `exit` with no mechanism is refused,
    // `stop_loss_pct: null` is a type error, and neither is what clearing a field means.
    const edited = removeAt(FILE, ['exit', 'stop_loss_pct'])
    expect(edited).not.toContain('stop_loss_pct')
  })

  it('treats null as clearing the field', () => {
    expect(putAt(FILE, ['exit', 'stop_loss_pct'], null)).not.toContain('stop_loss_pct')
    expect(putAt(FILE, ['exit', 'stop_loss_pct'], 8)).toContain('stop_loss_pct: 8')
  })

  it('discards an edit against text that does not parse, rather than mangling it', () => {
    const broken = 'strategy:\n  name: [unclosed\n'
    expect(setAt(broken, ['strategy', 'name'], 'x')).toBe(broken)
  })
})

describe('reading a draft', () => {
  it('reports the line a syntax error is on', () => {
    const result = readConfig('strategy:\n  name: ok\n  - not a key\n')
    expect(result.parsed).toBe(false)
    if (result.parsed) return
    expect(result.line).not.toBeNull()
  })

  it('refuses a document that is not a mapping of sections', () => {
    expect(readConfig('- one\n- two\n').parsed).toBe(false)
  })

  it('does not coerce a number written as a string', () => {
    // Spec section 3.1: `"200"` is not `200`. An editor that read it as a number would show a
    // valid-looking form over a config the engine rejects.
    const parsed = readConfig(FILE.replace('window: 200', "window: '200'"))
    expect(parsed.parsed).toBe(true)
    if (!parsed.parsed) return
    expect(numberAt(parsed.value, ['indicators', 0, 'window'])).toBeNull()
    expect(stringAt(parsed.value, ['indicators', 0, 'window'])).toBe('200')
  })
})
