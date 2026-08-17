/**
 * Two editor affordances for signal expressions.
 *
 * Neither decides whether an expression is valid — the engine does that, and a second grammar
 * living in the client would agree with it exactly until the day it did not. These produce an
 * *edit to offer* and a *hint to show*; both are then validated by the same round trip as
 * everything else the user types.
 */

/** Element-wise boolean operators. These bind more tightly than comparisons — the whole trap. */
const OPERATORS = new Set(['&', '|', '^'])

const COMPARISONS = ['<=', '>=', '==', '!=', '<', '>']

/**
 * Parenthesise each comparison in an expression, or `null` if none needed it.
 *
 * `close > sma & rsi < 30` parses in Python as `close > (sma & rsi) < 30`, which is not what
 * anybody means and is the single most common mistake in a hand-written signal (spec §7.2).
 * The engine catches it and explains it with a generic example; this turns the explanation
 * into the user's own corrected expression, which is the difference between reading advice and
 * pressing a button.
 *
 * The transform is deliberately local: split at the top-level boolean operators, and wrap any
 * resulting segment that contains a comparison of its own. A segment that is already
 * parenthesised holds its comparison one level down and is left alone. Nothing is reordered
 * and no precedence is reasoned about, because the point is to produce something the user can
 * read and recognise, not to be clever with their strategy.
 */
export function parenthesiseComparisons(expression: string): string | null {
  const parts = splitTopLevel(expression)
  if (parts === null || parts.segments.length < 2) return null

  let changed = false
  const wrapped = parts.segments.map((segment) => {
    const text = segment.trim()
    if (text.length === 0 || !hasTopLevelComparison(text)) return text
    changed = true
    return `(${text})`
  })
  if (!changed) return null

  return wrapped.reduce(
    (left, right, index) => `${left} ${parts.operators[index - 1] ?? '&'} ${right}`,
  )
}

/**
 * Numeric literals written into an expression.
 *
 * The `35` in `rsi_ind < 35` is unreachable by the optimizer: discovery walks the config's
 * numeric leaves, and this number is inside a string (spec §9.1). Users hit this constantly —
 * they pin every indicator, see "nothing to search", and cannot find the number they are
 * looking at. Naming the literals is how the editor says so before the run rather than after.
 */
export function numericLiterals(expression: string): string[] {
  const found = expression.match(/(?<![\w.])\d+(?:\.\d+)?/g)
  return found === null ? [] : [...new Set(found)]
}

/** Names an expression mentions, so the namespace chips can show which are in use. */
export function referencedNames(expression: string): string[] {
  const found = expression.match(/[A-Za-z_]\w*/g)
  return found === null ? [] : [...new Set(found)]
}

/**
 * Split at parenthesis depth zero, keeping the operator that separated each pair.
 *
 * `null` when the parentheses do not balance — an expression mid-edit, where any suggestion
 * would be built on a misreading.
 */
function splitTopLevel(expression: string): { segments: string[]; operators: string[] } | null {
  const segments: string[] = []
  const operators: string[] = []
  let depth = 0
  let start = 0

  for (let index = 0; index < expression.length; index += 1) {
    const character = expression[index] as string
    if (character === '(') depth += 1
    else if (character === ')') {
      depth -= 1
      if (depth < 0) return null
    } else if (depth === 0 && OPERATORS.has(character)) {
      segments.push(expression.slice(start, index))
      operators.push(character)
      start = index + 1
    }
  }
  if (depth !== 0) return null

  segments.push(expression.slice(start))
  return { segments, operators }
}

/** Whether a segment compares at its own top level, rather than inside parentheses. */
function hasTopLevelComparison(segment: string): boolean {
  let depth = 0
  for (let index = 0; index < segment.length; index += 1) {
    const character = segment[index] as string
    if (character === '(') depth += 1
    else if (character === ')') depth -= 1
    else if (depth === 0 && COMPARISONS.some((operator) => segment.startsWith(operator, index))) {
      return true
    }
  }
  return false
}
