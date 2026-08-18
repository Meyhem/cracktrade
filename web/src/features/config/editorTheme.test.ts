import { describe, expect, it } from 'vitest'
import { yaml } from '@codemirror/lang-yaml'
import { highlightTree } from '@lezer/highlight'
import { highlightFor } from './editorTheme'

/**
 * The syntax colours.
 *
 * This asserts the mapping, not the palette: a rule keyed on a tag the YAML grammar never emits
 * is silently inert, and the token then falls back to the body colour. That is invisible in
 * review and looks like a design choice on screen. It caught exactly that when written — rules
 * for `number`, `bool` and `null` that `@codemirror/lang-yaml` never triggers.
 */
const PROBE = [
  '# a comment',
  'execution:',
  '  initial_capital: 10000',
  '  commission_pct: 0.05',
  '  enabled: true',
  '  cap: null',
  '  ticker: "XLE"',
].join('\n')

function classesByText(scheme: 'light' | 'dark') {
  const tree = yaml().language.parser.parse(PROBE)
  const found = new Map<string, string>()
  highlightTree(tree, highlightFor(scheme), (from, to, classes) => {
    found.set(PROBE.slice(from, to), classes)
  })
  return found
}

describe('the YAML highlight style', () => {
  it.each(['light', 'dark'] as const)('colours what the grammar knows in %s mode', (scheme) => {
    const found = classesByText(scheme)

    for (const token of ['execution', 'initial_capital', 'enabled']) {
      expect(found.get(token), `key ${token}`).toBeTruthy()
    }
    expect(found.get('"XLE"'), 'quoted string').toBeTruthy()
    expect(found.get('# a comment'), 'comment').toBeTruthy()
    expect(found.get(':'), 'separator').toBeTruthy()
  })

  it('leaves unquoted scalars in the body colour', () => {
    // The grammar tags `10000`, `true` and `null` alike, as `content`. Colouring them as a
    // number or a boolean would be the editor asserting a distinction it cannot make. If a
    // grammar upgrade starts drawing one, this fails — which is the point: it is a decision to
    // take deliberately, not to inherit.
    const found = classesByText('dark')
    for (const scalar of ['10000', '0.05', 'true', 'null']) {
      expect(found.get(scalar), `scalar ${scalar}`).toBeUndefined()
    }
  })

  it('gives keys, strings and comments distinct classes', () => {
    const found = classesByText('dark')
    const distinct = new Set([found.get('execution'), found.get('"XLE"'), found.get('# a comment')])
    expect(distinct.size).toBe(3)
  })
})
